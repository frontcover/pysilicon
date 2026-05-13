# Memory and Typed Storage Plan

_Status: draft — delete when feature ships_

---

## 1. What exists today

| Concept | Class | File |
|---|---|---|
| Sparse memory model | `Memory` | `pysilicon/hw/memory.py` |
| Address unit | `AddrUnit` (byte, word) | `pysilicon/hw/memory.py` |
| MM slave endpoint | `MMIFSlave(InterfaceEndpoint)` | `pysilicon/hw/aximm.py` |
| MM master endpoint | `MMIFMaster(InterfaceEndpoint)` | `pysilicon/hw/aximm.py` |
| Point-to-point MM | `DirectMMIF(QueuedTransferIF)` | `pysilicon/hw/aximm.py` |
| Multi-master×slave MM | `AXIMMCrossBarIF(QueuedTransferIF)` | `pysilicon/hw/aximm.py` |
| Address range | `AXIMMAddressRange` | `pysilicon/hw/aximm.py` |

### `Memory`

Sparse model for large memories. Core methods:

```python
mem = Memory(word_size=4, addr_size=32, nwords_tot=2**20)
addr = mem.alloc(nwords=256)        # → base address (int)
data = mem.read(addr, nwords=256)   # → np.ndarray
mem.write(addr, data)
mem.free(addr)
```

`Memory` has no interface — it is a plain Python object, not a `SimObj` or `Component`. It has no knowledge of how it is connected to other components or of the AXI protocol.

### `MMIFSlave` / `MMIFMaster`

`MMIFSlave` is an `InterfaceEndpoint` with `rx_write_proc` and `rx_read_proc` callbacks and a `latency_per_word` model. `MMIFMaster` provides typed convenience methods: `write()`, `read()`, `write_schema()`, `read_schema()`, `write_array()`, `read_array()`. Neither is a `Component` — they are endpoints that must be owned by one.

`MMIFMaster` will gain three additional **reference** methods (§4) that return views rather than copies: `as_words()`, `as_array(elem_type)`, `as_schema(schema_type)`.

---

## 2. Goal

Two things are needed, in order of dependency:

1. **`MemComponent`** — a `Component` that wraps a `Memory` and exposes an `MMIFSlave` endpoint. This is the system-level model of a memory resource (DDR bank, BRAM block, shared buffer).

2. **Local storage for compute hooks** — a lightweight way to declare internal buffers inside an `HwComponent` that map to HLS local arrays (not AXI-MM ports). Separate from `MemComponent`, which is always an external resource.

---

## 3. `MemComponent`

### 3.1 Role

`MemComponent` is a `Component` that models a memory resource in the system. It exposes **two endpoints**:

- `m_mm: MMIFMaster` — the endpoint passed to hooks and sub-component users. Backed directly by `self._mem`; all `MMIFMaster` methods (read/write/as_array etc.) operate on the internal `Memory` with no AXI protocol overhead.
- `s_mm: MMIFSlave` — exposed for external components to connect to this `MemComponent` via `DirectMMIF` or `AXIMMCrossBarIF` (the DDR / shared BRAM use case).

```python
@dataclass
class MemComponent(Component):
    word_size:   int = 4          # bytes per word
    addr_size:   int = 32         # address bits
    nwords_tot:  int = 2**20      # capacity

    def __post_init__(self) -> None:
        super().__post_init__()
        self._mem  = Memory(self.word_size, self.addr_size, self.nwords_tot)
        self.m_mm  = MMIFMaster(
            name=f'{self.name}_m_mm', sim=self.sim,
            mem=self._mem,           # direct backing — no AXI round-trip
        )
        self.s_mm  = MMIFSlave(
            name=f'{self.name}_s_mm', sim=self.sim,
            rx_write_proc=self._on_write,
            rx_read_proc=self._on_read,
        )
        self.add_endpoint(self.m_mm)
        self.add_endpoint(self.s_mm)
```

`_on_write` / `_on_read` delegate to `self._mem` (for external callers via `s_mm`). `MemComponent` has no `run_proc` — it is purely reactive.

### 3.2 Synthesis mapping

`MemComponent` does not generate HLS code itself — it represents an external memory resource. In the build system:

- **Vitis backend**: maps to a DDR bank declaration in `connectivity.cfg` (`sp=kernel.port:DDR[0]`)
- **Vivado IPI backend**: maps to a Block RAM or MIG IP instance in the block design

The `HwComponent` that owns an `MMIFMaster` connected to a `MemComponent` gets an `m_axi` port pragma for that master.

### 3.3 Allocation helper

Components that need to carve up a shared `MemComponent` use `MemComponent.alloc()` / `free()` which delegate to the internal `Memory`:

```python
buf_addr = mem_comp.alloc(nwords=1024)
mem_comp.free(buf_addr)
```

This is a simulation-time helper only; codegen uses static buffer addresses from `SynthesisHints`.

---

## 4. `MMIFMaster` method table and hook calling convention

### 4.1 Uniform hook convention

Hooks receive **interface endpoints** and **`HwVar`s** as inputs; they produce **`HwVar`s** as outputs. `MMIFMaster` is an interface endpoint — whether it comes from a sub-component `MemComponent` (local) or from an external wired connection (DDR) is transparent to the hook signature. The distinction is expressed only by which methods are called.

```python
# External DDR — hook receives an MMIFMaster wired to the external MemComponent's s_mm
@compute_hook
def _load_weights(self, m_ext: MMIFMaster, addr: HwVar) -> HwVar:
    w = yield from m_ext.read_array(addr, count=256, elem_type=Float32)
    return w   # HwVar — a burst-copied local buffer

# Local BRAM — hook receives the sub-component MemComponent's m_mm directly
@compute_hook
def _apply_taps(self, m_taps: MMIFMaster) -> HwVar:
    t = m_taps.as_array(elem_type=Float32)
    return t   # HwVar — a direct reference, zero sim time
```

### 4.2 `MMIFMaster` method table

| Method | Semantics | Sim cost | HLS mapping |
|---|---|---|---|
| `read(addr, nwords)` | burst copy → raw `np.ndarray` | SimPy yield | `m_axi` burst read |
| `write(addr, data)` | burst copy ← raw `np.ndarray` | SimPy yield | `m_axi` burst write |
| `read_array(addr, count, elem_type)` | burst copy → typed array | SimPy yield | `m_axi` burst read |
| `write_array(addr, data, elem_type)` | burst copy ← typed array | SimPy yield | `m_axi` burst write |
| `read_schema(addr, schema_type)` | burst copy → schema instance | SimPy yield | `m_axi` burst read |
| `write_schema(addr, schema_inst)` | burst copy ← schema instance | SimPy yield | `m_axi` burst write |
| `as_words()` | reference → raw `np.ndarray` view | zero | local C array ref |
| `as_array(elem_type)` | reference → typed array view | zero | local C array ref |
| `as_schema(schema_type)` | reference → schema view | zero | local C array ref |

`as_*` methods are only valid on a `MemComponent.m_mm` (directly backed by `Memory`). Calling them on an externally-wired `MMIFMaster` raises `SynthesisError` at construction time.

### 4.3 Setup — local sub-component

```python
@dataclass
class PolyAccelComponent(HwComponent):
    def __post_init__(self) -> None:
        super().__post_init__()
        taps = MemComponent(name='taps', sim=self.sim, word_size=4, nwords_tot=64)
        self.add_sub_component('taps', taps)
        # taps.m_mm is passed to hooks that need local tap storage
```

`add_sub_component` registers the `MemComponent` in `sub_components`. No `DirectMMIF` is created — the hook receives `taps.m_mm` directly.

### 4.4 `inline` and codegen mapping

A `MemComponent` in `sub_components` carries `inline=True` (default). This tells the parent `HwComponent`'s codegen to emit a local C array + `BIND_STORAGE` / `ARRAY_PARTITION` pragmas rather than an `m_axi` port, and not to generate a separate Vitis function for the sub-component.

| `MMIFMaster` source | Methods used | HLS mapping | Typical resource |
|---|---|---|---|
| `HwComponent` own endpoint → external `MemComponent.s_mm` | `read_array`, `write_array`, … | `m_axi` port + burst | HBM / DDR |
| sub-component `MemComponent.m_mm` (`inline=True`) | `as_array`, `as_schema`, … | local C array + pragmas | BRAM / URAM / registers |

### 4.5 Generality

`sub_components` is not restricted to `MemComponent`. Future sub-component types fit the same pattern. Each exposes whatever endpoint(s) are appropriate; hooks always receive typed endpoints and `HwVar`s.

---

## 5. Identified gaps (not yet in repo)

| Gap | Needed for |
|---|---|
| `MemComponent` class with `m_mm: MMIFMaster` + `s_mm: MMIFSlave` + `inline` flag | memory resource model (local and external) |
| `MMIFMaster.as_words()`, `as_array(elem_type)`, `as_schema(schema_type)` | reference (zero-copy) access to directly-backed memory |
| `sub_components: dict[str, Component]` + `add_sub_component()` on `HwComponent` | local sub-component wiring |
| `MMSchemaReadStmt`, `MMSchemaWriteStmt` (owned by `MMIFMaster`) | `HwStmt` for `read_schema()` / `write_schema()` |
| `MMArrayReadStmt`, `MMArrayWriteStmt` (owned by `MMIFMaster`) | `HwStmt` for `read_array()` / `write_array()` |
| Export `MMIFSlave`, `MMIFMaster`, `DirectMMIF` from `pysilicon/hw/__init__.py` | public API hygiene (currently missing) |

The `MM*Stmt` classes live in `pysilicon/hw/aximm.py` alongside `MMIFMaster`, consistent with the endpoint-owns-its-`HwStmt` design: `MMIFMaster.read_schema(...)` returns an `MMSchemaReadStmt`, `MMIFMaster.read_array(...)` returns an `MMArrayReadStmt`, and so on — exactly as `StreamIFSlave.get()` returns a `StreamGetStmt`.

---

## 6. Open questions

1. **`MemComponent` as `HwComponent`?** `MemComponent` is reactive (callback-driven, no `run_proc`). Should it be a plain `Component` or an `HwComponent`? Probably plain `Component` — codegen does not generate HLS for it, only a connectivity declaration.

2. **Address management in synthesis**: simulation uses `MemComponent.alloc()` for dynamic allocation. Synthesis needs static addresses. Where do static addresses live — in `SynthesisHints`, in a connectivity config, or derived from a separate placement pass?

3. **`LocalArray` shape constraints**: HLS requires statically-known shapes for local arrays. Should `LocalArray` enforce `static=True` and a fixed `max_shape` at construction, or defer the check to codegen? Enforce at construction — same reason `SynthesisError` is strict.

4. **Shared `MemComponent` across components**: if two `HwComponent`s share one `MemComponent` (e.g. producer writes, consumer reads), the AXI-MM crossbar handles arbitration in hardware. In simulation, `Memory.read/write` is not SimPy-aware — concurrent access needs serialization. Does `MemComponent` need to serialize access via a SimPy `Resource`?

---

## 7. Phased plan

| Phase | Deliverable | Dependency |
|---|---|---|
| 1 | `MemComponent` wrapping existing `Memory` + `MMIFSlave` | existing `Memory`, `MMIFSlave` |
| 2 | `sub_components` dict + `add_sub_component()` on `HwComponent`; internal `DirectMMIF` wiring | Phase 1, `hw_component_plan` Phase 1 |
| 3 | `MM*Stmt` classes on `MMIFMaster` (`MMSchemaReadStmt`, `MMArrayReadStmt`, etc.) | `hw_component_plan` Phase 2 (core `HwStmt`) |
| 4 | Codegen: `m_axi` pragma for system-level `MemComponent`; local array + pragmas for `sub_components` `MemComponent` | Phase 3, `hw_component_plan` Phase 4 |
| 5 | Address placement pass for synthesis (static addresses from allocation plan) | Phase 4 |
