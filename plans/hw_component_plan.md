# HwComponent Synthesis Plan

_Status: draft — delete when feature ships_

---

## 0. Module layout

### New files

| File | Contents | Layer |
|---|---|---|
| `pysilicon/hw/synth.py` | `@synthesizable` decorator (`synth_fn` optional; omitting defaults to stub) | `hw` — no build deps |
| `pysilicon/hw/hw_component.py` | `HwComponent`, `HwParam[T]`, `SynthContext`, `ControlMode` | `hw` |
| `pysilicon/hw/hwstmt.py` | `HwStmt` base hierarchy, `HwVar`, `HwExpr` (`Ref`, `FieldRef`), `SynthCallStmt` | `hw` — IR types only, no codegen |
| `pysilicon/build/hwcodegen.py` | `HwStmtExtractor`, `HlsKernelStep`, `HlsImplStep` | `build` |

### Where endpoint `HwStmt` subclasses live

Endpoint-owned statement classes stay alongside their endpoint:

| Statement class | Lives in |
|---|---|
| `StreamGetStmt`, `StreamWriteStmt`, `StreamDrainStmt` | `pysilicon/hw/interface.py` |
| `MMArrayReadStmt`, `MMArrayWriteStmt`, `MMSchemaReadStmt`, `MMSchemaWriteStmt` | `pysilicon/hw/memif.py` |

All import `SynthCallStmt` from `pysilicon/hw/hwstmt.py`.

### Dependency graph (no cycles)

```
dataschema.py ──────────────────────────────────────────────┐
arrayutils.py ──────────────────────────────────────────────┤
synth.py      (no hw deps)                                  │
                                                            ▼
interface.py  → dataschema.py, synth.py                    hwstmt.py → dataschema.py, arrayutils.py
memif.py      → interface.py                                           (interface.py, hw_component.py
component.py  → (interface.py TYPE_CHECKING only)                       under TYPE_CHECKING)
                                                            │
hw_component.py → component.py, synth.py                   │
                  (hwstmt.py TYPE_CHECKING only)            │
                                                            │
build/hwcodegen.py → hw/hw_component.py, hw/hwstmt.py, build/build.py
```

`synth.py` has no imports from other `hw/` files — only `typing` — so both `interface.py` and `hw_component.py` can safely import `@synthesizable` from it without creating cycles.

`hwstmt.py` references `InterfaceEndpoint` and `HwComponent` only under `TYPE_CHECKING` (for `HwVar.producer` and `SynthCallStmt` input types), avoiding a cycle with `interface.py`.

---

## 1. What exists today

### Core simulation infrastructure

| Concept | Class | File |
|---|---|---|
| Simulation entity base | `SimObj(NamedObject)` | `pysilicon/simulation/simobj.py` |
| Hardware component | `Component(SimObj)` | `pysilicon/hw/component.py` |
| Endpoint base | `InterfaceEndpoint(SimObj)` | `pysilicon/hw/interface.py` |
| Interface base | `Interface(SimObj)` | `pysilicon/hw/interface.py` |
| AXI-Stream (sim) | `StreamIF`, `StreamIFMaster`, `StreamIFSlave` | `pysilicon/hw/interface.py` |
| Crossbar (sim) | `CrossBarIF`, `CrossBarIFInput`, `CrossBarIFOutput` | `pysilicon/hw/interface.py` |
| Type system | `DataSchema`, `DataList`, `DataArray`, `IntField`, `FloatField`, `EnumField` | `pysilicon/hw/dataschema.py` |
| Build DAG | `BuildDag`, `BuildStep`, `Buildable`, `BuildConfig` | `pysilicon/build/build.py` |

**`SimObj` lifecycle** (critical for codegen mapping):

```
pre_sim()     →  initialization / callback registration
run_proc()    →  simpy generator — the behavior specification
post_sim()    →  teardown / logging
```

**`Component`** is currently minimal — a `SimObj` with `endpoints: dict[str, InterfaceEndpoint]` and `add_endpoint()`. There is no `HwComponent` subclass yet.

**`DataSchema`** already has C++ codegen methods: `gen_write(word_bw, dst_type, ...)` and `gen_read(word_bw, src_type, ...)`. These emit the serialization/deserialization code that kernel functions use. `nwords_per_inst(word_bw)` gives the word count for any type. This is the most significant existing codegen asset.

**`pysilicon/build/`** already has `DataSchemaStep(Buildable)` (generates schema headers) and stream utility files (`streamutils.py`, `streamutils_hls.h`, `streamutils_tb.h`, `memmgr.hpp`). The build DAG is functional.

### What `poly_demo_simpy.py` shows about the target

`PolyAccelComponent` is the clearest concrete example of the pattern we want to synthesize:

```python
@dataclass
class PolyAccelComponent(Component):
    in_bw: int = 32
    out_bw: int = 32

    def __post_init__(self):
        self.s_in  = StreamIFSlave(...)   # → hls::stream in the kernel
        self.m_out = StreamIFMaster(...)  # → hls::stream in the kernel

    def run_proc(self):               # → kernel function body
        while True:
            cmd_words = yield from self.s_in.get(nwords_max=N)
            cmd_hdr   = PolyCmdHdr().deserialize(cmd_words, word_bw=32)
            ...
            samp_out  = self.evaluate(cmd_hdr, samp_in)   # ← compute hook
            yield from self.m_out.write(resp_hdr.serialize(word_bw=32))
```

The kernel orchestration (`run_proc`) is already clean and mechanical. The algorithmic body (`evaluate`) is already factored out. This is the pattern codegen must formalize.

---

## 2. Goal

Make every `Component` with the right structure synthesizable to a Vitis HLS kernel. Two integration backends on top of shared module-level codegen:

- **Vitis acceleration flow** — `v++`, `.xo`, `.xclbin`, `connectivity.cfg`, PyXRT host. Targets Alveo, Versal.
- **Vivado IPI flow** — IP packaging, block-design Tcl, PYNQ overlay driver. Targets RFSoC 4x2 and other Zynq/RFSoC boards where the RF Data Converter requires Vivado IPI.

Module codegen (Layer 1) is identical for both backends. Backends differ only in system integration (Layer 2) and host code (Layer 3).

---

## 3. HwComponent

### 3.1 New marker class

A synthesizable component is a `Component` that additionally carries:

| Addition | What it is | Status |
|---|---|---|
| `HwParam[T]` field annotations | compile-time template parameters | **Gap** |
| `@synthesizable` methods (no `synth_fn`) | Python reference impl + C++ stub target | **Gap** |
| Control mode declaration | `free_running` vs `per_invocation` | **Gap** |
| Typed endpoints | already in `Component.endpoints` via `DataSchema` | Exists |
| `run_proc` returning `HwStmt` tree | dual-use orchestration spec | Exists (needs `HwStmt` classes) |

Proposed class, subject to revision:

```python
class HwComponent(Component):
    control_mode: ClassVar[ControlMode] = ControlMode.AUTO
```

`ControlMode` is inferred from the top-level `HwStmt` returned by `run_proc`: `WhileStmt` at the root → free-running (`ap_ctrl_none`); `SeqStmt` → per-invocation (`ap_ctrl_chain`). The explicit `control_mode` class attribute exists only as an override for unusual cases where inference is insufficient.

### 3.2 `HwParam[T]` — synthesis template parameters

Dataclass fields on an `HwComponent` fall into three categories for synthesis:

| Annotation | Synthesis treatment | Example |
|---|---|---|
| `ClassVar[T]` | Literal constant — substituted directly in generated C++ | `MAX_TAPS: ClassVar[int] = 64` |
| `HwParam[T]` | C++ template parameter — referenced by name in generated C++ | `in_bw: HwParam[int] = 32` |
| Plain field (no marker) | Simulation-only — not accessible in synthesis | `name`, `sim`, etc. |

`HwParam[T]` is a generic wrapper that in simulation behaves as `T` (just a normal Python attribute). At build time, the extractor collects all `HwParam` fields and generates a C++ template signature:

```python
@dataclass
class PolyAccelComponent(HwComponent):
    in_bw:     HwParam[int] = 32
    out_bw:    HwParam[int] = 32
    nwords_max: HwParam[int] = 256
```

Generated C++ signature:

```cpp
template <int IN_BW, int OUT_BW, int NWORDS_MAX>
void poly_accel(
    hls::stream<ap_uint<IN_BW>>& s_in,
    hls::stream<ap_uint<OUT_BW>>& m_out,
    ...
)
```

#### `HwParam[T]` implementation

`HwParam` is a plain `Generic[T]` class. This makes `HwParam[int]` a standard `typing._GenericAlias` detectable via `typing.get_origin` and `typing.get_args`:

```python
from typing import Generic, TypeVar
T = TypeVar('T')

class HwParam(Generic[T]):
    """Marks a dataclass field as a C++ template parameter.
    
    In simulation the field behaves as a normal Python attribute (dataclass
    does not enforce types). At build time the extractor collects all
    HwParam fields from get_type_hints() and maps them to C++ template names.
    """
```

Detection at build time:
```python
import typing
hints = typing.get_type_hints(type(component))
hw_params = {
    name: name.upper()          # in_bw → IN_BW, nwords_max → NWORDS_MAX
    for name, hint in hints.items()
    if typing.get_origin(hint) is HwParam
}
```

C++ name convention: `field_name.upper()` — snake_case becomes UPPER_SNAKE_CASE. `in_bw → IN_BW`, `nwords_max → NWORDS_MAX`.

`HwParam` lives in `hw_component.py` (not `synth.py`) since it is only relevant to `HwComponent` field annotations.

#### `SynthContext` carries the parameter mapping

`SynthContext` is passed to every `synth_fn`. It exposes `HwParam` fields both as values (for simulation-side checks) and as C++ name strings (for code emission):

```python
@dataclass
class SynthContext:
    component: HwComponent
    params: dict[str, str]   # Python name → C++ template param name, e.g. {'in_bw': 'IN_BW'}

    def cpp_param(self, py_name: str) -> str:
        """Return C++ expression for a parameter: template name if HwParam, literal if ClassVar."""
        if py_name in self.params:
            return self.params[py_name]              # e.g. 'IN_BW'
        return repr(getattr(self.component, py_name))  # e.g. '64'
```

A `synth_fn` for `StreamIFSlave.get` uses `ctx.cpp_param('in_bw')` to emit `hls::stream<ap_uint<IN_BW>>` rather than the hard-coded literal `32`. The same `synth_fn` works for any bitwidth configuration without modification.

### 3.3 Synthesizable compute methods

User-implemented algorithmic leaves are decorated with `@synthesizable` (no `synth_fn`). When `synth_fn` is omitted, the decorator defaults to stub behavior: at build time it emits a call to a user-written function in `_impl.cpp` rather than generating the full implementation. The Python body is the simulation golden reference and also drives HLS C-sim.

```python
class PolyAccelComponent(HwComponent):
    @synthesizable
    def evaluate(self,
                 cmd_hdr: PolyCmdHdr,
                 samp_in: SchemaArray[Float32],
                 ) -> tuple[PolyRespHdr, SchemaArray[Float32], PolyRespFtr]:
        """Reference implementation — also the C-sim golden."""
        ...
```

All arguments and return values must be `DataSchema` instances or `SchemaArray[T]` — the two synthesizable data types (§4.0). `MMIFMaster` endpoints for sub-component memory are passed as additional arguments before the data args.


### 3.4 Synthesis hints — deferred

Pragma surface (`PIPELINE`, `DATAFLOW`, `ARRAY_PARTITION`, etc.) is deferred. When needed, hints will be expressed as arguments on the `@synthesizable` decorator rather than as a separate `SynthesisHints` class. Interface pragmas (`m_axi`, `axis`, `s_axilite`) are always derived automatically from endpoint types.

---

## 4. `HwStmt` — synthesizable orchestration via AST parsing

`run_proc` on an `HwComponent` is written as normal Python. In simulation it runs as a SimPy generator unchanged. At build time an `HwStmtExtractor` parses its AST, maps recognized patterns to `HwStmt` nodes, and raises `SynthesisError` for anything outside the synthesizable subset. The user never constructs `HwStmt` objects manually.

### 4.0 Synthesizable data types

Exactly two data types cross synthesis boundaries (stream endpoints, memory endpoints, hook arguments):

| Type | Python representation | HLS C++ representation | Notes |
|---|---|---|---|
| `DataSchema` subclass | Python object with field attributes | C++ struct | Structured, named fields |
| `SchemaArray[T]` | `np.ndarray` (scalar `T`) or `list[T]` (complex `T`) | `T arr[N]` | Homogeneous array of a scalar field type |

`SchemaArray` is **not** a `DataSchema` subclass — it is a separate generic container for runtime transfer buffers. `T` is a scalar field type (`Float32`, `IntField` subclass, `EnumField` subclass).

`DataArray` (which already exists as a `DataSchema` subclass) is for static-shape embedded arrays *inside* a schema definition, not for variable-count stream or memory buffers. The two are distinct:

| | `DataArray` | `SchemaArray[T]` |
|---|---|---|
| Is a `DataSchema` | Yes — appears inside schema definitions | No — used at transfer boundaries |
| Shape | Static (`nrows` fixed at class definition) | Dynamic (count comes from a `HwVar` or literal) |
| Python repr | `list` of schema instances | `np.ndarray` or `list` |
| HLS repr | struct field | local array argument |

`HwVar.schema` holds either a `type[DataSchema]` or a `SchemaArray[T]` instance (which carries `elem_type` and an optional static `count`).

### 4.1 Synthesizable subset

The allowed patterns in `run_proc` are deliberately narrow:

| Python construct | Maps to | Notes |
|---|---|---|
| `while True:` | `WhileStmt` | only `while True` — no condition expression |
| `var: T = yield from self.ep.method(...)` | `SynthCallStmt` + `HwVar` binding | `ep` method must have `@synthesizable`; `T` is `DataSchema` or `SchemaArray[T]` |
| `yield from self.ep.method(var)` | `SynthCallStmt` (no binding) | write / drain — no output variable |
| `a, b = self.hook(var1, var2)` | `SynthCallStmt` + multiple `HwVar` bindings | hook must have `@synthesizable`; types from return annotation |
| `if var.field == EnumValue:` | `CaseStmt` | restricted form only; body may contain `continue` |
| `continue` | `ContinueStmt` | only inside `while True` |

The underlying `HwStmt` type for any `@synthesizable` call is `SynthCallStmt`, which holds the `synth_fn` and the resolved input/output `HwVar`s. Typed subclasses (`StreamGetStmt`, `MMArrayReadStmt`, etc.) extend this for better error messages and introspection.

Everything else — `for`, general `if`, arithmetic, comprehensions, calls to non-`@synthesizable` methods — raises `SynthesisError` at build time with a message pointing to the offending AST node.

### 4.2 What `run_proc` looks like

```python
def run_proc(self):
    while True:
        cmd_hdr: PolyCmdHdr           = yield from self.s_in.get(PolyCmdHdr)
        samp_in: SchemaArray[Float32] = yield from self.s_in.get(Float32, count=cmd_hdr.nsamp)
        resp_hdr, samp_out, resp_ftr  = self.evaluate(cmd_hdr, samp_in)
        yield from self.m_out.write(resp_hdr)
        yield from self.m_out.write(samp_out)
        yield from self.m_out.write(resp_ftr)
```

This is valid SimPy Python — no special objects, no tree construction. The AST extractor runs only when `HwComponent.build()` is called.

### 4.3 `HwVar` and `HwExpr` — internal, not user-facing

`HwVar` and `HwExpr` are implementation details of the extractor and codegen. Users never construct them.

`HwVar` is created by the extractor for each binding (`var: T = ...`):

```python
@dataclass
class HwVar:
    name:     str                              # Python variable name → C++ identifier
    typ:      type[DataSchema] | SchemaArray   # from the type annotation
    producer: HwStmt                           # for ordering validation
```

`typ` is either a `DataSchema` subclass (for structured values) or a `SchemaArray[T]` instance (for homogeneous arrays). The extractor reads the annotation; the codegen uses `typ` to emit the correct C++ declaration.

#### C++ mapping for `HwVar`

| `typ` | C++ declaration | Example |
|---|---|---|
| `DataSchema` subclass | `schema_type.cpp_repr() name;` | `PolyCmdHdr cmd_hdr;` |
| `SchemaArray[T]` (static count) | `T.cpp_repr() name[N];` | `float samp_in[256];` |
| `SchemaArray[T]` (dynamic count from `HwVar`) | `T.cpp_repr() name[MAX_N];` + `int name_count;` | `float samp_in[MAX_NSAMP]; int samp_in_count;` |

For dynamic count, `MAX_N` comes from the `nwords_max` argument on the endpoint call (a static upper bound required by HLS). The runtime count is tracked as a paired `int name_count` variable, initialised from the `FieldRef` expression (e.g., `cmd_hdr.nsamp`).

#### C++ mapping for `InterfaceEndpoint` arguments

Each `InterfaceEndpoint` subclass defines `to_cpp_arg(name: str) -> str` returning the C++ parameter declaration:

| Endpoint | C++ argument | Notes |
|---|---|---|
| `StreamIFSlave` | `hls::stream<ap_uint<BW>>& name` | `BW` from endpoint `bitwidth` |
| `StreamIFMaster` | `hls::stream<ap_uint<BW>>& name` | same |
| `MMIFMaster` (local sub-component, `inline=True`) | `T (&name)[N]` | C array reference; `T` from `elem_type.cpp_repr()`, `N` from `nwords_tot` |
| `MMIFMaster` (external, `inline=False`) | `T* name` | pointer; `m_axi` pragma emitted separately |

These mappings are used both for kernel function signatures (top-level arguments) and for `synth_fn` argument lists in hook calls.

`HwExpr` covers field access needed in synthesizable expressions (`cmd_hdr.nsamp`):

```
HwExpr
├── Ref(var: HwVar)
└── FieldRef(var: HwVar, field: str)   # validated against var.schema at extraction time
```

`Compare` and `BinOp` are deferred — arithmetic belongs in compute hooks, not in orchestration.

### 4.4 `HwStmt` class hierarchy (internal)

The extractor produces a tree of these; codegen walks it via `to_cpp(ctx)`:

```
HwStmt (base)
├── WhileStmt          — while(true) + ap_ctrl_none
├── SeqStmt            — sequential list of child HwStmts
├── CaseStmt           — enum match (→ switch/if-else in C++)
├── ForStmt            — constant-trip-count loop
├── HookStmt           — @synthesizable (no synth_fn) call → C++ stub in _impl.cpp
└── ContinueStmt       — continue in while body
```

I/O statements are owned by their `InterfaceEndpoint`, not in this hierarchy:

```
StreamIFSlave.get(...)    → StreamGetStmt(HwStmt)
StreamIFMaster.write(...) → StreamWriteStmt(HwStmt)
StreamIFSlave.drain(...)  → StreamDrainStmt(HwStmt)
MMIFMaster.read_array(…)  → MMArrayReadStmt(HwStmt)   # in aximm.py
```

Each endpoint `HwStmt` subclass lives alongside its endpoint class. Codegen calls `to_cpp(ctx)` polymorphically with no central registry.

### 4.5 `@synthesizable` — the unified decorator

#### Implementation (`pysilicon/hw/synth.py`)

`@synthesizable` must work both with and without parentheses. The standard Python pattern:

```python
def synthesizable(fn=None, *, synth_fn=None):
    def decorator(f):
        f._is_synthesizable = True
        f._synth_fn = synth_fn   # None → stub behavior at codegen time
        return f
    if fn is not None:
        return decorator(fn)   # used as @synthesizable (no parens)
    return decorator           # used as @synthesizable(...) with args
```

The extractor detects synthesizable methods via `getattr(method, '_is_synthesizable', False)`. The `_synth_fn` attribute is `None` for user compute methods (stub) or a callable for endpoint methods (full codegen).

`synth.py` imports only from `typing` — no other `pysilicon` modules.

Every call that appears in a synthesizable `run_proc` must be on a method decorated with `@synthesizable`. This applies equally to **interface endpoint methods** and **user compute methods** — there is no separate concept, no `@compute_hook`.

`synth_fn` is optional:

| Usage | Meaning |
|---|---|
| `@synthesizable(synth_fn=_gen_fn)` | custom codegen — used on interface endpoint methods |
| `@synthesizable` (no `synth_fn`) | stub codegen — emits a call to user-written C++ in `_impl.cpp` |

```python
# Interface endpoint — explicit synth_fn generates full inline HLS code
def _gen_stream_get(ctx: SynthContext,
                    inputs: list[HwVar | InterfaceEndpoint],
                    outputs: list[HwVar]) -> str: ...

class StreamIFSlave(InterfaceEndpoint):
    @synthesizable(synth_fn=_gen_stream_get)
    def get(self, schema_type, count=None):
        ...  # SimPy body unchanged

# User compute method — no synth_fn; stub emits a call to _impl.cpp
class PolyAccelComponent(HwComponent):
    @synthesizable
    def evaluate(self, cmd_hdr, samp_in): ...
```

The `synth_fn` signature is always `(ctx: SynthContext, inputs: list[HwVar | InterfaceEndpoint], outputs: list[HwVar]) -> str`. Inputs may be typed values (`HwVar`) or interface endpoints (`InterfaceEndpoint` subclasses such as `MMIFMaster`) passed directly from `self`. Outputs are always `HwVar` — bound to Python variable names by the extractor. The extractor calls `synth_fn` when walking the `HwStmt` tree during `to_cpp()`.

**Why module-level functions instead of class methods?** At class definition time `self` and `cls` are not yet available, so `synth_fn=self.get_synth` cannot work. A module-level function avoids the forward-reference problem entirely.

The `HwStmt` subclasses for I/O operations (`StreamGetStmt`, `MMArrayReadStmt`, etc.) remain as typed wrappers — they hold the `synth_fn` reference, the input/output `HwVar`s, and produce helpful error messages. They are created by the extractor, not by the user.

### 4.6 Error handling in `run_proc`

A TLAST framing error in normal Python:

```python
def run_proc(self):
    while True:
        cmd_hdr: PolyCmdHdr = yield from self.s_in.get(PolyCmdHdr)
        if cmd_hdr.tlast_error:
            yield from self.status.write_reg(PolyError.TLAST_EARLY_CMD_HDR)
            yield from self.s_in.drain()
            continue
        ...
```

**Wait — `if` is not in the synthesizable subset.** This is where `CaseStmt` is needed: a restricted `if` that only tests a single `DataSchema` field against a literal or enum value maps to `CaseStmt`. The extractor recognises:

```python
if var.field == EnumValue:
    ...
    continue
```

as a `CaseStmt(..., if_true=SeqStmt([..., ContinueStmt()]))`. Any `if` that does not match this pattern raises `SynthesisError`.

### 4.7 `HwStmtExtractor`

A build-time visitor (`ast.NodeVisitor`) over the `run_proc` source. Runs when `HwComponent.build()` is called. Returns the root `HwStmt` for codegen.

Validation performed:
- Every statement is in the allowed set (error otherwise)
- Every variable used is bound by an earlier statement in the same scope
- Every `FieldRef` matches a real field on its schema type
- Every `@synthesizable` method call matches the declared signature

| | AST visitor | `HwStmt` |
|---|---|---|
| Parsing | required (fragile) | none |
| Error detection | at codegen time | at construction time |
| Inspectable at runtime | no | yes |
| Serializable | no | yes (future) |
| SimPy compatibility | unchanged `run_proc` | `__iter__` on each node |
| New I/O construct | extend vocabulary list | endpoint owns its `HwStmt` subclass; core unchanged |

---

## 5. Error handling — why `simpy.Event` is wrong and what to do instead

The current `poly_demo_simpy.py` error model uses a one-off `simpy.Event` (`self._reset_event`) to block `run_proc` after a TLAST error. **This is not synthesizable.** There is no hardware analog to a process blocking on an arbitrary event — the component either polls a control register or drains and continues.

### Synthesizable error model

Every `HwComponent` gets an implicit AXI-Lite control/status register block:

| Register | Purpose | HLS mapping |
|---|---|---|
| `STATUS` | error code written by kernel | `s_axilite` read-only |
| `CTRL` | bit 0: sw-reset | `s_axilite` write |

In simulation, these are Python attributes on the component:

```python
self.status_reg: int = 0   # written by run_proc on error; host reads it
```

On a framing error, `run_proc` sets `status_reg` and drains its input stream (no `simpy.Event`):

```python
# framing error — set status, drain stream, loop back
self.status_reg = PolyError.TLAST_EARLY_CMD_HDR
while True:                            # drain remaining words in burst
    words = yield from self.s_in.get()
    if words.shape[0] == 0:
        break
continue                               # restart outer while True
```

The testbench polls `status_reg` to detect the error, just as host software polls the AXI-Lite STATUS register. This maps directly to synthesizable hardware with no special sim primitives.

**Gap**: the AXI-Lite CSR endpoint for `HwComponent` does not yet exist. The `status_reg` attribute needs to become a typed `AxiLiteIFSlave` endpoint that codegen auto-generates the `s_axilite` pragma for.

---

## 6. Three generated files per component

```
<component>.h          → types, signatures. Fully generated. Overwritten on regen.
<component>.cpp        → kernel function, pragmas, DataSchema I/O, hook calls. Fully generated.
<component>_impl.cpp   → compute hook bodies. User-editable. Regen preserves existing bodies;
                          adds stubs only for newly introduced hooks.
```

### `<component>.cpp` structure (sketch)

```cpp
#include "<component>.h"
#include "streamutils_hls.h"

void poly_accel(
    hls::stream<ap_uint<32>>& in_stream,
    hls::stream<ap_uint<32>>& out_stream,
    volatile uint32_t& status_reg          // AXI-Lite STATUS
) {
#pragma HLS INTERFACE axis port=in_stream
#pragma HLS INTERFACE axis port=out_stream
#pragma HLS INTERFACE s_axilite port=status_reg bundle=ctrl
#pragma HLS INTERFACE ap_ctrl_none port=return

    while (true) {
        // generated by WhileStmt.to_cpp() → SeqStmt.to_cpp() → ...
        PolyCmdHdr cmd_hdr;
        read_PolyCmdHdr(in_stream, cmd_hdr);   // gen_read() output
        // ...
        evaluate(cmd_hdr, samp_in, resp_hdr, samp_out);  // hook call
        write_PolyRespHdr(out_stream, resp_hdr);          // gen_write() output
    }
}
```

The `gen_write()` and `gen_read()` methods on `DataSchema` already produce the bodies of `write_*` / `read_*` helpers. The kernel function structure comes from `HwStmt.to_cpp()` walking the tree returned by `run_proc`.

---

## 7. Build integration

Codegen plugs into the existing `BuildDag` as new `Buildable` steps:

```
DataSchemaStep      (exists)  →  <component>.h (type declarations)
HlsKernelStep       (new)     →  <component>.cpp + int main() for TB components
HlsImplStep         (new)     →  <component>_impl.cpp (preserve user edits)
```

There is no separate `HlsCsimTbStep`. A testbench is just another `HwComponent` synthesized by `HlsKernelStep`. The only TB-specific difference is that interface pragmas are suppressed (no `#pragma HLS INTERFACE` directives) and `HlsKernelStep` emits a short `int main()` wrapper when `is_testbench=True` is set on the component. The transformation from `run_proc` → C++ is identical for both DUT and TB.

Backend-specific steps extend `BuildStep` and consume the outputs above:

```
VitisBackend:   ConnectivityStep, XoStep, XclbinStep, PyXrtHostStep
VivadoBackend:  IpPackageStep, BlockDesignTclStep, PynqOverlayStep
```

A `Backend` ABC with `VitisBackend` and `VivadoBackend` implementations.

---

## 8. Verification ladder

1. **pysilicon native** — `run_proc` golden vectors at every component boundary (`.npy` files)
2. **HLS C-sim** — TB `HwComponent` synthesized by `HlsKernelStep` loads `.npy` goldens, drives the DUT kernel
3. **SW-emu** — `v++ -t sw_emu` (Vitis) or VFS (Versal) for multi-component integration
4. **HW-emu / XSIM** — cycle-level checks, post-route timing

Same golden vectors at every level. No hand-written testbench.

---

## 9. Identified gaps (not yet in repo)

| Gap | File | Needed for |
|---|---|---|
| `@synthesizable(synth_fn=...)` decorator (optional `synth_fn`; omitting it defaults to stub behavior) | `pysilicon/hw/synth.py` (NEW) | unified marker for all synthesizable calls |
| `HwComponent(Component)`, `ControlMode` | `pysilicon/hw/hw_component.py` (NEW) | synthesizable component base |
| `HwParam[T]` generic annotation | `pysilicon/hw/hw_component.py` (NEW) | compile-time template parameters |
| `SynthContext` dataclass with `cpp_param()` | `pysilicon/hw/hw_component.py` (NEW) | parameter mapping passed to every `synth_fn` |
| `HwStmt` base + control flow subclasses (`WhileStmt`, `SeqStmt`, `CaseStmt`, `ContinueStmt`, `HookStmt`) | `pysilicon/hw/hwstmt.py` (NEW) | internal IR for codegen |
| `SynthCallStmt` base | `pysilicon/hw/hwstmt.py` (NEW) | base for all endpoint I/O statements |
| `HwVar` + `HwExpr` (`Ref`, `FieldRef`) | `pysilicon/hw/hwstmt.py` (NEW) | symbolic values used by extractor and codegen |
| `StreamGetStmt`, `StreamWriteStmt`, `StreamDrainStmt` | `pysilicon/hw/interface.py` (extend) | stream I/O IR nodes (endpoint-owned) |
| `MMArrayReadStmt`, `MMArrayWriteStmt`, `MMSchemaReadStmt`, `MMSchemaWriteStmt` | `pysilicon/hw/memif.py` (extend) | MM I/O IR nodes (endpoint-owned) |
| `HwStmtExtractor` (`ast.NodeVisitor`) | `pysilicon/build/hwcodegen.py` (NEW) | parses `run_proc` AST → `HwStmt` tree at build time |
| `HlsKernelStep` (`is_testbench` flag suppresses pragmas + emits `int main()`), `HlsImplStep` | `pysilicon/build/hwcodegen.py` (NEW) | HLS module codegen |
| `Backend` ABC + `VitisBackend`, `VivadoBackend` | `pysilicon/build/hwcodegen.py` or separate (NEW) | system integration |
| AXI-Lite CSR endpoint for `HwComponent` | TBD | synthesizable error/control model |

---

## 10. Open questions

1. **Compute hook argument types**: require `DataSchema`-typed args at synthesis boundaries, or allow annotated `np.ndarray` with a shape declaration? Former is stricter; latter is more ergonomic for array-heavy kernels.

2. **Hook signature derivation**: infer C++ signature from the Python call site in `run_proc` (more reliable, avoids annotation drift) or from the Python method signature on the class?

3. **`DataSchema.cpp_repr`**: field exists but its semantics for kernel argument types vs. stream word types need to be confirmed before codegen starts.

4. **`gen_write` / `gen_read` exact call conventions**: these methods exist; the exact invocation pattern that `HlsKernelStep` will use needs to be established.

5. **AXI-Lite CSR**: auto-added to every `HwComponent` (clean default, simpler error model) or opt-in (smaller port count for components that don't need it)?

6. **`has_tlast` on endpoints**: just added to `StreamIF`/`StreamIFSlave`/`StreamIFMaster`. For codegen, `has_tlast=False` maps to `hls::stream` without TLAST; `has_tlast=True` maps to `hls::stream` with TLAST (AXI-Stream). Confirm this is the right mapping before implementing `HlsKernelStep`.

---

## 11. Phased plan

| Phase | Deliverable | Files touched | Dependency |
|---|---|---|---|
| 1 | `@synthesizable` (with optional `synth_fn`); `HwComponent`, `HwParam[T]`, `SynthContext` — no codegen | `hw/synth.py` (NEW), `hw/hw_component.py` (NEW) | nothing |
| 2 | `HwStmt` IR + `HwVar`/`HwExpr`; `HwStmtExtractor` (AST → IR) | `hw/hwstmt.py` (NEW), `build/hwcodegen.py` (NEW) | Phase 1 |
| 3 | `StreamGetStmt`, `StreamWriteStmt`, `StreamDrainStmt`; rewrite `PolyAccelComponent.run_proc` to synthesizable subset; fix error model | `hw/interface.py` (extend) | Phase 2 |
| 4 | `HlsKernelStep`, `HlsImplStep` (codegen via `HwStmt.to_cpp()`); TB component uses `is_testbench=True` flag | `build/hwcodegen.py` (extend) | Phase 3 + existing `gen_write`/`gen_read` |
| 5 | C-sim round-trip on `PolyAccelComponent` + TB component | — | Phase 4 |
| 6 | `VitisBackend` (connectivity.cfg, .xo/.xclbin, PyXRT host) | `build/hwcodegen.py` or `build/vitis.py` (NEW) | Phase 5 |
| 7 | `VivadoBackend` (IPI Tcl, IP packaging, PYNQ driver) | `build/vivado.py` (NEW) | Phase 5 |
| 8 | Cross-backend regression: same scenario through both backends vs pysilicon golden | — | Phases 6 + 7 |

**First codegen target**: `PolyAccelComponent`. Two stream endpoints, a factored compute method, `DataSchema`-typed I/O — structurally ready. Blocked only on Phases 2–3.

---

## 12. Phase 1 implementation spec

**Scope**: `pysilicon/hw/synth.py` and `pysilicon/hw/hw_component.py`. No `HwStmt`, no `HwStmtExtractor`, no `HwComponent.build()` — those are Phase 2.

### `pysilicon/hw/synth.py`

```python
def synthesizable(fn=None, *, synth_fn=None):
    def decorator(f):
        f._is_synthesizable = True
        f._synth_fn = synth_fn
        return f
    if fn is not None:
        return decorator(fn)
    return decorator
```

No other contents. No imports from `pysilicon`.

### `pysilicon/hw/hw_component.py`

```python
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import ClassVar, Generic, TypeVar, TYPE_CHECKING
import typing

from pysilicon.hw.component import Component

T = TypeVar('T')

class HwParam(Generic[T]):
    """Marks a dataclass field as a C++ template parameter."""

class ControlMode(Enum):
    AUTO = auto()          # inferred from HwStmt root at build time
    FREE_RUNNING = auto()  # ap_ctrl_none
    PER_INVOCATION = auto() # ap_ctrl_chain

@dataclass
class SynthContext:
    component: HwComponent
    params: dict[str, str]   # Python name → C++ template param name

    def cpp_param(self, py_name: str) -> str:
        if py_name in self.params:
            return self.params[py_name]
        return repr(getattr(self.component, py_name))

    @classmethod
    def from_component(cls, comp: HwComponent) -> SynthContext:
        hints = typing.get_type_hints(type(comp))
        params = {
            name: name.upper()
            for name, hint in hints.items()
            if typing.get_origin(hint) is HwParam
        }
        return cls(component=comp, params=params)

class HwComponent(Component):
    control_mode: ClassVar[ControlMode] = ControlMode.AUTO
```

### Tests (`tests/hw/test_hw_component.py`)

- `@synthesizable` with no args sets `_is_synthesizable=True`, `_synth_fn=None`
- `@synthesizable(synth_fn=fn)` sets `_is_synthesizable=True`, `_synth_fn=fn`
- `HwParam[int]` annotation is detectable via `typing.get_origin(hint) is HwParam`
- `SynthContext.from_component` correctly extracts `HwParam` fields and builds `params` dict
- `SynthContext.cpp_param` returns `'IN_BW'` for a `HwParam` field and `repr(value)` for a `ClassVar`
- `HwComponent` can be instantiated with a `Simulation` (same as `Component`)
