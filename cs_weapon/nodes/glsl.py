# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Evaluate a small GLSL subset into shader nodes.

The paint shader of the game is exported per static combo from the
user's own installation (tools/export_cs2_weapons.py) as decompiled GLSL.
This module runs such a ``main()`` symbolically on top of ``expr.Graph``:

- uniforms are replaced by the paint kit's values (constants, folded at
  build time) or by named Value nodes for live inputs (wear, seed
  transforms),
- branches on constants are resolved, branches on per-pixel values are
  evaluated on both sides and blended,
- loops must have constant trip counts (they are unrolled),
- ``texture()`` calls become Image Texture nodes through a callback.

Supported: scalar and vector types up to vec4, swizzles, component
assignment, constant arrays, if/else, for(;;) with break/continue,
ternaries and the usual built-ins. Anything else raises GLSLError, so an
unsupported shader fails loudly instead of rendering wrongly.
"""

import math
import re

from .expr import Val

TYPES = {"float": 1, "int": 1, "uint": 1, "bool": 1,
         "vec2": 2, "vec3": 3, "vec4": 4, "ivec2": 2, "ivec3": 3, "ivec4": 4,
         "uvec2": 2, "uvec3": 3, "uvec4": 4, "bvec2": 2, "bvec3": 3, "bvec4": 4,
         "mat4": 16}  # matrices: flat, column after column
SWIZZLE = {"x": 0, "y": 1, "z": 2, "w": 3, "r": 0, "g": 1, "b": 2, "a": 3}
MACROS = {"SPIRV_CROSS_BRANCH", "SPIRV_CROSS_FLATTEN", "SPIRV_CROSS_UNROLL", "SPIRV_CROSS_LOOP"}
MAX_LOOP = 256


class GLSLError(Exception):
    pass


# ---------------------------------------------------------------------------
# Lexer and parser
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"""
    (?P<ws>\s+|//[^\n]*|/\*.*?\*/)
  | (?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[uUfF]?)
  | (?P<id>[A-Za-z_]\w*)
  | (?P<op>\+\+|--|\+=|-=|\*=|/=|==|!=|<=|>=|&&|\|\||[-+*/<>=!?:;,.(){}\[\]])
""", re.S | re.X)


def tokenize(src):
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise GLSLError(f"unexpected {src[pos:pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        tok = m.group(kind)
        if kind == "id" and tok in MACROS:
            continue
        if kind == "num":
            tok = tok.rstrip("uUfF")
            out.append(("num", float(tok)))
        else:
            out.append((kind, tok))
    out.append(("eof", None))
    return out


class Parser:
    """Recursive descent into tuples: expressions ('num', v), ('id', name),
    ('call', name, args), ('member', expr, swz), ('index', expr, expr),
    ('un', op, e), ('bin', op, a, b), ('tern', c, a, b); statements
    ('decl', type, name, expr|None, size|None), ('assign', op, lhs, expr),
    ('expr', e), ('if', c, then, else), ('loop', init, cond, step, body),
    ('break',), ('continue',), ('return',), ('block', [stmts])."""

    BIN = [("||",), ("&&",), ("==", "!="), ("<", ">", "<=", ">="), ("+", "-"), ("*", "/")]

    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self, k=0):
        return self.t[self.i + k]

    def take(self, val=None):
        tok = self.t[self.i]
        if val is not None and tok[1] != val:
            raise GLSLError(f"expected {val!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def accept(self, val):
        if self.t[self.i][1] == val:
            self.i += 1
            return True
        return False

    # expressions ----------------------------------------------------------
    def expr(self):
        return self.ternary()

    def ternary(self):
        c = self.binary(0)
        if self.accept("?"):
            a = self.expr()
            self.take(":")
            b = self.expr()
            return ("tern", c, a, b)
        return c

    def binary(self, level):
        if level == len(self.BIN):
            return self.unary()
        left = self.binary(level + 1)
        while self.peek()[0] == "op" and self.peek()[1] in self.BIN[level]:
            op = self.take()[1]
            left = ("bin", op, left, self.binary(level + 1))
        return left

    def unary(self):
        if self.peek()[1] in ("-", "!", "+"):
            op = self.take()[1]
            return ("un", op, self.unary())
        return self.postfix(self.primary())

    def primary(self):
        kind, tok = self.take()
        if kind == "num":
            return ("num", tok)
        if tok == "(":
            e = self.expr()
            self.take(")")
            return e
        if kind == "id":
            if tok in ("true", "false"):
                return ("num", 1.0 if tok == "true" else 0.0)
            if self.peek()[1] == "[" and self.peek(1)[1] == "]":  # vec2[](...)
                self.take("[")
                self.take("]")
                return ("call", "array", self.args())
            if self.peek()[1] == "(":
                return ("call", tok, self.args())
            return ("id", tok)
        raise GLSLError(f"unexpected token {tok!r}")

    def args(self):
        self.take("(")
        out = []
        if not self.accept(")"):
            while True:
                out.append(self.expr())
                if self.accept(")"):
                    break
                self.take(",")
        return out

    def postfix(self, e):
        while True:
            if self.accept("."):
                e = ("member", e, self.take()[1])
            elif self.accept("["):
                idx = self.expr()
                self.take("]")
                e = ("index", e, idx)
            else:
                return e

    # statements -----------------------------------------------------------
    def block(self):
        self.take("{")
        out = []
        while not self.accept("}"):
            out.append(self.statement())
        return ("block", out)

    def body(self):
        return self.block() if self.peek()[1] == "{" else ("block", [self.statement()])

    def statement(self):
        kind, tok = self.peek()
        if tok == "{":
            return self.block()
        if tok == ";":
            self.take()
            return ("block", [])
        if tok == "if":
            self.take()
            self.take("(")
            c = self.expr()
            self.take(")")
            then = self.body()
            other = self.body() if self.accept("else") else ("block", [])
            return ("if", c, then, other)
        if tok == "for":
            self.take()
            self.take("(")
            init = None if self.peek()[1] == ";" else self.simple()
            self.take(";")
            cond = None if self.peek()[1] == ";" else self.expr()
            self.take(";")
            step = None if self.peek()[1] == ")" else self.simple()
            self.take(")")
            return ("loop", init, cond, step, self.body())
        if tok == "do":
            self.take()
            body = self.body()
            self.take("while")
            self.take("(")
            cond = self.expr()
            self.take(")")
            self.take(";")
            return ("dowhile", body, cond)
        if tok == "switch":
            return self.switch()
        if tok in ("break", "continue", "return"):
            self.take()
            if tok == "return" and self.peek()[1] != ";":
                raise GLSLError("return with a value is not supported")
            self.take(";")
            return (tok,)
        s = self.simple()
        self.take(";")
        return s

    def switch(self):
        """switch -> if/else chain. Every case group must end with break
        (no fall-through), as spirv-cross writes them."""
        self.take("switch")
        self.take("(")
        sel = self.expr()
        self.take(")")
        self.take("{")
        groups, labels = [], []
        while not self.accept("}"):
            if self.accept("case"):
                labels.append(self.expr())
                self.take(":")
            elif self.accept("default"):
                self.take(":")
                labels.append(None)
            else:
                if not labels:
                    raise GLSLError("statement before the first case")
                stmts = []
                while self.peek()[1] not in ("case", "default", "}"):
                    stmts.append(self.statement())
                groups.append((labels, stmts))
                labels = []
        if labels:
            groups.append((labels, []))
        default = ("block", [])
        cases = []
        for i, (labs, stmts) in enumerate(groups):
            body = _strip_break(stmts, last=i == len(groups) - 1)
            if None in labs:
                default = body
            else:
                cases.append((labs, body))
        out = default
        for labs, body in reversed(cases):
            cond = None
            for lab in labs:
                c = ("bin", "==", sel, lab)
                cond = c if cond is None else ("bin", "||", cond, c)
            out = ("if", cond, body, out)
        return out

    def simple(self):
        kind, tok = self.peek()
        if kind == "id" and tok == "const":
            self.take()
            kind, tok = self.peek()
        if kind == "id" and tok in TYPES and self.peek(1)[0] == "id":
            self.take()
            name = self.take()[1]
            dims = []
            while self.accept("["):
                dims.append(int(self.take()[1]))
                self.take("]")
            size = None if not dims else dims[0] if len(dims) == 1 else tuple(dims)
            init = self.expr() if self.accept("=") else None
            return ("decl", tok, name, init, size)
        lhs = self.expr()
        if self.peek()[1] in ("=", "+=", "-=", "*=", "/="):
            op = self.take()[1]
            return ("assign", op, lhs, self.expr())
        if self.peek()[1] in ("++", "--"):
            op = self.take()[1]
            return ("assign", "+=" if op == "++" else "-=", lhs, ("num", 1.0))
        return ("expr", lhs)


def _strip_break(stmts, last):
    """Body of a case group without its closing break."""
    stmts = list(stmts)
    while len(stmts) == 1 and stmts[0][0] == "block":
        stmts = list(stmts[0][1])
    if stmts and stmts[-1] == ("break",):
        stmts.pop()
    elif not last and not (stmts and stmts[-1][0] == "return"):
        raise GLSLError("switch fall-through is not supported")
    if any(_has_break(s) for s in stmts):
        raise GLSLError("break inside a switch case")
    return ("block", stmts)


def _has_break(s):
    if s == ("break",):
        return True
    if s[0] == "block":
        return any(_has_break(x) for x in s[1])
    if s[0] == "if":
        return _has_break(s[2]) or _has_break(s[3])
    return False  # breaks inside loops belong to the loop


def parse_main(src):
    """Statements of ``void main()`` plus the top-level constants."""
    consts = []
    for m in re.finditer(r"^const .*?;$", src, re.M):
        consts.append(Parser(tokenize(m.group(0))).simple())
    i = src.find("void main()")
    if i < 0:
        raise GLSLError("no main()")
    p = Parser(tokenize(src[i + len("void main()"):]))
    return consts, p.block()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    pass


class Machine:
    """Runs parsed GLSL on a Graph. ``uniforms``: name -> number/sequence
    (constant) or list of Val (live). ``inputs``: varying name -> list of
    Val. ``sample(name, uv_vec)`` -> (rgb Val, alpha Val, (lo, hi) range or
    None)."""

    def __init__(self, g, uniforms, inputs, sample, size=None):
        self.g = g
        self.size = size  # texture name -> (width, height)
        self.uniforms = uniforms
        self.inputs = inputs
        self.sample = sample
        self.ranges = {}

    # values are lists of Val -------------------------------------------------
    def w(self, x):
        return self.g.wrap(x)

    def const(self, v):
        if isinstance(v, (int, float)):
            return [self.w(float(v))]
        return [self.w(float(c)) for c in v]

    def lookup(self, env, name):
        if name in env:
            return env[name]
        if name in self.inputs:
            return self.inputs[name]
        if name in self.uniforms:
            u = self.uniforms[name]
            if isinstance(u, list) and u and isinstance(u[0], Val):
                return u
            return self.const(u)
        raise GLSLError(f"unknown name {name}")

    # expressions ----------------------------------------------------------
    def ev(self, e, env):
        kind = e[0]
        if kind == "num":
            return [self.w(e[1])]
        if kind == "id":
            return self.lookup(env, e[1])
        if kind == "member":
            base = self.ev(e[1], env)
            try:
                return [base[SWIZZLE[c]] for c in e[2]]
            except (KeyError, IndexError):
                raise GLSLError(f"bad swizzle .{e[2]}") from None
        if kind == "index":
            i = self._index(e[2], env)
            base = self._array(e[1], env)
            if isinstance(base, tuple):
                return base[i]
            base = self.ev(e[1], env)
            if len(base) == 16:  # column of a matrix
                return base[4 * i:4 * i + 4]
            if len(base) <= 4:
                return [base[i]]
            raise GLSLError("index on an expression")
        if kind == "un":
            v = self.ev(e[2], env)
            if e[1] == "-":
                return [self.g.op("MULTIPLY", c, -1.0) for c in v]
            if e[1] == "!":
                return [self.g.op("SUBTRACT", 1.0, c) for c in v]
            return v
        if kind == "bin":
            return self.binop(e[1], self.ev(e[2], env), self.ev(e[3], env))
        if kind == "tern":
            c = self.ev(e[1], env)[0]
            if c.is_const:
                return self.ev(e[2] if c.const else e[3], env)
            a, b = self.ev(e[2], env), self.ev(e[3], env)
            return [self.g.mix(y, x, c) for x, y in zip(a, b)]
        if kind == "call":
            return self.call(e[1], e[2], env)
        raise GLSLError(f"cannot evaluate {kind}")

    def _index(self, e, env):
        idx = self.ev(e, env)[0]
        if not idx.is_const:
            raise GLSLError("dynamic array index")
        return int(idx.const)

    def _array(self, e, env):
        """The array (tuple) an expression names, or None."""
        if e[0] == "id":
            v = env.get(e[1])
            return v if isinstance(v, tuple) else None
        if e[0] == "index":
            base = self._array(e[1], env)
            if base is not None:
                return base[self._index(e[2], env)]
        return None

    def _zeros(self, typ, size):
        if size is None:
            return self.const([0.0] * TYPES[typ])
        dims = size if isinstance(size, tuple) else (size,)
        if len(dims) == 1:
            return tuple(self.const([0.0] * TYPES[typ]) for _ in range(dims[0]))
        return tuple(self._zeros(typ, dims[1:]) for _ in range(dims[0]))

    def ev_array(self, e, env):
        if e[0] != "id":
            raise GLSLError("array expression")
        arr = env.get(e[1])
        if not isinstance(arr, tuple):
            raise GLSLError(f"{e[1]} is not an array")
        return arr

    def _pair(self, a, b):
        if len(a) == len(b):
            return a, b
        if len(a) == 1:
            return a * len(b), b
        if len(b) == 1:
            return a, b * len(a)
        raise GLSLError(f"size mismatch {len(a)} vs {len(b)}")

    def compare(self, op, x, y):
        g = self.g
        rx, ry = self.range_of(x), self.range_of(y)
        if rx and ry:  # decide from known value ranges (texture samples)
            lo_x, hi_x = rx
            lo_y, hi_y = ry
            decided = None
            if op == "<":
                decided = 1.0 if hi_x < lo_y else 0.0 if lo_x >= hi_y else None
            elif op == "<=":
                decided = 1.0 if hi_x <= lo_y else 0.0 if lo_x > hi_y else None
            elif op == ">":
                decided = 1.0 if lo_x > hi_y else 0.0 if hi_x <= lo_y else None
            elif op == ">=":
                decided = 1.0 if lo_x >= hi_y else 0.0 if hi_x < lo_y else None
            if decided is not None:
                return self.w(decided)
        if op == "<":
            return g.op("LESS_THAN", x, y)
        if op == ">":
            return g.op("GREATER_THAN", x, y)
        if op == "<=":
            return g.op("SUBTRACT", 1.0, g.op("GREATER_THAN", x, y))
        if op == ">=":
            return g.op("SUBTRACT", 1.0, g.op("LESS_THAN", x, y))
        if x.is_const and y.is_const:
            eq = 1.0 if x.const == y.const else 0.0
            return self.w(eq if op == "==" else 1.0 - eq)
        d = g.abs(g.op("SUBTRACT", x, y))
        eq = g.op("LESS_THAN", d, 1e-6)
        return eq if op == "==" else g.op("SUBTRACT", 1.0, eq)

    def range_of(self, v):
        if v.is_const:
            return (v.const, v.const)
        return self.ranges.get(v.key)

    def binop(self, op, a, b):
        g = self.g
        if op == "*" and (len(a), len(b)) in ((4, 16), (16, 4)):
            return self.mat_mul(a, b)
        a, b = self._pair(a, b)
        if op in ("+", "-", "*", "/"):
            name = {"+": "ADD", "-": "SUBTRACT", "*": "MULTIPLY", "/": "DIVIDE"}[op]
            return [g.op(name, x, y) for x, y in zip(a, b)]
        if op in ("<", ">", "<=", ">=", "==", "!="):
            return [self.compare(op, x, y) for x, y in zip(a, b)]
        if op == "&&":
            return [g.op("MULTIPLY", x, y) for x, y in zip(a, b)]
        if op == "||":
            return [g.op("MAXIMUM", x, y) for x, y in zip(a, b)]
        raise GLSLError(f"operator {op}")

    def _sum(self, terms):
        out = terms[0]
        for t in terms[1:]:
            out = self.g.op("ADD", out, t)
        return out

    def mat_mul(self, a, b):
        """vec4 * mat4 (row vector: dot with each column) or mat4 * vec4."""
        g = self.g
        if len(a) == 4:
            return [self._sum([g.op("MULTIPLY", a[i], b[4 * j + i]) for i in range(4)]) for j in range(4)]
        return [self._sum([g.op("MULTIPLY", a[4 * j + i], b[j]) for j in range(4)]) for i in range(4)]

    def call(self, name, args, env):
        g = self.g
        if name in ("texture", "textureGrad", "textureLod"):  # no mip control in nodes
            if args[0][0] != "id":
                raise GLSLError("texture() on an expression")
            uv = self.ev(args[1], env)
            if len(uv) < 2:
                raise GLSLError("texture() needs vec2 coordinates")
            vec = g.vec(uv[0], g.op("SUBTRACT", 1.0, uv[1]), 0.0)  # source v runs downwards
            rgb, alpha, rng = self.sample(args[0][1], vec)
            comps = list(g.sep(rgb)) + [alpha]
            if rng is not None:
                for c in comps:
                    if not c.is_const:
                        self.ranges[c.key] = rng
            return comps
        if name == "textureSize":
            if args[0][0] != "id" or self.size is None:
                raise GLSLError("textureSize() without texture sizes")
            w, h = self.size(args[0][1])
            return [g.wrap(float(w)), g.wrap(float(h))]
        vals = [self.ev(a, env) for a in args]
        if name in ("dFdx", "dFdy", "fwidth", "dFdxFine", "dFdyFine", "dFdxCoarse", "dFdyCoarse"):
            # screen-space derivatives of the composite pass (texel edges):
            # no per-pixel neighbours in a node tree, taken as 0
            return [g.wrap(0.0) for _ in vals[0]]
        if name in TYPES:
            flat = [c for v in vals for c in v]
            n = TYPES[name]
            if n == 16 and len(flat) != 16:
                raise GLSLError(f"{name}() with {len(flat)} components")
            if len(flat) == 1:
                flat = flat * n
            if len(flat) < n:
                raise GLSLError(f"{name}() with {len(flat)} components")
            flat = flat[:n]
            if name in ("int", "uint", "ivec2", "ivec3", "ivec4", "uvec2", "uvec3", "uvec4"):
                flat = [c if c.is_const and float(c.const).is_integer() else
                        g.wrap(float(int(c.const))) if c.is_const else g.op("TRUNC", c) for c in flat]
            return flat
        if name == "array":
            return vals
        comp = self._componentwise(name, vals)
        if comp is not None:
            return comp
        if name == "dot":
            a, b = self._pair(vals[0], vals[1])
            out = g.op("MULTIPLY", a[0], b[0])
            for x, y in zip(a[1:], b[1:]):
                out = g.op("ADD", out, g.op("MULTIPLY", x, y))
            return [out]
        if name == "length":
            return [g.sqrt(self.call_v("dot", [vals[0], vals[0]])[0])]
        if name == "normalize":
            inv = g.op("DIVIDE", 1.0, self.call_v("length", [vals[0]])[0])
            return [g.op("MULTIPLY", c, inv) for c in vals[0]]
        if name == "cross":
            a, b = vals
            return [g.op("SUBTRACT", g.op("MULTIPLY", a[1], b[2]), g.op("MULTIPLY", a[2], b[1])),
                    g.op("SUBTRACT", g.op("MULTIPLY", a[2], b[0]), g.op("MULTIPLY", a[0], b[2])),
                    g.op("SUBTRACT", g.op("MULTIPLY", a[0], b[1]), g.op("MULTIPLY", a[1], b[0]))]
        raise GLSLError(f"unsupported function {name}()")

    def call_v(self, name, vals):
        """call() on already evaluated arguments."""
        consts = [("v%d" % i, v) for i, v in enumerate(vals)]
        env = dict(consts)
        return self.call(name, [("id", k) for k, _ in consts], env)

    def _componentwise(self, name, vals):
        g = self.g
        one = {"abs": "ABSOLUTE", "floor": "FLOOR", "fract": "FRACTION", "sqrt": "SQRT",
               "sin": "SINE", "cos": "COSINE", "exp": "EXPONENT", "sign": "SIGN",
               "trunc": "TRUNC", "round": "ROUND", "inversesqrt": "INVERSE_SQRT", "ceil": "CEIL"}
        if name in one:
            return [g.op(one[name], c) for c in vals[0]]
        if name in ("exp2", "log2"):
            if name == "exp2":
                return [g.op("POWER", 2.0, c) for c in vals[0]]
            return [g.op("LOGARITHM", c, 2.0) for c in vals[0]]
        if name in ("min", "max", "pow", "mod", "step"):
            a, b = self._pair(vals[0], vals[1])
            if name == "step":
                return [g.step(x, y) for x, y in zip(a, b)]
            if name == "mod":
                return [g.op("SUBTRACT", x, g.op("MULTIPLY", y, g.floor(g.op("DIVIDE", x, y))))
                        for x, y in zip(a, b)]
            op = {"min": "MINIMUM", "max": "MAXIMUM", "pow": "POWER"}[name]
            return [g.op(op, x, y) for x, y in zip(a, b)]
        if name in ("clamp", "mix", "smoothstep", "fma"):
            n = max(len(v) for v in vals)
            vs = [v * n if len(v) == 1 else v for v in vals]
            if name == "clamp":
                return [g.clamp(x, lo.const, hi.const) if lo.is_const and hi.is_const
                        else g.max(g.min(x, hi), lo) for x, lo, hi in zip(*vs)]
            if name == "mix":
                return [g.mix(a, b, t) for a, b, t in zip(*vs)]
            if name == "fma":
                return [g.op("ADD", g.op("MULTIPLY", a, b), c) for a, b, c in zip(*vs)]
            return [g.smoothstep(a, b, x) for a, b, x in zip(*vs)]
        return None

    # statements -----------------------------------------------------------
    def run(self, consts, main):
        env = {}
        for decl in consts:
            _, typ, name, init, size = decl
            if size is None:  # plain constant (spirv-cross' undefined values)
                v = self.ev(init, env)
                env[name] = v * TYPES[typ] if len(v) == 1 and TYPES[typ] > 1 else v
            else:
                env[name] = tuple(self.ev(e, env) for e in init[2])
        try:
            self.exec(main, env)
        except _Return:
            pass
        return env

    def exec(self, s, env):
        kind = s[0]
        if kind == "block":
            for st in s[1]:
                self.exec(st, env)
        elif kind == "decl":
            _, typ, name, init, size = s
            if size is not None and init is not None and init[0] == "call" and init[1] == "array":
                env[name] = tuple(self.ev(e, env) for e in init[2])
            elif size is not None or init is None:
                env[name] = self._zeros(typ, size)
            else:
                v = self.ev(init, env)
                if len(v) == 1 and TYPES[typ] > 1:
                    v = v * TYPES[typ]
                env[name] = v
        elif kind == "assign":
            self.assign(s[1], s[2], s[3], env)
        elif kind == "expr":
            self.ev(s[1], env)
        elif kind == "if":
            c = self.ev(s[1], env)[0]
            if c.is_const:
                self.exec(s[2] if c.const else s[3], env)
            else:
                self.branch(c, s[2], s[3], env)
        elif kind == "loop":
            self.loop(s, env)
        elif kind == "dowhile" and s[2] == ("num", 0.0):
            try:
                self.seq(self._stmts(s[1]), env)
            except (_Break, _Continue):
                pass
        elif kind == "dowhile":
            for _ in range(MAX_LOOP):
                try:
                    self.exec(s[1], env)
                except _Break:
                    break
                except _Continue:
                    pass
                c = self.ev(s[2], env)[0]
                if not c.is_const:
                    raise GLSLError("do-while on a per-pixel condition")
                if not c.const:
                    break
        elif kind == "break":
            raise _Break()
        elif kind == "continue":
            raise _Continue()
        elif kind == "return":
            raise _Return()
        else:
            raise GLSLError(f"statement {kind}")

    def assign(self, op, lhs, rhs, env):
        v = self.ev(rhs, env)
        if op != "=":
            v = self.binop(op[0], self.ev(lhs, env), v)
        if lhs[0] == "id":
            name = lhs[1]
            old = env.get(name)
            if old is not None and not isinstance(old, tuple) and len(v) == 1 and len(old) > 1:
                v = v * len(old)
            env[name] = v
        elif lhs[0] == "member" and lhs[1][0] == "id":
            name = lhs[1][1]
            cur = list(self.lookup(env, name))
            if len(v) == 1:
                v = v * len(lhs[2])
            for c, val in zip(lhs[2], v):
                cur[SWIZZLE[c]] = val
            env[name] = cur
        elif lhs[0] == "index":
            path, root = [], lhs
            while root[0] == "index":
                path.insert(0, self._index(root[2], env))
                root = root[1]
            if root[0] != "id" or not isinstance(env.get(root[1]), tuple):
                raise GLSLError("unsupported assignment target")
            env[root[1]] = self._set_in(env[root[1]], path, v)
        else:
            raise GLSLError("unsupported assignment target")

    @staticmethod
    def _set_in(arr, path, v):
        arr = list(arr)
        i = path[0]
        if len(path) > 1:
            arr[i] = Machine._set_in(arr[i], path[1:], v)
        else:
            if len(v) == 1 and len(arr[i]) > 1:
                v = v * len(arr[i])
            arr[i] = v
        return tuple(arr)

    @staticmethod
    def _trailing_break(body):
        """(body without a final ``break``, True) or (body, False)."""
        if body is not None and body[0] == "break":
            return ("block", []), True
        if body is not None and body[0] == "block" and body[1] and body[1][-1][0] == "break":
            return ("block", body[1][:-1]), True
        return body, False

    def branch(self, c, then, other, env):
        """Both sides of a per-pixel branch, merged with mix(). If both end
        with ``break`` (spirv-cross' do { if .. break; else .. break; }
        while (false)), the break happens after the merge."""
        then2, b1 = self._trailing_break(then)
        other2, b2 = self._trailing_break(other)
        if b1 and b2:
            self._branch(c, then2, other2, env)
            raise _Break()
        self._branch(c, then, other, env)

    def _branch(self, c, then, other, env):
        e1, e2 = dict(env), dict(env)
        for e, body in ((e1, then), (e2, other)):
            if body is None:
                continue
            try:
                self.exec(body, e)
            except (_Break, _Continue, _Return):
                raise GLSLError("control flow inside a per-pixel branch") from None
        self._join(env, e1, e2, c)

    @staticmethod
    def _stmts(body):
        if body is None:
            return []
        return list(body[1]) if body[0] == "block" else [body]

    def seq(self, stmts, env):
        """The body of spirv-cross' ``do { .. } while (false)``: there
        ``break`` and ``continue`` jump to the end. A per-pixel ``if`` runs
        each side followed by the rest of the body, and the two
        environments are merged, so a break on one side only skips the
        rest there."""
        stmts = list(stmts)
        while stmts:
            s = stmts.pop(0)
            kind = s[0]
            if kind in ("break", "continue"):
                return
            if kind == "block":
                stmts[:0] = s[1]
                continue
            if kind == "if":
                c = self.ev(s[1], env)[0]
                if c.is_const:
                    stmts[:0] = self._stmts(s[2] if c.const else s[3])
                    continue
                if self._jumps(s[2]) or self._jumps(s[3]):
                    e1, e2 = dict(env), dict(env)
                    self.seq(self._stmts(s[2]) + stmts, e1)
                    self.seq(self._stmts(s[3]) + stmts, e2)
                    self._join(env, e1, e2, c)
                    return
            self.exec(s, env)

    @classmethod
    def _jumps(cls, s):
        """True if s has a break or continue outside a nested loop."""
        if s is None:
            return False
        if s[0] in ("break", "continue"):
            return True
        if s[0] == "block":
            return any(cls._jumps(x) for x in s[1])
        if s[0] == "if":
            return cls._jumps(s[2]) or cls._jumps(s[3])
        return False

    def _join(self, env, e1, e2, c):
        for name in set(e1) | set(e2):
            a, b = e1.get(name), e2.get(name)
            if a is None or b is None:
                env[name] = a if b is None else b  # declared in one branch only
            else:
                env[name] = self._merge(a, b, c)

    def _merge(self, a, b, c):
        """a where c, else b; arrays element by element."""
        if a is b:
            return a
        if isinstance(a, tuple):
            return tuple(self._merge(x, y, c) for x, y in zip(a, b))
        return [x if x is y else self.g.mix(y, x, c) for x, y in zip(a, b)]

    def loop(self, s, env):
        _, init, cond, step, body = s
        if init is not None:
            self.exec(init, env)
        for _ in range(MAX_LOOP):
            if cond is not None:
                c = self.ev(cond, env)[0]
                if not c.is_const:
                    raise GLSLError("loop on a per-pixel condition")
                if not c.const:
                    return
            try:
                self.exec(body, env)
            except _Break:
                return
            except _Continue:
                pass
            if step is not None:
                self.exec(step, env)
        raise GLSLError("loop does not terminate")


def header(src):
    """Metadata comment lines written by the exporter: textures (name ->
    sRGB flag), inputs (location -> name), outputs and combo values."""
    meta = {"textures": {}, "inputs": {}, "outputs": {}, "combos": {}}
    for line in src.splitlines():
        if not line.startswith("//"):
            if line.strip():
                break
            continue
        parts = line[2:].split()
        if not parts:
            continue
        if parts[0] == "texture" and len(parts) >= 3:
            meta["textures"][parts[1]] = parts[2].endswith("=1")
        elif parts[0] == "input" and len(parts) >= 3:
            meta["inputs"][int(parts[1])] = parts[2]
        elif parts[0] == "output" and len(parts) >= 3:
            meta["outputs"][int(parts[1])] = parts[2]
        elif parts[0] == "combos":
            for kv in parts[1:]:
                k, _, v = kv.partition("=")
                meta["combos"][k] = int(v)
    return meta


def finite(x):
    return x if math.isfinite(x) else 0.0
