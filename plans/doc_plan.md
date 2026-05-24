# Documentation Reorganization Plan

> **Status: DEFERRED.** Do not execute until Phase 11 (poly integration —
> wiring `HlsCodegenStep` into `examples/poly/poly_build.py`) has landed.
> Reason: the `synthesis` and `components` guide sections need a real
> end-to-end example to document accurately, and writing them off
> `DemoComponent` alone would be thin and need rewriting once poly is wired
> in. The `docs/future/` placeholder (Phase 1) is the only thing safe to
> land before then.

## Goal

Reorganize `docs/` so it reflects the codebase as it actually exists today, with a clear separation between:

- **`docs/overview/`** — short project pitch (what PySilicon is, who it's for, value proposition). 2-3 pages.
- **`docs/guide/`** — reference docs for shipped features. Each feature area has its own subsection.
- **`docs/examples/`** — worked examples (poly, etc.). Already present; expand to cover the end-to-end synthesis pipeline once it exists.
- **`docs/future/`** — explicit roadmap for aspirational features (AI hook completion, AI planning, design analysis, multi-component synthesis, Vivado IPI backend).
- **`docs/architecture/`** — **deleted**. Content was written when the codebase was nascent and is now ~70% stale or wrong. Anything genuinely architectural moves into per-feature `index.md` pages in `docs/guide/`.

## Already done (do NOT redo)

- `docs/guide/installation/`, `interface/`, `memory/`, `build/`, `timing/`, `developer/` — current and accurate. Leave alone.
- `docs/examples/poly/` — current. Will need expansion in a follow-up once the full synthesis pipeline ships, but not part of this plan.

## Stale sections caught later (must be rewritten in this plan)

- `docs/guide/schema/` — stale due to the dataschema unification (Phase 9 of the synthesis work). Specifically `dataarrays.md` references the deleted `SchemaArray` framing, an obsolete `gen_array_utils` function (current API: `ArrayUtilsStep`), and an old `CodeGenConfig`/`util_dir` configuration style (current: `BuildConfig`). Plus several new concepts are completely undocumented: the `array()` factory in [pysilicon/hw/arrayutils.py](../pysilicon/hw/arrayutils.py), `DataArray.cpp_storage` (struct vs raw lowering), and the rule that pipelined stream operations are only legal inside `@synthesizable` hook bodies. Phase 4 of this plan addresses these.

## Design decisions (already settled — do NOT re-litigate)

1. **`docs/architecture/` is deleted entirely.** Cross-cutting design principles move into the relevant `docs/guide/<area>/index.md` pages where they're closest to the feature they describe.
2. **`docs/overview/` shrinks to 3 pages max:** `index.md` (1-page elevator pitch), `example.md` (a poly walkthrough, ~1 page, pointing at the full example docs), `motivation.md` (why this approach). Drop `innovations.md` and `prior.md` — aspirational positioning is better expressed via worked examples that actually exist.
3. **`docs/future/` is new.** One page per major aspirational area. Each page is forward-looking only — it does not document existing behavior. Cross-references to relevant plan files in `plans/`.
4. **Two new `docs/guide/` subsections:** `components/` (mostly placeholder today, fill in real content) and `synthesis/` (does not exist today, create from scratch). These are the meat of the writing work.
5. **No `docs/analysis/` section yet.** Design analysis (timing-model validation, characterization scripts) is still mostly aspirational. Mention in `docs/future/`. When real analysis features ship, add as a guide subsection then.
6. **Just-the-docs nav_order** is used to control sidebar order. Numbers preserved across moves; renumber the guide sections so synthesis/components land in a sensible spot (probably after `components`, before `build`).
7. **Internal links updated, not redirected.** Any in-repo `[text](docs/architecture/foo.md)` link becomes a link to the new location. No legacy-redirect pages.

## Reference reading (read once before executing)

- [docs/architecture/](../docs/architecture/) — all 10 files. Read them to understand what existed; harvest any content worth preserving.
- [docs/guide/build/](../docs/guide/build/), [docs/guide/interface/](../docs/guide/interface/) — the modern doc style. Follow this shape for the new sections.
- [pysilicon/hw/hw_component.py](../pysilicon/hw/hw_component.py), [pysilicon/build/hwgen.py](../pysilicon/build/hwgen.py), [pysilicon/build/hwcodegen.py](../pysilicon/build/hwcodegen.py), [pysilicon/build/hwresolve.py](../pysilicon/build/hwresolve.py), [pysilicon/build/hwcodegen_steps.py](../pysilicon/build/hwcodegen_steps.py) — source of truth for the synthesis docs.
- [examples/poly/poly.py](../examples/poly/poly.py) and [examples/poly/poly_build.py](../examples/poly/poly_build.py) — worked example to draw from.

## Working convention

- One commit per phase, in order, push after each.
- Read each file you're about to delete and harvest any preservation-worthy content into a `_harvest.md` scratch file (deleted before the final commit). This protects against accidentally losing useful prose.
- No code changes anywhere — this plan is documentation only.

---

## Phase 1: Add `docs/future/` placeholder (safe to execute before Phase 11 lands)

**Goal:** Establish the structural intent now with a minimal placeholder. Cheap, signals direction, doesn't require any other work to be done first.

**Changes:**

- Create [docs/future/index.md](../docs/future/index.md):

  ```markdown
  ---
  title: Future
  parent: PySilicon
  nav_order: 9
  has_children: true
  ---

  # Future Work

  This section describes planned features that are not yet implemented.
  Each page below describes one area of forward-looking work; for details
  on what's currently being designed or built, see the corresponding
  plan file under [plans/](https://github.com/sdrangan/pysilicon/tree/main/plans).

  - [AI-Assisted Hook Completion](./ai_hook_completion.md)
  - [AI-Assisted Planning](./ai_planning.md)
  - [Design Analysis Automation](./design_analysis.md)
  - [Vivado IPI Backend](./vivado_backend.md)
  ```

- Create stub pages for each (`docs/future/ai_hook_completion.md`, `ai_planning.md`, `design_analysis.md`, `vivado_backend.md`), each ~10 lines:

  ```markdown
  ---
  title: <Title>
  parent: Future
  ---

  # <Title>

  > **Status:** Not implemented. This page describes intended future work.

  ## Concept
  <1 paragraph on what this is and why it's wanted>

  ## Status
  <1 paragraph on what's currently designed/built/none>

  ## See also
  <links to relevant plan files in plans/, or "TBD">
  ```

**Commit:** `docs: add future/ section with stubs for aspirational features`

---

## Phase 2: Write `docs/guide/synthesis/`

**Goal:** Reference docs for the full synthesis pipeline as it actually exists. This is the biggest writing effort in the plan.

**Pages:**

- `docs/guide/synthesis/index.md` — concept overview. The layered pipeline (extractor → resolver → codegen → BuildStep). How a Python `HwComponent` becomes a compilable HLS kernel set. Link to subpages.
- `docs/guide/synthesis/extractor.md` — `HwStmtExtractor`, the synthesizable subset (which Python patterns translate, which raise), the no-implicit-capture rule, `extract_kernel(comp)` entry point.
- `docs/guide/synthesis/codegen.md` — how `kernel_files_to_str` works. The three generated file types (.hpp, .cpp, _impl.cpp/.tpp). C++ type translation rules. Namespacing.
- `docs/guide/synthesis/templating.md` — `HwParam` → C++ template params. The `HwParamValue` wrapper. Hook templating and the `.tpp` pattern. The sticky-impl-file lifecycle.
- `docs/guide/synthesis/step.md` — `HlsCodegenStep` usage in a `BuildDag`. Example wiring (lifted from `examples/poly/poly_build.py` once it's wired in).

Match the doc style of [docs/guide/interface/](../docs/guide/interface/) — frontmatter, concept → API → example → quick reference. Each page should be self-contained and skim-readable.

**Commit:** `docs: add guide/synthesis/ section`

---

## Phase 3: Rewrite `docs/guide/components/`

**Goal:** The `components/` subsection exists but is thin. Fill it in with current `HwComponent` reality.

**Pages:**

- `docs/guide/components/index.md` — concept: `Component` vs `HwComponent`, the three variable categories (`HwConst` / `HwParam` / plain), endpoint declaration patterns.
- `docs/guide/components/hwparam.md` — `HwParam[T]`, `HwParamValue` wrapper, immutability semantics, how the auto-wrap in `__post_init__` works.
- `docs/guide/components/hwconst.md` — `HwConst[T]`, intent vs enforcement, when to use vs plain class attribute.
- `docs/guide/components/variants.md` (new) — `param_supports` declaration: when and why to declare variants, naming convention (`<cpp_kernel_name>_<key>`), the default-always-emitted rule, validation rules (valid C identifier, non-empty override dict, known HwParam fields, duplicate-resolved-config warning), and what the generated code looks like (one concrete top-level function per variant, no top-level templates).
- `docs/guide/components/lifecycle.md` — `pre_sim` / `run_proc` / `on_start` / `post_sim`. When each fires. When to use `on_start` (regmap-driven launch) vs `run_proc` (free-running SimPy process).

Existing content in `docs/guide/components/index.md` (if any) gets absorbed or replaced. Read first; harvest worth-preserving prose.

**Commit:** `docs: rewrite guide/components/ for HwComponent reality`

---

## Phase 4: Refresh `docs/guide/schema/`

**Goal:** Update the schema docs to reflect the post-dataschema-unification reality. Delete references to the deleted `SchemaArray`, replace obsolete API names, and document new concepts (`array()` factory, `DataArray.cpp_storage`, the pipelined-ops rule).

**Pages to touch:**

- `docs/guide/schema/dataarrays.md` — biggest rewrite. The current opening framing ("This section does NOT discuss the `DataArray` schema class itself") is wrong; `DataArray` *is* the unified array concept now. Restructure as: (1) `DataArray` declaration and `element_type`/`max_shape` basics, (2) the `array(elem_type, data)` factory for runtime construction, (3) `cpp_storage="struct"` (default) vs `"raw"` lowering modes with examples, (4) generated array utilities via `ArrayUtilsStep`. Drop the section that says it's only about "array utilities" — that's one piece of a larger story.
- `docs/guide/schema/dataarrays.md` — fix specific API references:
  - `gen_array_utils` → `ArrayUtilsStep` (BuildStep, registered in a BuildDag; not a free function).
  - `CodeGenConfig(root_dir=..., util_dir=...)` → `BuildConfig(root_dir=...)`.
  - Fix the typo `WORD_BWword_bw_supported` → `word_bw_supported` (whatever it should have been).
- `docs/guide/schema/datalists.md` — verify the `CoeffArray(DataArray)` example still reads correctly post-unification. The example shape (a class inheriting `DataArray` with `element_type` and `max_shape`) is still valid; just confirm the surrounding prose doesn't reference `SchemaArray` or obsolete APIs.
- `docs/guide/schema/codegen.md` — verify the generated-codegen story still matches the current `DataSchemaStep` + `ArrayUtilsStep` flow.
- `docs/guide/schema/index.md` — verify the table of contents and any overview prose still reflect current structure.
- `docs/guide/schema/dataunion.md` — verify (probably unaffected, but check).

**New sections to add to `dataarrays.md`** (or a new sibling page `array_lowering.md`):

- **`cpp_storage` modes.** Worked example: declaring a `DataArray` with `cpp_storage="raw"` and what the generated C++ looks like (`float arr[N]` vs `MyArray& arr`). When to use each. Reference the poly `CoeffArray` usage as an example.
- **The `array()` factory.** For converting `(elem_type, runtime_data)` to a `DataArray` instance without manual specialization. Useful for testbench-side array construction.
- **Pipelined ops rule.** Brief callout that `s_in.get_pipelined(...)` and `m_out.write_pipelined(...)` are only legal inside `@synthesizable` hook bodies, not in `on_start` / `run_proc`. Probably better placed in `docs/guide/components/` or `docs/guide/synthesis/`, but worth cross-referencing from the schema docs.

**Sweep for `SchemaArray` references** across all of `docs/guide/schema/`:

```bash
grep -rn "SchemaArray" docs/
```

For each hit, replace with a `DataArray`-based equivalent or delete the surrounding sentence if it was specifically describing `SchemaArray`-only behavior.

**Commit:** `docs: refresh guide/schema/ for DataArray unification (drop SchemaArray, add cpp_storage + array())`

---

## Phase 5: Slim `docs/overview/`

**Goal:** Cut the overview down to the elevator pitch + one worked example pointer + motivation. Drop aspirational positioning.

**Changes:**

- Rewrite [docs/overview/index.md](../docs/overview/index.md) as a 1-page elevator pitch. What PySilicon is, who it's for, the value proposition.
- Rewrite [docs/overview/example.md](../docs/overview/example.md) as a short poly walkthrough that points at the full example docs.
- Rewrite [docs/overview/motivation.md](../docs/overview/motivation.md) — why a Python-source-of-truth approach. Keep the most-current prose from the existing version; drop anything that's aspirational positioning.
- **Delete** [docs/overview/innovations.md](../docs/overview/innovations.md) and [docs/overview/prior.md](../docs/overview/prior.md). The "innovations" framing is better expressed via the worked examples and the feature guides; "prior art" is positioning we don't need on a project page.

**Commit:** `docs: slim overview/ to elevator pitch + example + motivation`

---

## Phase 6: Delete `docs/architecture/`

**Goal:** Final cut — after Phases 2–5 have absorbed any prose worth preserving.

**Changes:**

- Before deleting, walk each file in [docs/architecture/](../docs/architecture/) one last time. Any preservation-worthy content that isn't already in a guide page goes into the closest guide subsection's `index.md`.
- Delete the entire `docs/architecture/` directory.
- Grep the rest of `docs/` for `architecture/` links and update them to point at the new locations (or delete the references if the content was dropped).

**Commit:** `docs: delete architecture/ section; content absorbed into guide/`

---

## Phase 7: Renumber + cross-link audit

**Goal:** Make sure the sidebar order makes sense and internal links all resolve.

**Changes:**

- Walk every `docs/**/*.md` frontmatter and renumber `nav_order` so the sidebar reads in a sensible order:
  - `overview` (1), `examples` (2), `guide` (3, with children), `future` (4).
  - Inside `guide`: `installation`, `schema`, `interface`, `components`, `synthesis`, `build`, `memory`, `timing`, `developer`.
- Run a link-check across `docs/`:
  ```bash
  grep -r '\](.*\.md)' docs/ | grep -v '^Binary'
  ```
  For each match, verify the target exists. Fix any stale links.
- Build the site locally (or use `bundle exec jekyll serve` if Jekyll is set up) and confirm the sidebar renders and no 404s.

**Commit:** `docs: renumber nav_order + audit internal links`

---

## Final acceptance

- `docs/architecture/` does not exist.
- `docs/overview/` has 3 pages (`index.md`, `example.md`, `motivation.md`).
- `docs/guide/synthesis/` exists with at least 5 pages.
- `docs/guide/components/` is fleshed out with real content (not the current stub).
- `docs/future/` exists with at least 4 stub pages.
- No broken internal links across `docs/`.
- 7 commits on `main`, one per phase, pushed in order.

## Out of scope (do NOT do)

- `docs/analysis/` section. Design analysis is still aspirational — covered in `docs/future/design_analysis.md`. Promote to a real section when the features ship.
- Expanding `docs/examples/poly/` to cover the synthesis pipeline. Separate effort, depends on Phase 11 and probably Phase 12.
- Documenting features that haven't shipped (HwConst C++ codegen, multi-component synthesis, AI completion, etc.). Mention in `docs/future/`; don't write a guide for non-existent behavior.
- Renaming/relocating `docs/examples/` or `docs/guide/` themselves.
- Adding screenshots, diagrams, or auto-generated API reference. Optional polish for later.
- Touching the `_config.yml` site theme settings.

If a design question arises that this plan doesn't answer, stop and ask — do not invent a new convention.

## Trigger condition

Execute this plan when **all** of the following are true:

1. Phase 11 (poly integration) has landed and `python -m examples.poly.poly_build` produces a buildable kernel file set end-to-end.
2. Phase 12 (HwConst C++ codegen) has landed *or* has been explicitly deferred indefinitely — without one of these decisions, the `HwConst` docs in `docs/guide/components/` would be incomplete.
3. The `experiment/` sandbox scripts (`extract_demo.py`, `codegen_demo.py`, `buildstep_demo.py`) all produce sensible output for the current state — they're informal validation that the docs will accurately reflect what users see.

Until these hold, leave `docs/architecture/` in place (stale but harmless). Phase 1 (the `docs/future/` placeholder) is the only piece safe to execute before the trigger.

Note: as the synthesis pipeline has evolved, sections originally marked "current and accurate" can fall out of sync (this happened to `docs/guide/schema/` after the dataschema unification — addressed by Phase 4). Before executing this plan, re-grep the "current and accurate" sections for references to deleted classes / renamed APIs / superseded patterns. If new staleness is found, add or expand a phase rather than executing the plan as-written against incomplete inventory.
