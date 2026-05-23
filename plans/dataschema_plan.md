# Refactor Plan: Harmonize `SchemaArray` and `DataArray`

## 1) Problem statement and current state

`pysilicon` currently has two array concepts:

- **`DataArray`** (`pysilicon/hw/dataschema.py`) is a `DataSchema` subclass with class-level specialization (`element_type`, `max_shape`, `static`, `member_name`), bitwidth/dependency APIs, serialization/deserialization, and C++ helper generation.
- **`SchemaArray`** (`pysilicon/hw/arrayutils.py`) is a runtime/typing container with instance-level `elem_type` and NumPy-backed data, used by `write_array`/`read_array`, stream convenience APIs, and memory helpers.

The synthesis typing path is also split:

- `pysilicon/build/hwresolve.py` uses a placeholder typing marker for pipelined stream get:
  - `stmt.outputs[0].typ = ('SchemaArray', stmt.inputs[0])`
- `pysilicon/build/hwgen.py::cpp_type()` lowers that tuple to:
  - `"<elem>[MAX_N] /* TODO: real SchemaArray typing */"`

This creates duplicate semantics and ad-hoc typing for arrays despite `DataArray` already being the full schema model.

## 2) Why directly converting the current `SchemaArray` class to `DataSchema` is not clean

A direct inheritance change (`class SchemaArray(DataSchema)`) is not a clean fit because:

1. **Metadata location mismatch**
   - `DataSchema`/`DataArray` are class-driven.
   - `SchemaArray` stores `elem_type` on each instance (`SchemaArray.__init__`).

2. **Shape/model mismatch**
   - `DataArray` relies on specialized class shape (`max_shape`) and `static` behavior.
   - `SchemaArray` shape is implicit from runtime `data.shape`.

3. **Behavioral mismatch**
   - `DataSchema` expects class APIs (`get_bitwidth`, `nwords_per_inst`, dependency/include behavior).
   - `SchemaArray` is currently just a container + typing shim.

4. **Current call chain already normalizes to `DataArray`**
   - `arrayutils.write_array()` specializes `DataArray` internally before serializing.
   - So making current `SchemaArray` a full schema class would duplicate what `DataArray` already does.

## 3) Recommended target architecture

Use **`DataArray` as the single semantic array schema** and treat `SchemaArray` as a **compatibility facade** during migration.

### Core target decisions

- Keep array semantics unified in `DataArray` / `DataSchema`.
- Replace tuple placeholder typing (`('SchemaArray', elem)`) with a real schema-class type signal where possible.
- Support multiple C++ lowerings from one logical array schema:
  - wrapped/named type when needed,
  - raw inline array form where legal/performance-friendly.

This preserves `DataSchema` consistency while still allowing C++ raw-array style codegen when needed.

## 4) Phased implementation plan (repo-specific)

### Phase 0 — Inventory and guardrails

- Confirm/record all `SchemaArray` touchpoints:
  - `pysilicon/hw/arrayutils.py`
  - `pysilicon/hw/interface.py` (`get`, `get_pipelined`, `write`, `write_pipelined`)
  - `pysilicon/hw/memory.py` (`write_array`, `read_array`, `as_array`)
  - `pysilicon/build/hwresolve.py` and `pysilicon/build/hwgen.py::cpp_type`
- Add migration notes in docstrings where the public API currently promises `SchemaArray`.

### Phase 1 — Introduce a schema-native array typing marker in resolve/codegen

- In `pysilicon/build/hwresolve.py`:
  - Replace the pipelined placeholder tuple assignment with a typed representation tied to `DataArray` semantics (e.g., a specialized `DataArray` type, or a temporary dedicated marker object that carries `elem_type` + count semantics and is explicitly translatable).
- In `pysilicon/build/hwgen.py`:
  - Update `cpp_type()` to handle the new typed representation.
  - Keep tuple handling temporarily for backward compatibility; mark deprecated.

### Phase 2 — Move runtime serialization helpers to schema-first interfaces

- In `pysilicon/hw/arrayutils.py`:
  - Add/standardize helper(s) that construct specialized `DataArray` classes/instances from `(elem_type, shape, value)`.
  - Ensure `write_array` and `read_array` are explicitly thin wrappers over those helpers.
  - Keep `SchemaArray` accepted/returned initially, but make conversion points explicit and centralized.

### Phase 3 — Normalize stream and memory call sites around unified semantics

- In `pysilicon/hw/interface.py`:
  - Ensure typed get/write paths operate on schema-native array values without double-wrapping.
  - Maintain backward compatibility for existing callers that pass/expect `SchemaArray`.
- In `pysilicon/hw/memory.py`:
  - Keep helper ergonomics (`write_array`, `read_array`, `as_array`) but route through the same centralized conversion path used in `arrayutils`.
  - Preserve behavior for inline-memory “view” workflows while aligning types with schema-native representation.

### Phase 4 — Add `DataArray` lowering mode(s) for raw-array vs wrapped C++ emission

- In `pysilicon/hw/dataschema.py` (`DataArray`):
  - Add specialization/config knobs for C++ storage/lowering mode (naming TBD).
  - Keep existing wrapped behavior as default for compatibility.
  - Add codegen branches where raw array emission is legal (arguments/locals/fields as appropriate).
- In `pysilicon/build/*` codegen call sites:
  - Propagate/consume the lowering mode consistently.

### Phase 5 — Deprecation and compatibility tightening

- Deprecate direct reliance on tuple placeholder typing.
- Keep `SchemaArray` as an alias/facade for at least one migration cycle.
- Later, make `SchemaArray` minimal (constructor + adapters) or alias-only, depending on downstream breakage.

## 5) Risks and compatibility concerns

1. **Python typing surface**
   - Existing annotations use `SchemaArray[T]` in docs/examples and may be relied on by users/tools.
   - Need a migration path that does not abruptly remove this annotation shape.

2. **Runtime behavior compatibility**
   - Stream/memory helper return types are user-visible.
   - Any change must preserve practical behavior (`len`, indexing, NumPy conversion expectations) during transition.

3. **Serialization stability**
   - `write_array` / `read_array` currently route through `DataArray`; refactor must preserve exact packed word behavior.
   - Ensure `word_bw`, shape validation, and dtype behavior remain unchanged.

4. **C++ raw-array semantics**
   - Raw arrays are not interchangeable with nominal types in all contexts.
   - Must define where inline/raw lowering is legal and keep wrapped lowering where a named type is required.

5. **Codegen compatibility**
   - `cpp_type` currently has explicit tuple handling and tests for TODO behavior.
   - Transition requires parallel support period to avoid breaking kernel generation paths.

## 6) Suggested tests to add/update

Primary targets:

- `tests/hw/test_hwgen.py`
  - Extend/replace placeholder tuple typing expectations with new schema-native typing path.
  - Keep temporary regression coverage for legacy tuple support during migration.

- `tests/hw/test_arrayutils.py`
  - Add explicit coverage that schema-first helpers and legacy `SchemaArray` wrappers produce identical packed/unpacked results.

- `tests/hw/test_interface.py` and/or `tests/hw/test_schema_transfer_interface.py`
  - Validate typed stream get/write paths for array payloads in both compatibility and schema-native forms.

- `tests/hw/test_memory.py` / `tests/hw/test_aximm_interface.py`
  - Validate memory read/write array helper compatibility and return/acceptance behavior across transition.

- `tests/hw/test_dataschema.py` (and vitis variants if needed)
  - Add coverage for any new `DataArray` C++ lowering mode branches.

## 7) Recommendation on `SchemaArray` during migration

**Recommendation: preserve `SchemaArray` as a compatibility facade/alias during migration.**

Concretely:

- Keep it importable from `pysilicon.hw.arrayutils`.
- Keep it accepted by stream/memory/array helpers.
- Internally normalize quickly to schema-native `DataArray` semantics.
- Emit deprecation guidance only after new typing/lowering paths are stable and tested.

This gives minimal user disruption while converging the architecture on a single array schema model.
