# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Write shader node graphs as Python expressions.

    g = Graph(material.node_tree)
    w = g.value("cs_wear", 0.1)
    mask = g.smoothstep(0.58, 0.68, edge * (w * 6.0 + 1.0))
    color = g.mix(paint, vanilla, mask)
    g.feed(bsdf.inputs["Base Color"], color)

Values are scalars (``kind == "f"``) or 3D vectors (``"v"``). Operations
on Python constants are folded at build time, so parameters that are fixed
per paint kit never become nodes; only texture samples and the live
inputs (wear, seed transforms) do.

Computed values are recorded first and become nodes only when they are
fed into a real socket (``feed``): parts of a shader program that no
output uses never become nodes, and structurally equal expressions share
one node (the passes of a composite shader repeat most of their work).
This matters because Blender's cost per added node and link grows with
the size of the tree. Nodes are placed in columns by their depth.

That cost is quadratic: every added node, link or property change walks
the whole tree (2000 nodes in one tree take about 10 s, eight trees of 250
under a second). A deferred graph (``defer=True``) therefore collects its
feeds and creates the recorded nodes in ``flush()``: small graphs inline,
large ones split into nested groups of ``CHUNK`` nodes each, wired through
group sockets. Live Value nodes stay in the outer tree.
"""

import math

_VEC_OPS = {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "MINIMUM", "MAXIMUM",
            "FLOOR", "FRACTION", "ABSOLUTE"}


def _is_const(x):
    return isinstance(x, (int, float, tuple))


class _Pending:
    """A recorded node: type, properties, inputs (socket key -> Val)."""

    __slots__ = ("kind", "props", "inputs", "depth", "real", "ext")

    def __init__(self, kind, props, inputs, depth):
        self.kind, self.props, self.inputs, self.depth = kind, props, inputs, depth
        self.real = None  # the node, when created in the graph's own tree
        self.ext = {}     # output -> socket in the own tree, when created in a chunk


class Val:
    """A scalar or vector: a Python constant, a node output socket or the
    output of a recorded node."""

    __slots__ = ("g", "kind", "const", "socket", "depth", "pend", "out")

    def __init__(self, g, kind, const=None, socket=None, depth=0, pend=None, out=0):
        self.g, self.kind, self.const, self.socket, self.depth = g, kind, const, socket, depth
        self.pend, self.out = pend, out

    @property
    def is_const(self):
        return self.socket is None and self.pend is None

    @property
    def key(self):
        """Hashable identity (equal for structurally equal values)."""
        if self.socket is not None:
            return ("s", self.socket.as_pointer())
        if self.pend is not None:
            return ("p", id(self.pend), self.out)
        return ("c", self.const)

    # arithmetic -----------------------------------------------------------
    def __add__(self, o): return self.g.op("ADD", self, o)
    def __radd__(self, o): return self.g.op("ADD", o, self)
    def __sub__(self, o): return self.g.op("SUBTRACT", self, o)
    def __rsub__(self, o): return self.g.op("SUBTRACT", o, self)
    def __mul__(self, o): return self.g.op("MULTIPLY", self, o)
    def __rmul__(self, o): return self.g.op("MULTIPLY", o, self)
    def __truediv__(self, o): return self.g.op("DIVIDE", self, o)
    def __rtruediv__(self, o): return self.g.op("DIVIDE", o, self)
    def __neg__(self): return self.g.op("MULTIPLY", self, -1.0)
    def __pow__(self, o): return self.g.pow(self, o)

    # components -----------------------------------------------------------
    @property
    def x(self): return self.g.sep(self)[0]
    @property
    def y(self): return self.g.sep(self)[1]
    @property
    def z(self): return self.g.sep(self)[2]


def _fold(op, a, b):
    if op == "ADD": return a + b
    if op == "SUBTRACT": return a - b
    if op == "MULTIPLY": return a * b
    if op == "DIVIDE": return a / b if b != 0 else 0.0
    if op == "MINIMUM": return min(a, b)
    if op == "MAXIMUM": return max(a, b)
    if op == "POWER": return a ** b if a > 0 or float(b).is_integer() else 0.0
    if op == "FLOOR": return math.floor(a)
    if op in ("FRACTION", "FRACT"): return a - math.floor(a)
    if op == "ABSOLUTE": return abs(a)
    if op == "SQRT": return math.sqrt(a) if a > 0 else 0.0
    if op == "GREATER_THAN": return 1.0 if a > b else 0.0
    if op == "LESS_THAN": return 1.0 if a < b else 0.0
    if op == "LOGARITHM": return math.log(a, b) if a > 0 and b > 0 and b != 1 else 0.0
    if op == "SINE": return math.sin(a)
    if op == "COSINE": return math.cos(a)
    if op == "EXPONENT": return math.exp(a)
    if op == "SIGN": return (a > 0) - (a < 0)
    if op == "TRUNC": return float(math.trunc(a))
    if op == "ROUND": return float(math.floor(a + 0.5))
    if op == "CEIL": return float(math.ceil(a))
    if op == "INVERSE_SQRT": return 1.0 / math.sqrt(a) if a > 0 else 0.0
    raise ValueError(op)


def _prop_key(v):
    if isinstance(v, (str, int, float, bool, type(None))):
        return v
    return ("id", getattr(v, "name", None), id(v))


class Graph:
    COLUMN = 180
    ROW = 150

    CHUNK = 150  # nodes per nested group of a deferred graph

    def __init__(self, tree, origin=(0.0, 0.0), defer=False):
        self.tree = tree
        self.nodes, self.links = tree.nodes, tree.links
        self.origin = origin
        self.defer = defer
        self._rows = {}
        self._sep_cache = {}
        self._pending = {}  # structural key -> _Pending
        self._deferred = []  # (input socket, Val) to link in flush()
        self._chunks = 0

    # construction helpers -------------------------------------------------
    def node(self, kind, depth=0, **props):
        """A real node, created now."""
        n = self.nodes.new(kind)
        row = self._rows.get(depth, 0)
        self._rows[depth] = row + 1
        n.location = (self.origin[0] + depth * self.COLUMN, self.origin[1] - row * self.ROW)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def _rec(self, kind, inputs, outputs, out_kinds, **props):
        """Record a node; returns a Val per output. ``inputs``: [(socket
        index or name, value)], ``outputs``: socket indices or names."""
        inputs = [(k, self.wrap(v)) for k, v in inputs]
        key = (kind, tuple(sorted((k, _prop_key(v)) for k, v in props.items())),
               tuple((k, v.key) for k, v in inputs))
        p = self._pending.get(key)
        if p is None:
            depth = 1 + max((v.depth for _k, v in inputs), default=0)
            p = self._pending[key] = _Pending(kind, props, inputs, depth)
        return [Val(self, ok, pend=p, out=o, depth=p.depth) for o, ok in zip(outputs, out_kinds)]

    def _materialize(self, v):
        """Real output socket of a recorded value (creating what it needs)."""
        order, seen, stack = [], set(), [(v.pend, False)]
        while stack:
            p, done = stack.pop()
            if p.real is not None:
                continue
            if done:
                order.append(p)
                continue
            if id(p) in seen:
                continue
            seen.add(id(p))
            stack.append((p, True))
            for _k, iv in p.inputs:
                if iv.pend is not None and iv.pend.real is None:
                    stack.append((iv.pend, False))
        for p in order:
            if p.real is not None:
                continue
            n = self.node(p.kind, p.depth, **p.props)
            for k, iv in p.inputs:
                self.feed(n.inputs[k], iv)
            p.real = n
        return v.pend.real.outputs[v.out]

    # deferred creation ----------------------------------------------------
    def _outer(self, iv):
        """Socket of the own tree that already carries ``iv``, or None."""
        if iv.socket is not None:
            return iv.socket
        if iv.pend.real is not None:
            return iv.pend.real.outputs[iv.out]
        return iv.pend.ext.get(iv.out)

    def _order(self, vals):
        """Recorded nodes needed for ``vals``, inputs first."""
        order, seen = [], set()
        for v in vals:
            stack = [(v.pend, False)]
            while stack:
                p, done = stack.pop()
                if done:
                    order.append(p)
                    continue
                if id(p) in seen:
                    continue
                seen.add(id(p))
                stack.append((p, True))
                for _k, iv in reversed(p.inputs):
                    if iv.pend is not None and self._outer(iv) is None:
                        stack.append((iv.pend, False))
        return order

    def flush(self):
        """Create the nodes of the deferred feeds and link them."""
        feeds, self._deferred = self._deferred, []
        if not feeds:
            return
        order = self._order([v for _s, v in feeds if self._outer(v) is None])
        if len(order) <= self.CHUNK:
            for p in order:
                n = self.node(p.kind, p.depth, **p.props)
                for k, iv in p.inputs:
                    self.feed(n.inputs[k], iv)
                p.real = n
        else:
            chunks = [order[i:i + self.CHUNK] for i in range(0, len(order), self.CHUNK)]
            where = {id(p): ci for ci, chunk in enumerate(chunks) for p in chunk}
            exports = [{} for _c in chunks]  # per chunk: {(id(p), out): Val}
            for ci, chunk in enumerate(chunks):
                for p in chunk:
                    for _k, iv in p.inputs:
                        if iv.pend is not None and where.get(id(iv.pend), ci) != ci:
                            exports[where[id(iv.pend)]][(id(iv.pend), iv.out)] = iv
            for _s, v in feeds:
                if id(v.pend) in where:
                    exports[where[id(v.pend)]][(id(v.pend), v.out)] = v
            for ci, chunk in enumerate(chunks):
                self._chunk(chunk, list(exports[ci].values()), where, ci)
        for sock, v in feeds:
            self.links.new(self._outer(v), sock)

    @staticmethod
    def _import_key(iv):
        return ("s", iv.socket.as_pointer()) if iv.socket is not None else (id(iv.pend), iv.out)

    def _chunk(self, chunk, exports, where, ci):
        """One nested group with the nodes of ``chunk``: outputs for
        ``exports``, inputs for what it takes from the own tree."""
        import bpy
        self._chunks += 1
        tree = bpy.data.node_groups.new(f".{self.tree.name} part {self._chunks}", "ShaderNodeTree")
        types = {"f": "NodeSocketFloat", "v": "NodeSocketVector"}
        inside = {id(p) for p in chunk}
        imports = {}  # key -> (index, socket in the own tree)
        for p in chunk:
            for _k, iv in p.inputs:
                if iv.is_const or (iv.pend is not None and id(iv.pend) in inside):
                    continue
                key = self._import_key(iv)
                if key not in imports:
                    tree.interface.new_socket(f"in {len(imports)}", in_out="INPUT", socket_type=types[iv.kind])
                    imports[key] = (len(imports), self._outer(iv))
        for i, v in enumerate(exports):
            tree.interface.new_socket(f"out {i}", in_out="OUTPUT", socket_type=types[v.kind])
        nodes, links = tree.nodes, tree.links
        low = min(p.depth for p in chunk)
        high = max(p.depth for p in chunk)
        gin = nodes.new("NodeGroupInput")
        gin.location = (-self.COLUMN, 0.0)
        made, rows = {}, {}
        for p in chunk:
            n = nodes.new(p.kind)
            row = rows.get(p.depth, 0)
            rows[p.depth] = row + 1
            n.location = ((p.depth - low) * self.COLUMN, -row * self.ROW)
            for k, val in p.props.items():
                setattr(n, k, val)
            made[id(p)] = n
            for k, iv in p.inputs:
                if iv.is_const:
                    self._set_const(n.inputs[k], iv.const)
                elif iv.pend is not None and id(iv.pend) in inside:
                    links.new(made[id(iv.pend)].outputs[iv.out], n.inputs[k])
                else:
                    links.new(gin.outputs[imports[self._import_key(iv)][0]], n.inputs[k])
        gout = nodes.new("NodeGroupOutput")
        gout.location = ((high - low + 1) * self.COLUMN, 0.0)
        for i, v in enumerate(exports):
            links.new(made[id(v.pend)].outputs[v.out], gout.inputs[i])
        gn = self.node("ShaderNodeGroup", high)
        gn.node_tree = tree
        gn.label = f"part {self._chunks}"
        for index, outer in imports.values():
            self.links.new(outer, gn.inputs[index])
        for i, v in enumerate(exports):
            v.pend.ext[v.out] = gn.outputs[i]

    def wrap(self, x):
        if isinstance(x, Val):
            return x
        if isinstance(x, (int, float)):
            return Val(self, "f", const=float(x))
        if isinstance(x, (tuple, list)) and len(x) >= 3:
            return Val(self, "v", const=tuple(float(c) for c in x[:3]))
        raise TypeError(f"cannot use {x!r} in a node expression")

    def socket(self, out, kind, depth):
        return Val(self, kind, socket=out, depth=depth)

    def feed(self, sock, v):
        """Connect a Val to an input socket (or set its default)."""
        v = self.wrap(v)
        if v.is_const:
            self._set_const(sock, v.const)
        elif v.socket is not None:
            self.links.new(v.socket, sock)
        elif v.out in v.pend.ext:
            self.links.new(v.pend.ext[v.out], sock)
        elif self.defer and v.pend.real is None:
            self._deferred.append((sock, v))
        else:
            self.links.new(self._materialize(v), sock)

    @staticmethod
    def _set_const(sock, c):
        if sock.type in ("RGBA",):
            c = (c, c, c, 1.0) if isinstance(c, float) else (*c, 1.0)
        elif sock.type == "VECTOR" and isinstance(c, float):
            c = (c, c, c)
        elif sock.type == "VALUE" and isinstance(c, tuple):
            c = c[0]
        sock.default_value = c

    def link(self, v, sock):
        """feed() with the arguments in link order."""
        self.feed(sock, v)

    def _depth(self, *vals):
        return 1 + max((v.depth for v in vals if isinstance(v, Val)), default=0)

    def broadcast(self, v):
        v = self.wrap(v)
        if v.kind == "v":
            return v
        if v.is_const:
            return Val(self, "v", const=(v.const,) * 3)
        return self.vec(v, v, v)

    # inputs ---------------------------------------------------------------
    def value(self, name, default=0.0):
        """A named Value node (driven from Python, e.g. wear)."""
        n = self.nodes.get(name)
        if n is None:
            n = self.node("ShaderNodeValue", 0)
            n.name = n.label = name
            n.outputs[0].default_value = default
        return self.socket(n.outputs[0], "f", 0)

    # arithmetic -----------------------------------------------------------
    def op(self, op, a, b=None):
        a = self.wrap(a)
        b = self.wrap(b) if b is not None else None
        if a.is_const and (b is None or b.is_const):
            if a.kind == "f" and (b is None or b.kind == "f"):
                return self.wrap(_fold(op, a.const, b.const if b is not None else 0.0))
            ac = a.const if a.kind == "v" else (a.const,) * 3
            if b is None:
                return self.wrap(tuple(_fold(op, x, 0.0) for x in ac))
            bc = b.const if b.kind == "v" else (b.const,) * 3
            return self.wrap(tuple(_fold(op, x, y) for x, y in zip(ac, bc)))
        # identities keep the graph small
        if b is not None and b.is_const and b.kind == "f":
            if op in ("ADD", "SUBTRACT") and b.const == 0.0:
                return a
            if op in ("MULTIPLY", "DIVIDE") and b.const == 1.0:
                return a
            if op == "POWER" and b.const == 1.0:
                return a
        if a.is_const and a.kind == "f" and b is not None:
            if op == "ADD" and a.const == 0.0:
                return b
            if op == "MULTIPLY" and a.const == 1.0:
                return b
        vector = a.kind == "v" or (b is not None and b.kind == "v")
        if not vector:
            ins = [(0, a)] + ([(1, b)] if b is not None else [])
            return self._rec("ShaderNodeMath", ins, [0], "f",
                             operation="FRACT" if op == "FRACTION" else op)[0]
        if op == "MULTIPLY" and b is not None and (a.kind == "f" or b.kind == "f"):
            vec, s = (a, b) if a.kind == "v" else (b, a)
            return self._rec("ShaderNodeVectorMath", [(0, vec), (3, s)], [0], "v", operation="SCALE")[0]
        if op not in _VEC_OPS:
            xs = self.sep(a)
            ys = self.sep(b) if b is not None else (None,) * 3
            return self.vec(*(self.op(op, x, y) for x, y in zip(xs, ys)))
        ins = [(0, self.broadcast(a))] + ([(1, self.broadcast(b))] if b is not None else [])
        return self._rec("ShaderNodeVectorMath", ins, [0], "v", operation=op)[0]

    def add(self, a, b): return self.op("ADD", a, b)
    def min(self, a, b): return self.op("MINIMUM", a, b)
    def max(self, a, b): return self.op("MAXIMUM", a, b)
    def floor(self, a): return self.op("FLOOR", a)
    def fract(self, a): return self.op("FRACTION", a)
    def abs(self, a): return self.op("ABSOLUTE", a)
    def sqrt(self, a): return self.op("SQRT", a)
    def pow(self, a, b): return self.op("POWER", a, b)

    def step(self, edge, x):
        """GLSL step: 1 where x >= edge."""
        return 1.0 - self.op("LESS_THAN", x, edge)

    def clamp(self, x, lo=0.0, hi=1.0):
        x = self.wrap(x)
        if x.kind == "v":
            return self.max(self.min(x, hi), lo)
        if x.is_const:
            return self.wrap(min(max(x.const, lo), hi))
        if lo == 0.0 and hi == 1.0:
            return self._rec("ShaderNodeMath", [(0, x), (1, 0.0)], [0], "f",
                             operation="ADD", use_clamp=True)[0]
        return self.max(self.min(x, hi), lo)

    def saturate(self, x):
        return self.clamp(x, 0.0, 1.0)

    def mix(self, a, b, t):
        """GLSL mix (t not clamped). Scalar t on vectors mixes uniformly."""
        a, b, t = self.wrap(a), self.wrap(b), self.wrap(t)
        if t.is_const and t.kind == "f":
            if t.const == 0.0:
                return a
            if t.const == 1.0:
                return b
        if a.is_const and b.is_const and t.is_const:
            return a + (b - a) * t
        if a.key == b.key:
            return a
        vector = a.kind == "v" or b.kind == "v"
        if not vector:
            if t.kind == "v":
                raise TypeError("vector factor for scalar mix")
            return self._rec("ShaderNodeMix", [(0, t), (2, a), (3, b)], [0], "f",
                             data_type="FLOAT", clamp_factor=False)[0]
        if t.kind == "v":
            return self._rec("ShaderNodeMix", [(1, t), (4, self.broadcast(a)), (5, self.broadcast(b))], [1], "v",
                             data_type="VECTOR", clamp_factor=False, factor_mode="NON_UNIFORM")[0]
        return self._rec("ShaderNodeMix", [(0, t), (4, self.broadcast(a)), (5, self.broadcast(b))], [1], "v",
                         data_type="VECTOR", clamp_factor=False)[0]

    def smoothstep(self, e0, e1, x):
        e0, e1, x = self.wrap(e0), self.wrap(e1), self.wrap(x)
        if x.kind == "v":
            return self.vec(*(self.smoothstep(e0, e1, c) for c in self.sep(x)))
        if e0.is_const and e1.is_const and x.is_const:
            d = e1.const - e0.const
            t = min(max((x.const - e0.const) / d, 0.0), 1.0) if d else (0.0 if x.const < e0.const else 1.0)
            return self.wrap(t * t * (3.0 - 2.0 * t))
        return self._rec("ShaderNodeMapRange", [(0, x), (1, e0), (2, e1), (3, 0.0), (4, 1.0)], [0], "f",
                         data_type="FLOAT", interpolation_type="SMOOTHSTEP", clamp=True)[0]

    # vectors --------------------------------------------------------------
    def vec(self, x, y, z):
        x, y, z = self.wrap(x), self.wrap(y), self.wrap(z)
        if x.is_const and y.is_const and z.is_const:
            return self.wrap((x.const, y.const, z.const))
        # vec(sep(v)) is v again
        if x.pend is not None and x.pend is y.pend is z.pend and x.pend.kind == "ShaderNodeSeparateXYZ" \
                and (x.out, y.out, z.out) == (0, 1, 2):
            return x.pend.inputs[0][1]
        return self._rec("ShaderNodeCombineXYZ", [(0, x), (1, y), (2, z)], [0], "v")[0]

    def sep(self, v):
        v = self.wrap(v)
        if v.kind == "f":
            return (v, v, v)
        if v.is_const:
            return tuple(self.wrap(c) for c in v.const)
        if v.pend is not None and v.pend.kind == "ShaderNodeCombineXYZ":
            return tuple(iv for _k, iv in v.pend.inputs)
        key = v.key
        if key not in self._sep_cache:
            self._sep_cache[key] = tuple(self._rec("ShaderNodeSeparateXYZ", [(0, v)], [0, 1, 2], "fff"))
        return self._sep_cache[key]

    def dot(self, a, b):
        a, b = self.wrap(a), self.wrap(b)
        if a.is_const and b.is_const:
            return self.wrap(sum(x * y for x, y in zip(self.broadcast(a).const, self.broadcast(b).const)))
        return self._rec("ShaderNodeVectorMath", [(0, self.broadcast(a)), (1, self.broadcast(b))], ["Value"], "f",
                         operation="DOT_PRODUCT")[0]

    def normalize(self, v):
        v = self.wrap(v)
        if v.is_const:
            c = v.const
            n = math.sqrt(sum(x * x for x in c)) or 1.0
            return self.wrap(tuple(x / n for x in c))
        return self._rec("ShaderNodeVectorMath", [(0, v)], [0], "v", operation="NORMALIZE")[0]

    def max3(self, v):
        x, y, z = self.sep(v)
        return self.max(x, self.max(y, z))

    def lum(self, v):
        return self.dot(v, (0.2125, 0.7154, 0.0721))

    # textures -------------------------------------------------------------
    def texture(self, image, uv=None, extension="REPEAT", interpolation="Linear"):
        """Sample an image: returns (rgb, alpha). ``uv`` None uses the
        active UV map. A missing image samples as black."""
        ins = [(0, self.wrap(uv))] if uv is not None else []
        rgb, alpha = self._rec("ShaderNodeTexImage", ins, [0, 1], "vf",
                               image=image, extension=extension, interpolation=interpolation)
        return rgb, alpha
