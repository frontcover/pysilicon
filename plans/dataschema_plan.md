# Plan: Unify `SchemaArray` into `DataArray` + Forbid Pipelined Ops in Extracted Bodies

## Goal

Three coupled changes that together resolve the array-typing tangle currently blocking Phase 12 of the synthesis pipeline:

1. **Establish the rule:** pipelined stream ops (`get_pipelined`, `write_pipelined`) are only legal inside `@synthesizable` user hook bodies. The extractor rejects them anywhere else with a clear `SynthesisError`. This pushes the "fused-loop codegen" problem out of the framework and into hand-written hook C++, where the user expresses it directly using `float32_array_utils::*` and `#pragma HLS PIPELINE`.
2. **Unify arrays around `DataArray`.** Delete `SchemaArray` (per the "no backward compatibility" guidance). Migrate every caller — `arrayutils.py`, `interface.py`, `memory.py` — to construct `DataArray` instances directly. Provide a small `array(elem_type, data)` factory for the common ergonomic case.
3. **`DataArray` gains a `cpp_storage` lowering mode.** Default `"struct"` (current behavior — emits as a wrapped C++ struct). Optional `"raw"` (emits as a raw C array `<elem>[<count>]` at function-argument position). Schemas declare their own intended C++ shape; codegen doesn't introspect subclass identity.

After this plan, poly's `coeffs` regmap field can be declared with `cpp_storage="raw"` and the generated kernel signature gets `const float coeffs[4]` instead of `CoeffArray& coeffs`. Blocker 4 from `experiment/poly_codegen_notes.md` dissolves.

## What this plan does NOT do

- Codegen for pipelined patterns in extracted bodies. The rule explicitly forbids that case; AI-assisted synthesis hint extraction from pipelined ops (the long-term direction) is out of scope for this plan.
- Phase 12a's poly-codegen fixes (Blockers 1 and 2 — schema-include walking + utility-include discovery). Those are a separate plan that lands after this one.
- Phase 12b's poly swap-over. Still gated on this plan + Phase 12a.

## Already done (do NOT redo)

- `DataArray` exists and is the schema model for "static-shape array as a struct member" — see [pysilicon/hw/dataschema.py](../pysilicon/hw/dataschema.py).
- `DataArray.specialize(element_type=T, max_shape=(N,), static=True/False)` produces specialized subclasses.
- The DataArray runtime serialization path already routes through `pysilicon/hw/arrayutils.py::write_array/read_array`.
- Phases 1–11 of the synthesis pipeline (extractor, resolver, codegen, BuildStep, templating).

## Design decisions (already settled — do NOT re-litigate)

1. **Rule: pipelined ops live only in hook bodies.** Extractor raises if a `*.get_pipelined` or `*.write_pipelined` call appears in any extracted body (`on_start`, `run_proc`, or any other extracted method). Error message must clearly direct the user to "move this into a `@synthesizable` hook."
2. **No facade for `SchemaArray`.** The class is deleted outright. Callers update in the same commit as the deletion.
3. **`DataArray` gains `cpp_storage: ClassVar[str] = "struct"`.** Values: `"struct"` (default) or `"raw"`. Set on the class (via subclass override) or via `specialize(..., cpp_storage="raw")`. **Validation at class definition time:** any other value raises.
4. **`cpp_type()` dispatches on `DataArray.cpp_storage`, not on `isinstance(typ, DataArray)` alone.** Specifically: when `typ` is a `DataArray` subclass:
   - `cpp_storage == "struct"` → `typ.cpp_class_name()` (current behavior; the struct's name).
   - `cpp_storage == "raw"` → `f"{cpp_type(typ.element_type)}[{typ._declared_count()}]"`.
   The codegen reads schema-declared intent, not type identity. (Adds one branch keyed on a class attribute — qualitatively different from special-casing subclass identity.)
5. **Factory function `array(elem_type, data)`** lives in [pysilicon/hw/arrayutils.py](../pysilicon/hw/arrayutils.py). Replaces the common `SchemaArray(data=..., elem_type=...)` ergonomic pattern. Internally constructs a `DataArray.specialize(...)` and returns an instance.
6. **Dead code deletions:** `StreamGetPipelinedStmt` and `StreamWritePipelinedStmt` in `pysilicon/hw/interface.py` are deleted. The `('SchemaArray', elem_type)` branch in `pysilicon/build/hwgen.py::cpp_type` is deleted. The corresponding HwVar.typ population for pipelined gets in `pysilicon/build/hwresolve.py` is deleted (it was unreachable after the extractor rule but cleanup is in scope).
7. **AI synthesis hints from pipelined ops** is a future feature (mentioned in passing). The current plan deletes the dead representations; the future plan would re-introduce a different representation explicitly designed for hint extraction. Not in scope.

## Reference reading (read once before starting)

- [pysilicon/hw/dataschema.py](../pysilicon/hw/dataschema.py) — `DataSchema`, `DataArray`, `specialize()`. The `DataArray` class definition and its `_gen_*` methods.
- [pysilicon/hw/arrayutils.py](../pysilicon/hw/arrayutils.py) — `SchemaArray` (to be deleted), `write_array`, `read_array`, `ArrayUtilsStep`. The runtime-helper layer.
- [pysilicon/hw/interface.py](../pysilicon/hw/interface.py) — stream endpoint `get`, `get_pipelined`, `write`, `write_pipelined`. The `Stream*PipelinedStmt` classes to delete.
- [pysilicon/hw/memory.py](../pysilicon/hw/memory.py) — `write_array`, `read_array`, `as_array`. Memory-side callers of `SchemaArray`.
- [pysilicon/build/hwgen.py](../pysilicon/build/hwgen.py) — `cpp_type`. The branch that handles arrays.
- [pysilicon/build/hwresolve.py](../pysilicon/build/hwresolve.py) — the resolver that currently writes the `('SchemaArray', elem_type)` placeholder.
- [pysilicon/build/hwcodegen.py](../pysilicon/build/hwcodegen.py) — `HwStmtExtractor`. The new rule lives here.
- [examples/poly/poly.py](../examples/poly/poly.py) — `CoeffArray` and the `evaluate` hook. Phase 6 migration target.

## Working convention

- One commit per phase, in order. Push after each.
- Run `pytest tests/hw/ tests/build/ tests/examples/test_poly_demo.py -k "not vitis"` after every phase. All must stay green. (The 12 pre-existing `tests/build/test_build.py` failures are unrelated; ignore.)
- New tests in `tests/hw/test_arrayutils.py` (extend), `tests/hw/test_hwgen.py` (extend), `tests/hw/test_extract_poly.py` (extend), `tests/hw/test_dataschema.py` (extend for `cpp_storage`).

---

## Phase 1: Extractor forbids pipelined ops in extracted bodies

**Goal:** Walking an extracted body that calls `*.get_pipelined(...)` or `*.write_pipelined(...)` raises `SynthesisError` with a clear, actionable message.

**Changes:**

- In [pysilicon/build/hwcodegen.py](../pysilicon/build/hwcodegen.py), extend `_require_synthesizable` (or add a sibling check in `_visit_expr_stmt` / `_make_call_with_binding`):

  ```python
  _PIPELINED_OP_NAMES = frozenset({'get_pipelined', 'write_pipelined'})

  def _check_not_pipelined(self, method, node):
      if method is None:
          return
      method_name = getattr(method, '__name__', None)
      if method_name in _PIPELINED_OP_NAMES:
          lineno = getattr(node, 'lineno', '?')
          raise SynthesisError(
              f"Pipelined stream operation '{method_name}' at line {lineno} of "
              f"the extracted body. Pipelined ops are only legal inside "
              f"@synthesizable hook bodies (their C++ lowering requires "
              f"hand-written pipelined loops with #pragma HLS PIPELINE). "
              f"Refactor to call a hook that takes the stream as an argument "
              f"and does the pipelined I/O internally."
          )
  ```

  Invoke this from every call-resolution path (`_make_call_with_binding`, `_make_call_stmt_from_node`, `_visit_expr_stmt`'s direct-call branch).

**Tests** (extend `tests/hw/test_extract_poly.py` or new `tests/hw/test_extract_pipelined_forbidden.py`):

- Construct a minimal `HwComponent` whose `on_start` calls `self.s_in.get_pipelined(Float32, count=10)`. Run `extract_kernel(comp)`. Assert `SynthesisError` with a message containing `"Pipelined"` and `"hook"`.
- Same for `write_pipelined` in `on_start`.
- Same for a `run_proc`-based component (the rule applies to both extraction entry points).
- Negative test: `s_in.get(PolyCmdHdr)` (non-pipelined) extracts cleanly.
- Negative test: a hook calling `s_in.get_pipelined(...)` *inside its body* is fine because hook bodies are not extracted. (Construct a component where `on_start` calls the hook; assert extraction succeeds.)

**Commit:** `extractor: forbid pipelined stream ops in extracted bodies`

---

## Phase 2: Delete dead pipelined Stmt classes + placeholder typing

**Goal:** Remove the now-unreachable IR vocabulary and codegen branches.

**Changes:**

- In [pysilicon/hw/interface.py](../pysilicon/hw/interface.py), delete:
  - The `StreamGetPipelinedStmt` class.
  - The `StreamWritePipelinedStmt` class.
  - Remove the `@synthesizable(... stmt_class=Stream*PipelinedStmt)` decoration on `get_pipelined` / `write_pipelined`. The methods stay (they're still used by Python sim) — just remove the `@synthesizable` annotation entirely. They become regular Python methods, not extractor-recognized.

- In [pysilicon/build/hwgen.py](../pysilicon/build/hwgen.py), delete the placeholder branch in `cpp_type`:
  ```python
  # DELETE:
  if isinstance(typ, tuple) and len(typ) == 2 and typ[0] == 'SchemaArray':
      inner = cpp_type(typ[1])
      return f"{inner}[MAX_N] /* TODO: real SchemaArray typing */"
  ```

- In [pysilicon/build/hwresolve.py](../pysilicon/build/hwresolve.py), delete the `StreamGetPipelinedStmt` branch in `_populate_output_types`:
  ```python
  # DELETE:
  if isinstance(stmt, StreamGetPipelinedStmt) and stmt.outputs:
      stmt.outputs[0].typ = ('SchemaArray', stmt.inputs[0])
      return
  ```
  And remove the now-unused import.

- Grep the tree for any remaining references to `StreamGetPipelinedStmt`, `StreamWritePipelinedStmt`, or the `('SchemaArray', ` tuple-typing pattern. Delete them. Tests that exercised the placeholder typing get removed (not "updated to a different shape" — they tested dead code).

**Tests:**

- Remove tests that asserted the placeholder typing.
- Re-run the full test suite; all should pass with the deletions.

**Commit:** `interface/hwgen/hwresolve: delete dead pipelined Stmt classes + placeholder typing`

---

## Phase 3: `DataArray` gains `cpp_storage` lowering mode

**Goal:** A `DataArray` subclass can declare `cpp_storage = "raw"` to be emitted as a raw C array `<elem>[<count>]` at function-argument positions. Default remains `"struct"`.

**Changes:**

- In [pysilicon/hw/dataschema.py](../pysilicon/hw/dataschema.py), inside the `DataArray` class:

  ```python
  cpp_storage: ClassVar[str] = "struct"   # "struct" (default) or "raw"
  ```

  And inside the `_VALID_SPECIALIZE_KWARGS` (or wherever `validate_specialize_kwargs` enforces allowed kwargs):

  - Add `"cpp_storage"` to the allowed set.
  - In the validator: raise `ValueError` if the value is not `"struct"` or `"raw"`.

- Helper for codegen to extract the count:

  ```python
  @classmethod
  def _declared_count(cls) -> int:
      """Return the static declared count (product of max_shape)."""
      if not cls.max_shape:
          raise ValueError(f"{cls.__name__} has no max_shape; cannot lower as raw array.")
      n = 1
      for dim in cls.max_shape:
          n *= int(dim)
      return n
  ```

  Constraint: `cpp_storage="raw"` requires `static=True` and `len(max_shape) == 1`. Validate at class definition time (in `__init_subclass__` or the equivalent hook) and raise if violated.

- In [pysilicon/build/hwgen.py](../pysilicon/build/hwgen.py), modify `cpp_type` to dispatch on `cpp_storage`:

  ```python
  if isinstance(typ, type) and issubclass(typ, DataArray):
      storage = getattr(typ, 'cpp_storage', 'struct')
      if storage == 'raw':
          elem_cpp = cpp_type(typ.element_type)
          return f"{elem_cpp}[{typ._declared_count()}]"
      # fall through to struct emission below
  ```

  Note: this branch is keyed on a *class attribute* (intent declared by the schema), not on subclass identity. The check is specifically inside the existing `issubclass(typ, DataArray)` block.

- For kernel-signature emission of a raw-array argument, the C++ syntax is `<elem> <name>[<count>]`, not `<elem>[<count>] <name>`. Update `kernel_signature` to handle this when emitting regmap-field args:

  ```python
  for fname, fld in regmap_slave.regmap._fields.items():
      if fld.is_vitis_auto:
          continue
      schema = fld.schema
      if (isinstance(schema, type) and issubclass(schema, DataArray)
              and getattr(schema, 'cpp_storage', 'struct') == 'raw'):
          elem_cpp = cpp_type(schema.element_type)
          count = schema._declared_count()
          arg = f"    {elem_cpp} {fname}[{count}]"
      else:
          arg = f"    {cpp_type(schema)}& {fname}"
      arg_lines.append(arg)
      pragma_lines.append(...)
  ```

  Same adjustment if `cpp_storage="raw"` arrays appear as hook args (in `hook_signature`).

**Tests:**

- A `DataArray.specialize(element_type=Float32, max_shape=(4,), static=True, cpp_storage="raw")` class has `cls.cpp_storage == "raw"` and `cls._declared_count() == 4`.
- Invalid value: `specialize(..., cpp_storage="hybrid")` raises `ValueError`.
- Invalid shape: `specialize(..., cpp_storage="raw", static=False)` raises `ValueError`. Same for multi-dim `max_shape`.
- `cpp_type` on a `cpp_storage="struct"` DataArray returns the class name (unchanged).
- `cpp_type` on a `cpp_storage="raw"` DataArray returns `"float[4]"` (or similar; verify exact format).
- `kernel_signature` for a component whose regmap has a `cpp_storage="raw"` DataArray field emits `<elem> <name>[<count>]` (e.g., `float coeffs[4]`).

**Commit:** `dataschema/hwgen: DataArray cpp_storage="raw" emits as raw C array`

---

## Phase 4: Migrate `SchemaArray` callers to `DataArray` + add `array()` factory

**Goal:** Every caller of `SchemaArray` constructs `DataArray` instances instead. New factory `array(elem_type, data)` provides the ergonomic equivalent. `SchemaArray` is NOT yet deleted (Phase 5).

**Changes:**

- In [pysilicon/hw/arrayutils.py](../pysilicon/hw/arrayutils.py), add:

  ```python
  def array(elem_type, data, static: bool = False):
      """Construct a DataArray instance wrapping *data* with *elem_type* metadata.

      Replaces the common SchemaArray(data=..., elem_type=...) pattern.
      Internally specializes DataArray with the runtime shape and returns an instance.
      """
      import numpy as np
      arr = np.asarray(data)
      shape = arr.shape
      cls = DataArray.specialize(
          element_type=elem_type,
          max_shape=shape,
          static=static,
      )
      inst = cls()
      inst.val = arr   # or whatever the existing DataArray attribute is for the buffer
      return inst
  ```

  (Verify the exact attribute name DataArray uses for its backing data — `.val`, `.data`, etc. — and write the factory to match.)

- Walk every existing call site of `SchemaArray(...)` and replace with `array(elem_type, data)` or direct `DataArray.specialize(...)` calls. Files to touch:
  - [pysilicon/hw/arrayutils.py](../pysilicon/hw/arrayutils.py) — internal calls in `write_array` / `read_array` if any.
  - [pysilicon/hw/interface.py](../pysilicon/hw/interface.py) — `StreamIFSlave.get_pipelined` / `StreamIFMaster.write_pipelined` return/accept values.
  - [pysilicon/hw/memory.py](../pysilicon/hw/memory.py) — `write_array`, `read_array`, `as_array`.
  - [examples/poly/poly.py](../examples/poly/poly.py) — the `evaluate` hook does `SchemaArray(data=y, elem_type=Float32)` for the write_pipelined call.
  - Any test files that construct `SchemaArray` instances directly.

- The migration converts each call site to either:
  - `array(elem_type, np_data)` if the caller is constructing fresh.
  - `DataArray.specialize(element_type=T, max_shape=(N,), static=True)(initial_data)` if the caller wants explicit specialization control.

**Tests:**

- All pre-existing tests in `tests/hw/test_arrayutils.py`, `tests/hw/test_interface.py`, `tests/hw/test_memory.py`, `tests/examples/test_poly_demo.py` must still pass after migration. (They were passing with `SchemaArray`; the new constructions produce equivalent values.)
- Add `test_array_factory_round_trip`: construct via `array(Float32, [1.0, 2.0, 3.0])`, serialize via `write_array`, deserialize via `read_array`, assert equal.

**Commit:** `arrayutils: add array() factory; migrate SchemaArray callers to DataArray`

---

## Phase 5: Delete `SchemaArray`

**Goal:** The `SchemaArray` class is removed from the source tree. All imports gone.

**Changes:**

- In [pysilicon/hw/arrayutils.py](../pysilicon/hw/arrayutils.py), delete the `SchemaArray` class entirely.
- Grep the tree for `SchemaArray` references:
  ```bash
  grep -rn "SchemaArray" pysilicon/ tests/ examples/ docs/
  ```
  For each remaining hit:
  - Source code: should have been migrated in Phase 4; if not, migrate now.
  - Docs: update text references (probably in `docs/guide/components/` or wherever `DataArray` is documented).
  - Tests: remove tests that specifically exercised `SchemaArray`'s class behavior (vs round-trip behavior, which is covered by `DataArray` tests).
- Verify zero references remain.

**Tests:**

- All pre-existing tests still pass.
- `python -c "from pysilicon.hw.arrayutils import SchemaArray"` raises `ImportError` (confirming deletion).

**Commit:** `arrayutils: delete SchemaArray (all callers migrated to DataArray)`

---

## Phase 6: Migrate poly's `coeffs` regmap field to raw-array lowering

**Goal:** Demonstrate the new `cpp_storage="raw"` capability on a real example. Verify that `experiment/buildstep_demo.py` and `python -m examples.poly.poly_build --through gen_kernel` produce a kernel signature with `const float coeffs[4]` (or equivalent) instead of `CoeffArray& coeffs`.

**Changes:**

- In [examples/poly/poly.py](../examples/poly/poly.py), introduce a raw-lowering coefficient schema:

  ```python
  # Existing CoeffArray stays as the struct-form for any other use; if no other
  # use exists, just modify it. For poly's regmap, use raw lowering:
  CoeffArrayRaw = DataArray.specialize(
      element_type=Float32,
      max_shape=(4,),
      static=True,
      cpp_storage="raw",
  )
  ```

  Then change the regmap declaration:

  ```python
  self.regmap = VitisRegMap({
      ...
      "coeffs": RegField(CoeffArrayRaw, RegAccess.RW, description="Polynomial coefficients"),
  }, bitwidth=self.aximm_bw)
  ```

  Alternative if no other consumer of `CoeffArray` exists: just add `cpp_storage="raw"` to the existing `CoeffArray` class definition.

- Verify the change by running:
  ```bash
  python -m examples.poly.poly_build --through gen_kernel
  cat examples/poly/gen/poly.cpp
  ```
  The generated kernel signature should now include `const float coeffs[4]` (or `float coeffs[4]`) instead of `CoeffArray& coeffs`.

- Update [experiment/poly_codegen_notes.md](../experiment/poly_codegen_notes.md) — flip Blocker 4 from "open" to "resolved by Phase 3+6 of dataschema_plan."

**Tests:**

- Extend `tests/examples/test_poly_codegen.py` to assert the generated `poly.hpp` contains `coeffs[4]` (a `coeffs[` substring) and does NOT contain `CoeffArray& coeffs`.
- All other poly tests continue to pass.

**Commit:** `poly: coeffs uses cpp_storage="raw" — generated signature is float coeffs[4]`

---

## Final acceptance

- `pytest tests/hw/ tests/build/ tests/examples/ -k "not vitis"` passes (modulo the 12 pre-existing `tests/build/test_build.py` failures).
- `mypy` and `ruff` clean on touched files.
- `python -m examples.poly.poly_build --through gen_kernel` succeeds; `examples/poly/gen/poly.cpp` contains `coeffs[4]` in the kernel signature.
- `python experiment/buildstep_demo.py` still works (no regressions on the demo).
- `grep -r SchemaArray pysilicon/ tests/ examples/` returns nothing.
- 6 commits on `main`, one per phase, pushed in order.

## Out of scope (do NOT do)

- **Codegen for pipelined stream patterns** in extracted bodies. The rule explicitly forbids that case. Long-term AI-assisted synthesis hint extraction may reintroduce a different representation; not now.
- **Phase 12a** (Blockers 1 and 2 of poly codegen — schema-include walking and utility-include discovery). Separate plan that lands after this one.
- **Phase 12b** (the poly swap-over — refactor `poly_tb.cpp`, delete hand-written `poly.cpp`, rewire `CSimStep`). Gated on this plan + Phase 12a.
- **Renaming `DataArray` to something else.** Stays as is.
- **Multi-dimensional raw lowering.** `cpp_storage="raw"` requires `len(max_shape) == 1`. Multi-dim raw arrays (`float coeffs[N][M]`) come later if a real example needs them.
- **`cpp_storage` on `DataList` or other `DataSchema` subclasses.** Only `DataArray` for now.
- **Adding a `cpp_storage="hybrid"` or other modes.** Two-valued only.

If a design question arises that this plan doesn't answer, stop and ask — do not invent a new convention.
