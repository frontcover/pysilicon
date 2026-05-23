# HwComponent Phase 11: Poly Codegen Inspection (Option A — Generate Alongside)

## Goal

Wire `HlsCodegenStep` into [examples/poly/poly_build.py](../examples/poly/poly_build.py) so it generates `poly.hpp`, `poly.cpp`, and `poly_evaluate_impl.tpp` for `PolyAccelComponent` into a **`gen/` subdirectory**, alongside (not replacing) the existing hand-written `poly.cpp` / `poly.hpp`. CSim continues to use the hand-written files. The generated files are for visual inspection only.

The deliverable is a directory `examples/poly/gen/` containing the codegen output, which we'll use to identify any bugs in the codegen on a real example (PolyAccelComponent is the first non-`DemoComponent` user of the pipeline) **before** committing to the swap-over in a future plan.

This plan does **not** delete or modify the hand-written `poly.cpp` / `poly.hpp` / `poly_tb.cpp`. It does **not** touch `CSimStep` / `CSynthStep` / `ValidateCSimStep`. The existing Vitis flow continues to work unchanged.

## Already done (do NOT redo)

- Phases 3.5–10: the full synthesis pipeline (extractor, resolver, codegen, header/cpp/impl emission, namespacing, `HlsCodegenStep`, HwParam templating, hook templating with `.tpp` pattern).
- `HlsCodegenStep` works end-to-end on `DemoComponent` per `experiment/buildstep_demo.py`.

## Known issues this plan addresses

Re-reading the codegen output for `DemoComponent` reveals one bug that will block poly:

- **Schema include filenames don't match `DataSchemaStep`'s output.** `hwgen.py` emits `#include "include/<lowercase>.h"` (e.g., `include/democmdhdr.h`), but `DataSchemaStep` produces `include/<snake_case>.h` (e.g., `include/demo_cmd_hdr.h`). For `DemoComponent` this didn't matter because we never compiled. For poly it does — `DataSchemaStep` writes `include/poly_cmd_hdr.h` and the generated `poly.hpp` would try to `#include "include/polycmdhdr.h"` which doesn't exist.

The plan fixes this in Phase 1.

## Design decisions (already settled — do NOT re-litigate)

1. **`gen/` subdirectory**, not the root of `examples/poly/`. Keeps generated files visibly separate from hand-written ones.
2. **`PolyAccelComponent` gets `cpp_kernel_name: ClassVar[str | None] = "poly"`.** The default would name the kernel `poly_accel` (snake_case strip Component); we override to match what the existing `poly_tb.cpp` expects.
3. **Hand-written `poly.cpp` / `poly.hpp` / `poly_tb.cpp` are untouched.** They remain `SourceStep` artifacts feeding `CSimStep` / `CSynthStep`. No build-graph wiring changes downstream of `HlsCodegenStep`.
4. **Sticky-impl-file behavior protects `gen/poly_evaluate_impl.tpp` across re-runs.** The first generation writes the stub; subsequent runs preserve it. This matters because Phase 12 will hand-edit this file to contain the real `evaluate` body extracted from `poly.cpp`.
5. **CLI access via `--through gen_kernel`.** Add `gen_kernel` as the step name. Users can run only the codegen step via `python -m examples.poly.poly_build --through gen_kernel`.
6. **Snake-case naming follows the same rule as `cpp_kernel_name` does for class names**: insert `_` before each upper-case letter that isn't at the start, then lowercase. `PolyCmdHdr → poly_cmd_hdr`, `CoeffArray → coeff_array`, `Float32 → float32`.
7. **The "schema include uses snake_case" fix is in `hwgen.py`, not in `DataSchemaStep`.** `DataSchemaStep`'s current output is the convention; `hwgen.py` was inconsistent. One-line fix.
8. **No test changes for `DemoComponent`'s flow except updating include-name substring expectations.** The demo's generated `.hpp` will now reference `include/demo_cmd_hdr.h` instead of `include/democmdhdr.h`. Update the affected tests in `tests/hw/test_hwgen.py` inline with the Phase 1 commit.
9. **If `extract_kernel(PolyAccelComponent(...))` fails for any reason in Phase 3**, STOP and ask. This is a real synthesis bug, not something to paper over.

## Reference reading (read once before starting)

- [pysilicon/build/hwgen.py](../pysilicon/build/hwgen.py) — `header_to_cpp`, `_collect_schemas`. The schema-include emission is around the `_collect_schemas` call site.
- [pysilicon/build/hwcodegen_steps.py](../pysilicon/build/hwcodegen_steps.py) — `HlsCodegenStep`. The thing you're wiring into poly.
- [examples/poly/poly.py](../examples/poly/poly.py) — `PolyAccelComponent` definition. Note: `on_start` calls `self.evaluate(cmd_hdr, self.s_in, self.m_out)` — a stream-arg hook, will be templated.
- [examples/poly/poly_build.py](../examples/poly/poly_build.py) — the DAG you're extending. Pay attention to the existing `gen_cpp` step (which runs `DataSchemaStep` etc.) and the source-step declarations.
- [examples/poly/poly.cpp](../examples/poly/poly.cpp) — the hand-written kernel. Used as the **comparison target** in Phase 4. Do not modify.

## Working convention

- One commit per phase, in order, push after each.
- Run `pytest tests/hw/ tests/build/ tests/examples/test_poly_demo.py -k "not vitis"` after every phase. All must stay green (modulo the 12 pre-existing failures in `tests/build/test_build.py` — ignore those).
- The inspection in Phase 4 is the visible deliverable; capture observations in a sandbox file (`experiment/poly_codegen_notes.md`).

---

## Phase 1: Snake-case schema include filenames

**Goal:** `hwgen.py` emits `#include "include/poly_cmd_hdr.h"` instead of `#include "include/polycmdhdr.h"`. Matches what `DataSchemaStep` produces.

**Changes:**

- In [pysilicon/build/hwgen.py](../pysilicon/build/hwgen.py), find the schema-include emission inside `header_to_cpp`. It currently uses `s.cpp_class_name().lower()`. Replace with a snake_case helper:

  ```python
  import re

  def _snake_case(name: str) -> str:
      """CamelCase / PascalCase / mixedCase -> snake_case."""
      return re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
  ```

  Then change `f'#include "include/{s.cpp_class_name().lower()}.h"'` to `f'#include "include/{_snake_case(s.cpp_class_name())}.h"'`.

- Update test expectations in [tests/hw/test_hwgen.py](../tests/hw/test_hwgen.py):
  - Any substring assertion like `"include/democmdhdr.h"` becomes `"include/demo_cmd_hdr.h"`.
  - Any substring assertion like `"include/demoerror.h"` becomes `"include/demo_error.h"`.
  - Grep for `democmdhdr` and `demoerror` in tests; update.

**Tests:**

- The existing header-emission tests assert presence of include lines — update them.
- Add a small unit test for `_snake_case`: `'PolyCmdHdr' → 'poly_cmd_hdr'`, `'Float32' → 'float32'`, `'CoeffArray' → 'coeff_array'`, `'DemoErrorField' → 'demo_error_field'`.

**Commit:** `hwgen: snake_case schema include filenames (match DataSchemaStep convention)`

---

## Phase 2: `cpp_kernel_name` override on `PolyAccelComponent`

**Goal:** Generated kernel function is named `poly`, not `poly_accel`, matching what `poly_tb.cpp` will eventually call.

**Changes:**

- In [examples/poly/poly.py](../examples/poly/poly.py), add a class attribute to `PolyAccelComponent`:

  ```python
  class PolyAccelComponent(HwComponent):
      """..."""
      cpp_kernel_name: ClassVar[str | None] = "poly"
      ...
  ```

  Add the import if missing: `from typing import ClassVar`.

**Tests:**

- Add a small test (anywhere in `tests/hw/` or `tests/examples/`):
  ```python
  def test_poly_cpp_kernel_name_is_poly():
      from pysilicon.build.hwgen import cpp_kernel_name
      from examples.poly.poly import PolyAccelComponent
      assert cpp_kernel_name(PolyAccelComponent) == "poly"
  ```

**Commit:** `poly: cpp_kernel_name="poly" override on PolyAccelComponent`

---

## Phase 3: Wire `HlsCodegenStep` into `poly_build.py` with `output_dir="gen"`

**Goal:** Add `HlsCodegenStep` to the poly build DAG. It generates into `examples/poly/gen/`. CSim and downstream steps are untouched.

**Changes:**

- In [examples/poly/poly_build.py](../examples/poly/poly_build.py):
  - Import `HlsCodegenStep`:
    ```python
    from pysilicon.build.hwcodegen_steps import HlsCodegenStep
    ```
  - In `build_poly_dag()`, add a new step **after** the existing `SourceStep` for `poly_source` and **alongside** (not in dependency chain with) the existing `SourceStep(artifact="poly_cpp", ...)`:
    ```python
    dag.add(HlsCodegenStep(
        name="gen_kernel",
        comp_class=PolyAccelComponent,
        source_artifact="poly_source",
        output_dir="gen",
    ))
    ```
  - Do **not** add the generated files as consumes for `CSimStep` / `CSynthStep`. Those still consume the existing source-step `poly_cpp` / `poly_hpp` / `poly_tb`. The new step's outputs are unused by any downstream step in this plan.

- Update the CLI default `--through`: leave it as `validate_timing`. Users can now run `python -m examples.poly.poly_build --through gen_kernel` to invoke just the codegen.

- Verify `extract_kernel(PolyAccelComponent(name="_codegen", sim=Simulation()))` succeeds. The agent should run this manually (one-line Python invocation) before committing. **If it raises `SynthesisError`, STOP and ask** — the extractor or resolver has a bug on the real component, not anticipated by this plan.

**Tests:**

- Add `tests/examples/test_poly_codegen.py`:
  ```python
  def test_poly_codegen_step_extracts_and_writes(tmp_path):
      from examples.poly.poly_build import build_poly_dag
      from pysilicon.build.build import BuildConfig

      dag = build_poly_dag()
      results = dag.run(
          BuildConfig(root_dir=tmp_path),
          through='gen_kernel',
      )
      assert results['gen_kernel'].success
      gen_dir = tmp_path / 'gen'
      assert (gen_dir / 'poly.hpp').exists()
      assert (gen_dir / 'poly.cpp').exists()
      assert (gen_dir / 'poly_evaluate_impl.tpp').exists()
  ```

- Run `python -m examples.poly.poly_build --through validate_timing` and confirm it still works (Python sim unaffected).

**Commit:** `poly_build: add HlsCodegenStep wired to gen/ for inspection (no downstream impact)`

---

## Phase 4: Inspect and document findings

**Goal:** Run the codegen, look at the generated files, capture observations. This phase produces a `experiment/poly_codegen_notes.md` (sandbox, not committed) that's a reference for the next plan (Phase 12 — the actual swap-over).

**Changes:**

- Run:
  ```bash
  python -m examples.poly.poly_build --through gen_kernel
  ```
  Files land in `examples/poly/gen/`.

- Create [experiment/poly_codegen_notes.md](../experiment/poly_codegen_notes.md) (sandbox). Suggested structure:
  ```markdown
  # Poly Codegen Inspection Notes

  ## Generated files
  - examples/poly/gen/poly.hpp  (size, line count)
  - examples/poly/gen/poly.cpp
  - examples/poly/gen/poly_evaluate_impl.tpp

  ## Comparison with hand-written examples/poly/poly.cpp

  ### Kernel function signature
  - Generated: <template params, args, pragmas>
  - Hand-written: <same fields>
  - Differences: <list>

  ### Schema includes
  - Generated: <list>
  - Hand-written: <list>
  - All match DataSchemaStep output? Yes/No

  ### Kernel body
  - Generated body structure matches on_start ✓ / ✗
  - Notable differences from hand-written kernel body: <list>

  ### Hook (evaluate) forward decl
  - Generated: <decl>
  - Templated? Yes/No
  - Expected `template <int in_bw, int out_bw>` form

  ### Hook impl stub
  - Generated body is a `// TODO` stub
  - Hand-written evaluate body (currently inline in poly.cpp's loop) will need
    to be lifted into this .tpp file in Phase 12

  ## Bugs / surprises observed
  - <list — these are what Phase 12 needs to address before the swap>

  ## Things that look right
  - <list — for confidence>
  ```

  Don't commit `experiment/poly_codegen_notes.md` (sandbox convention). Same for `examples/poly/gen/` — it's auto-generated; gitignore it.

- Add `gen/` to [examples/poly/.gitignore](../examples/poly/.gitignore) (create the file if absent):
  ```
  gen/
  ```

  This last item IS committed.

**Verification commands:**

```bash
pytest tests/hw/ tests/build/ tests/examples/test_poly_demo.py tests/examples/test_poly_codegen.py -k "not vitis"
python -m examples.poly.poly_build --through gen_kernel
python -m examples.poly.poly_build --through validate_timing      # still works
ls examples/poly/gen/
```

**Commit:** `poly: gitignore gen/ (HlsCodegenStep output)`

---

## Final acceptance

- `pytest tests/hw/ tests/build/ tests/examples/test_poly_demo.py tests/examples/test_poly_codegen.py -k "not vitis"` passes.
- `python -m examples.poly.poly_build --through gen_kernel` succeeds; three files appear in `examples/poly/gen/`.
- `python -m examples.poly.poly_build --through validate_timing` still succeeds (Python sim unaffected).
- `examples/poly/poly.cpp`, `examples/poly/poly.hpp`, `examples/poly/poly_tb.cpp` are byte-identical to before this plan started (`git diff main -- examples/poly/poly.cpp examples/poly/poly.hpp examples/poly/poly_tb.cpp` is empty).
- `examples/poly/gen/` is gitignored.
- `experiment/poly_codegen_notes.md` exists (sandbox, not committed) with substantive observations.
- 4 commits on `main`, one per phase, pushed in order.

## Out of scope (do NOT do)

- **Replacing the hand-written `poly.cpp` / `poly.hpp`** with the generated version. **That's Phase 12.**
- **Refactoring `poly_tb.cpp`** to use the new kernel signature. Phase 12.
- **Migrating the `evaluate` body** from inline-in-`poly.cpp` to `poly_evaluate_impl.tpp`. Phase 12.
- **Wiring `gen/poly.cpp` into `CSimStep`** as a consumed artifact. Phase 12.
- **Fixing any codegen bugs you observe in Phase 4.** Document them in the notes; the fixes go into a follow-up plan once you've decided which ones are blockers vs cosmetic.
- **`HwConst` C++ codegen.** Separate.
- **Vitis-compile verification of the generated files.** They're inspect-only; verifying compile is Phase 12 once we're actually using them.

If a design question arises that this plan doesn't answer, stop and ask — do not invent a new convention.
