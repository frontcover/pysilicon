# Register Map Plan

## Motivation

The poly accelerator's current error-handling model emits an in-band response footer carrying an error code. This is fragile: the errors that the kernel reports are typically AXI-Stream framing failures, and once framing is broken the same stream cannot reliably carry an error report. The replacement is the standard Vitis-style **halt-on-error** model: the kernel exposes an AXI-Lite control/status register block, halts on error by setting a status field and returning, and the host re-launches it after clearing the error. To support this cleanly we need a `RegMap` abstraction in PySilicon — a Python-declarative register map that drives both the SimPy simulation and (eventually) HLS pragma generation, host driver-class generation, and design-doc artifacts.

## Design specification

The full API contract is documented in [docs/guide/interface/regmap.md](../docs/guide/interface/regmap.md). That document is the source of truth — when implementation and doc disagree, fix the implementation. Read the doc end-to-end before writing any code; in particular:

- [Quick example](../docs/guide/interface/regmap.md#quick-example) shows the generic shape.
- [RegField](../docs/guide/interface/regmap.md#regfield), [RegAccess](../docs/guide/interface/regmap.md#regaccess), [RegMap](../docs/guide/interface/regmap.md#regmap) define the generic API.
- [Composite fields](../docs/guide/interface/regmap.md#composite-fields) and [Hooks](../docs/guide/interface/regmap.md#hooks) define the per-word transaction model and the hook contract.
- [VitisRegMap](../docs/guide/interface/regmap.md#vitisregmap) and [VitisRegMapMMIFSlave](../docs/guide/interface/regmap.md#vitisregmapmmifslave) define the Vitis-convention layer. **v1 only** — see the [Planned (v2)](../docs/guide/interface/regmap.md#planned-vitisregmap-v2-control-register) section for what is *not* in scope.
- [Worked example](../docs/guide/interface/regmap.md#worked-example-poly-accelerator) is the target shape for the poly migration.

## Architecture

One new module, [pysilicon/hw/regmap.py](../pysilicon/hw/regmap.py), exporting:

| Symbol                  | Kind     | Notes                                                       |
|-------------------------|----------|-------------------------------------------------------------|
| `RegAccess`             | Enum     | `R`, `W`, `RW`, `W1C`, `W1S`                                |
| `RegField`              | Dataclass| `schema, access, description, on_write, on_read, offset`    |
| `RegMap`                | Class    | Owns offset table and per-field word buffers                |
| `RegMapAccessError`     | Exception| Raised on access-mode violation, offset miss, etc.          |
| `RegMapMMIFSlave`       | Subclass of `MMIFSlave` | Wires `rx_*_proc` to `RegMap` dispatch         |
| `VitisRegMap`           | Subclass of `RegMap` | Auto-prepends `ap_start` (W1S) at offset `0x00`   |
| `VitisRegMapMMIFSlave`  | Subclass of `RegMapMMIFSlave` | Owns `on_start` launch lifecycle         |

No changes to existing files in `pysilicon/hw/` are required for the core implementation. [pysilicon/hw/memif.py](../pysilicon/hw/memif.py) is the dependency (we subclass `MMIFSlave`).

## Design decisions (already made — do not re-litigate)

These were settled in the design conversation. They appear in the doc and in this plan as facts; do not propose alternatives in the implementation PR.

1. **Field schemas may be any `DataSchema`.** Scalars (`IntField`, `EnumField`, `FloatField`), `DataList`s, and `DataArray`s are all valid. Implementation uses each schema's `nwords_per_inst(bus_bw)`, `serialize`, and `deserialize` methods. No special-casing per schema type.
2. **Backing store is per-field word buffers.** Each field owns a `numpy` array of length `nwords_per_inst(bus_bw)`. Host bus reads/writes touch the buffer at the appropriate sub-word offset; owner-side `get(name)` deserializes the buffer; `set(name, value)` serializes and stores.
3. **Hooks fire per host bus transaction (per word for LITE).** Signature `on_write(name, sub_word, word_value)` and `on_read(name, sub_word, word_value)`. Hooks must not yield. AXI-Lite has no "field write complete" boundary; consumers needing that semantic track it themselves.
4. **`on_write` fires after the backing store update (after W1C masking) and before the W1S auto-clear.** Hooks reading `regmap.get(name)` see the just-written value.
5. **W1C / W1S are restricted to single-word scalar fields.** `nwords_per_inst(bus_bw) > 1` with `access in {W1C, W1S}` raises `ValueError` at `RegMap` construction.
6. **W1S auto-clears immediately after the hook returns.** Sequence: backing word ← 1 → fire hook → backing word ← 0. Subsequent host reads return 0.
7. **Owner-side `regmap.set()` does NOT apply W1C/W1S semantics.** It overwrites the backing store directly. This matches "kernel sets its sticky status bit on event."
8. **Manual `RegField.offset` override is allowed.** Auto-placed fields fill gaps in declaration order around manually-placed fields. Overlap raises `ValueError`.
9. **`VitisRegMap` v1 prepends only `ap_start` (W1S) at offset `0x00`, occupying the entire word.** Bit-packed control + COR + auto_restart + IER/ISR/GIE are deferred to v2 and explicitly out of scope here.
10. **`VitisRegMap` rejects user-declared fields whose names start with `ap_`.** Reserved namespace; raises `ValueError` at construction.
11. **`VitisRegMapMMIFSlave` owns the launch lifecycle.** When the host writes `ap_start = 1`, the slave spawns `env.process(on_start())`. If `on_start` is already running (a previous launch hasn't returned), the write is silently ignored (mirrors Vitis `ap_ctrl_hs` gating by `ap_idle`). The W1S auto-clear of `ap_start` still fires regardless.
12. **`on_start` does not return any meaningful value.** The slave does not interpret the return; it only tracks that the generator finished. Status fields (error, tx_id, halted, etc.) are set by the kernel via `regmap.set(...)` before `return`. The slave does not auto-set anything.
13. **Master-side `start()` lives on `VitisRegMap`, not on `MMIFMaster`.** Signature `start(master, base_addr=0) -> ProcessGen[None]`. Internally writes `1` to `base_addr + offset_of("ap_start")`.
14. **No `RegMap.copy()` in v1.** Per-instance regmaps are constructed in `__post_init__` directly. (The doc was updated to remove the earlier `copy()` pattern.)

## Implementation phases

Each phase produces a self-contained increment that should compile and pass its own tests. Run `pytest tests/hw/test_regmap.py` after each phase to confirm progress.

### Phase 1 — Generic `RegMap` infrastructure

**Files:**
- Create [pysilicon/hw/regmap.py](../pysilicon/hw/regmap.py)
- Create [tests/hw/test_regmap.py](../tests/hw/test_regmap.py)

**Implement:**
- `RegAccess` enum with members `R, W, RW, W1C, W1S`.
- `RegMapAccessError(RuntimeError)`.
- `RegField` dataclass: `schema: type[DataSchema]`, `access: RegAccess`, `description: str = ""`, `on_write: Callable | None = None`, `on_read: Callable | None = None`, `offset: int | None = None`.
- `RegMap`:
  - Constructor: `(fields: dict[str, RegField], bitwidth: int = 32)`.
  - At construction:
    - Validate W1C/W1S fields are single-word.
    - Compute offsets: place manually-offset fields first, then fill with auto-placed fields in declaration order. Bus-word alignment (`bitwidth/8` bytes). Detect overlaps.
    - Allocate `self._buffers: dict[str, np.ndarray]` of zeros, one per field.
  - `offset_of(name) -> int`, `nwords_of(name) -> int`, `total_size_bytes() -> int`.
  - `get(name) -> Any`: deserialize buffer via the field's schema and return the Python value. For schemas where `deserialize` returns the same type as `schema()`, that is what `get` returns.
  - `set(name, value)`: accept either a schema instance or a raw value (wrap raw as `schema(value)`), serialize with `bitwidth`, store into the buffer. Length validation.
  - `field_name_at_offset(byte_offset) -> tuple[str, int]`: returns `(field_name, sub_word_index)` or raises `RegMapAccessError`.
  - `read_word(name, sub_word) -> int`: returns one word from the field's buffer.
  - `write_word(name, sub_word, value, *, source: Literal["host", "owner"])`: writes one word, applying W1C masking when `source == "host"`. Returns the post-write value (for hooks).
- `RegMapMMIFSlave(MMIFSlave)`:
  - Constructor takes `regmap: RegMap` and wires `rx_write_proc=self._rx_write` / `rx_read_proc=self._rx_read`.
  - `_rx_write(words, local_addr)`: for each word, decode `local_addr → (field, sub_word)`, validate access mode (raise on R-only host write), update the buffer, fire `on_write(name, sub_word, written_value)` hook, then auto-clear if W1S.
  - `_rx_read(nwords, local_addr)`: for each word, decode, validate access mode (raise on W-only host read), read the buffer, fire `on_read` hook, accumulate. Returns numpy array.
  - For multi-word transactions (FULL crossbar), iterate per-word. For LITE, the crossbar already calls per-word.

**Tests** (under `tests/hw/test_regmap.py` — pure-Python, no SimPy required for most of them):
- `test_offset_assignment_auto`: scalar fields, multi-word fields, mixed.
- `test_offset_assignment_manual`: explicit offsets, gaps filled by auto, overlap raises.
- `test_w1cs_validation_rejects_multiword`: `RegField(CoeffArray, W1S)` → `ValueError`.
- `test_get_set_scalar`: round-trip int / enum / float.
- `test_get_set_composite`: round-trip a `DataList` and a `DataArray`.
- `test_set_with_raw_value_wraps_via_schema`: `regmap.set("error", 3)` wraps via `EnumField(3)`.
- `test_field_name_at_offset`: hits, misses, sub_word indexing for multi-word fields.
- For the slave, use a small SimPy harness (a `MMIFMaster`-bound CPU `SimObj` writing/reading via a `DirectMMIF`):
  - `test_slave_round_trip_per_mode`: R/W/RW round-trip.
  - `test_slave_w1c`: write `0xF0` to a register holding `0xFF` → resulting value `0x0F`.
  - `test_slave_w1s_auto_clears`: write `1`, hook sees `1`, immediate read returns `0`.
  - `test_slave_rejects_host_write_to_r`: raises `RegMapAccessError`.
  - `test_slave_rejects_host_read_from_w`: raises `RegMapAccessError`.
  - `test_hook_ordering_write_after_w1c_before_w1s_clear`.

### Phase 2 — Vitis layer

**Files:** extend [pysilicon/hw/regmap.py](../pysilicon/hw/regmap.py), extend [tests/hw/test_regmap.py](../tests/hw/test_regmap.py).

**Implement:**
- `VitisRegMap(RegMap)`:
  - Constructor: `(fields: dict[str, RegField], bitwidth: int = 32)`.
  - Build a control field dict `{"ap_start": RegField(Bit, RegAccess.W1S, offset=0x00, description="Start the kernel (Vitis ap_ctrl_hs)")}` where `Bit = IntField.specialize(bitwidth=1, signed=False)`.
  - Reject any user field whose name starts with `ap_` (`ValueError`).
  - Reject any user field with `offset == 0` (collision with auto-prepended ap_start).
  - Call `super().__init__({**ctrl, **fields}, bitwidth=bitwidth)`.
  - `start(master: MMIFMaster, base_addr: int = 0) -> ProcessGen[None]`: writes `1` to `base_addr + offset_of("ap_start")` via `master.write_schema(Bit(1), addr=...)`.
- `VitisRegMapMMIFSlave(RegMapMMIFSlave)`:
  - Constructor: `(regmap: VitisRegMap, on_start: Callable[[], ProcessGen[None]] | None = None, ...)`.
  - On construction, install an internal `on_write` hook on the `ap_start` field that:
    - Checks `self._busy`. If True, does nothing (silently ignored).
    - Else sets `self._busy = True`, spawns `self.env.process(self._launch())`.
  - `_launch()`: `try: yield from on_start()` then `finally: self._busy = False`. If `on_start` is `None`, immediately returns.
  - If the user supplied an `on_write` hook on the `ap_start` field, raise — that field is reserved.
  - Note: `VitisRegMap` lets users add their own hooks to any non-`ap_` field. Only `ap_start`'s hook is reserved.

**Tests** (extend `test_regmap.py`):
- `test_vitis_regmap_prepends_ap_start_at_zero`: offset_of("ap_start") == 0; user fields start at 0x04.
- `test_vitis_regmap_rejects_ap_prefix`: `VitisRegMap({"ap_done": ...})` → `ValueError`.
- `test_vitis_regmap_rejects_offset_zero_collision`.
- `test_vitis_regmap_start_writes_one`: a CPU SimObj calls `regmap.start(cpu_master, base_addr=BASE)`; verify a one-word write of `1` lands at `BASE`.
- `test_vitis_slave_invokes_on_start_on_ap_start`: hook fires once after host writes `1` to ap_start; on_start generator runs to completion.
- `test_vitis_slave_drops_concurrent_ap_start`: while on_start is yielding (use a `self.env.timeout(...)` to hold it), host writes ap_start a second time; verify the second invocation does not spawn a new process. ap_start still auto-clears.
- `test_vitis_slave_relaunches_after_return`: on_start returns; second host ap_start launches a fresh invocation.
- `test_vitis_slave_status_set_inside_on_start_visible_to_host`: kernel sets `regmap.set("error", 3)` inside on_start, returns; host reads error and gets 3.

### Phase 3 — Demo

**File:** create [examples/interface/regmap_demo.py](../examples/interface/regmap_demo.py).

Modeled on [examples/interface/aximm_demo.py](../examples/interface/aximm_demo.py) — same structure (slave SimObj + master SimObj + harness with assertions, runnable as `python -m examples.interface.regmap_demo`).

**Scenario:**

A small "fake accelerator" SimObj using `VitisRegMap` + `VitisRegMapMMIFSlave`. Register fields:
- `status_clear` (W1C) — host writes 1, clears halted/error.
- `halted` (R) — kernel writes, host reads.
- `error` (R, `EnumField` over a small `DemoError` enum: `OK`, `BAD_INPUT`, `OVERFLOW`).
- `coeff_pair` (RW, a 2-element `DataArray` of `IntField(bitwidth=32)` to demonstrate composite fields).

The "kernel" `on_start` does the following deterministic dance (no AXI-Stream needed for the demo — the point is the regmap, not the data path):
1. Sleep for 5 cycles.
2. Read `coeff_pair`; if both are zero, it's "BAD_INPUT": set `error` and `halted`, return.
3. Else compute `sum = coeffs[0] + coeffs[1]`; if `sum > 1000`, "OVERFLOW".
4. Else "succeed" — set `error = OK`, leave `halted = 0`, return normally.

CPU SimObj sequence:
1. Write `coeff_pair = [0, 0]`, launch via `regmap.start(cpu, base_addr=BASE)`, wait, read `halted` → 1, read `error` → BAD_INPUT.
2. Write `status_clear = 1`, write `coeff_pair = [600, 700]` (overflow), re-launch, verify OVERFLOW.
3. Write `status_clear = 1`, write `coeff_pair = [10, 20]`, re-launch, verify halted == 0 and error == OK.
4. Test access-mode enforcement: try to write `halted` directly from the host, verify `RegMapAccessError`.

Wire via `AXIMMCrossBarIF` with `protocol=AXIMMProtocol.LITE` to demonstrate composition with the existing transport.

Demo should print a clean transcript at each step and assert all expected register values. Exit code 0 = pass.

### Phase 4 — Doc + index sanity check

**Files touched:** none new; verify [docs/guide/interface/index.md](../docs/guide/interface/index.md) lists `regmap.md`.

Read the implemented module and confirm:
- All public symbols in [regmap.md](../docs/guide/interface/regmap.md) exist with the documented signatures.
- Quick-reference table at the bottom of regmap.md is accurate.
- The worked-example code in regmap.md type-checks against the implemented API (it doesn't have to be runnable since it depends on the unmigrated poly, but the imports and call shapes must match).

### Phase 5 — Poly migration (Python side, separate PR)

**Out of scope for the core regmap PR.** This phase is documented here so the migration can follow as its own PR once Phases 1–4 land. Do not bundle it.

The migration touches:
- [examples/poly/poly.py](../examples/poly/poly.py): replace the `PolyRespFtr`-based error reporting with a `VitisRegMap` per the [worked example](../docs/guide/interface/regmap.md#worked-example-poly-accelerator). Drop `run_proc`; add `on_start`. Drop the resp_ftr.bin output. Update the `PolyTB` testbench to write `ap_start` before sending stream data, and to poll `halted`/`error` instead of reading a footer.
- [examples/poly/poly.py](../examples/poly/poly.py): update `PolySimResult` to drop the `resp_ftr` field and add a `regmap_status: PolyStatus` snapshot read from the regmap at end-of-simulation.
- The `PolyError` enum stays (it's the schema for the `error` field).
- C++ side ([examples/poly/poly.cpp](../examples/poly/poly.cpp), [examples/poly/poly.hpp](../examples/poly/poly.hpp)): add `s_axilite` control/status interface, restructure `poly()` into a persistent `while(true)` with halt-on-error returning the function, drop the `PolyRespFtr` write path. **This requires Vitis-in-the-loop verification and is best handled in person, not by a background agent.**
- [examples/poly/poly_build.py](../examples/poly/poly_build.py), [examples/poly/poly_tb.cpp](../examples/poly/poly_tb.cpp), and the `tests/examples/test_poly_*` files all need corresponding updates.

A separate plan file (`plans/poly_regmap_migration.md`) should be written before starting this phase.

## Acceptance criteria for the core PR (Phases 1–4)

- `pytest tests/hw/test_regmap.py` passes (~25 tests).
- `python -m examples.interface.regmap_demo` exits 0 with the expected transcript.
- `mypy pysilicon/hw/regmap.py` passes with no errors.
- `ruff check pysilicon/hw/regmap.py examples/interface/regmap_demo.py tests/hw/test_regmap.py` passes.
- No changes to existing files in `pysilicon/hw/` other than possible imports/exports in [pysilicon/hw/__init__.py](../pysilicon/hw/__init__.py) if there's a top-level export convention.
- The doc and the implementation agree on all public signatures.
- No part of the v2 API (COR access mode, bit-packed control fields, GIE/IER/ISR, artifact generators, `RegMap.copy()`) is implemented.

## Open questions

These are flagged because they depend on details of `DataSchema` that may not be obvious from the doc. If the implementation hits ambiguity, choose the option in **bold** and add a code comment explaining the choice.

1. **`RegMap.get()` return type for scalar fields.** `IntField(5).deserialize([5])` returns `IntField(5)`, not the raw `int`. Should `regmap.get("error")` return an `IntField` instance or unwrap to the underlying value? **Return the schema instance** (matches the rest of PySilicon's API conventions). Callers that want the raw value can do `int(field)` or `.value` per schema.
2. **What does `EnumField.specialize(...)` do under the hood, and is `EnumField(3)` accepted as input for `set()`?** Read [pysilicon/hw/dataschema.py](../pysilicon/hw/dataschema.py) before implementing — if `EnumField` requires the enum member rather than the int, `set(name, raw_int)` should wrap as `schema(IntEnumType(raw_int))` for enum fields specifically. **Inspect the EnumField API and match its accepted input types.**
3. **`bitwidth` of the `RegMap` vs the `MMIFSlave`.** Both default to 32. If they differ, the slave's bus width wins for transaction sizing. **Validate they match at slave construction; raise if not.**
4. **`Bit` schema.** No `Bit` class exists in [dataschema.py](../pysilicon/hw/dataschema.py). v1 uses the alias `Bit = IntField.specialize(bitwidth=1, signed=False)`. **Define this alias as a module-level constant in `pysilicon/hw/regmap.py`** and re-export it; the worked example and Quick example both rely on it. Optionally add a docstring noting that this could become a real class in the future.

## Out of scope (deferred to future PRs)

- Full bit-packed Vitis control register (ap_done / ap_idle / ap_ready / auto_restart at offset 0x00) — see [Planned (v2)](../docs/guide/interface/regmap.md#planned-vitisregmap-v2-control-register).
- `RegAccess.COR` (clear-on-read).
- GIE / IER / ISR interrupt registers.
- Artifact generators: `to_markdown()`, `to_c_header()`, `to_python_driver()` — see [Planned (v2)](../docs/guide/interface/regmap.md#planned-artifact-generation-v2).
- Poly migration (Phase 5 above).
- HLS pragma generation from a `VitisRegMap`.
- `RegMap.copy()` and any cross-instance sharing of `RegMap` instances.

## Suggested commit structure

One PR per phase, in order:

1. `phase 1: add generic RegMap, RegMapMMIFSlave, and tests`
2. `phase 2: add VitisRegMap + VitisRegMapMMIFSlave with on_start lifecycle`
3. `phase 3: add examples/interface/regmap_demo.py`
4. `phase 4: doc/impl reconciliation pass`

Phases 1+2 may be combined if reviewer prefers. Phase 3 must come after 2 (it depends on `VitisRegMap`). Phase 4 is a small cleanup; it can be folded into 3.
