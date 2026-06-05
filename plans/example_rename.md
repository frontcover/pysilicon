# Example Rename: pattern-named, teaching-ordered example set

## Goal

Rename the example set from incidental toy names to **pattern names that match
the intro-hardware teaching progression**, and restructure
[docs/examples/index.md](../docs/examples/index.md) to (a) list the five examples
as an ordered progression and (b) state the general PySilicon flow every full
example follows. Each directory name should foreground the *new concept* the
example introduces, not the computation it happens to use as a vehicle.

This is a **DRAFT** — the "Open decisions" section below must be resolved before
execution. Naming (Option B, semantic) is already settled; the structural
questions (mem_queue promotion, pure_stream scaffolding, internal symbol renames)
are not.

## The five examples (the progression)

| Order | Current | New dir | New concept introduced | Flow coverage today |
|---|---|---|---|---|
| 1 | `examples/regmap_simp_fun/` | `examples/regmap/` | register-mapped control (AXI4-Lite) | full |
| 2 | *(none — TBD)* | `examples/pure_stream/` | streaming dataflow: samples in/out, no packet boundary / no TLAST / no control (moving-average filter) | **planned, not built** |
| 3 | `examples/poly/` | `examples/stream_inband/` | packetization (TLAST) + in-band control on the stream | full |
| 4 | `examples/increment/` | `examples/shared_mem/` | data in memory (AXI-MM), control over a dedicated stream | full |
| 5 | `examples/interface/aximm_queue_demo.py` | `examples/mem_queue/` (proposed) | control *also* in memory, via a descriptor queue | **sim-only; codegen is Future** |

Each name = the delta a student just learned. The computation stays the vehicle:
`stream_inband` is still a polynomial accelerator, `shared_mem` is still the
increment kernel — the *directory* names the I/O pattern, the *content* keeps its
mathematical identity.

## The general flow (for index.md)

`index.md` should state the canonical PySilicon path every full example walks,
since that uniformity is the pedagogical point:

1. **Python model** — the golden numerical behavior (numpy / PyTorch).
2. **Python simulation** — the SimPy transactional sim (Components + Interfaces).
3. **Code generation** — Vitis HLS C++ kernel + testbench emitted from the model.
4. **C and RTL simulation** — Vitis C-sim, then C-synth + RTL co-simulation.
5. **Timing extraction** — cycle/burst measurement from cosim, fed back to the
   Python timing model.

Not every example covers all five yet — the index should show a per-example
coverage marker so the gaps are honest (regmap / stream_inband / shared_mem =
all five; mem_queue = stages 1–2 only, codegen TBD; pure_stream = planned).

## Scope

**In scope:** directory + doc-folder renames; `docs/examples/index.md` rewrite
(five-example list + general-flow statement); updating all internal references
(imports, test paths, doc nav_order/front-matter/links, build-script paths).

**Out of scope:**
- `mcp/corpus/` — reworked wholesale at the AI-codegen phase; do NOT re-path it
  now (see the separate corpus note).
- Building the `pure_stream` example itself (reserve the slot only — see Open
  decisions).
- Renaming computation-specific Python symbols (`PolyAccelComponent`,
  `IncrAccel`, etc.) — see Open decisions; default is to keep them.

## Open decisions (CONFIRM before executing)

1. **`mem_queue` placement.** The queue is currently a sim-only demo file inside
   `examples/interface/` (alongside `aximm_demo`, `crossbar_demo`, `stream_demo`,
   …), not a standalone example dir. Options:
   - **(A, recommended)** Promote it to `examples/mem_queue/` so all five live as
     sibling pattern-examples. Move `aximm_queue_demo.py` there; update the test
     path (`tests/examples/test_aximm_queue_demo.py`). The other `interface/`
     files stay (they're interface-*feature* demos, not pattern examples).
   - **(B)** Leave it in `interface/`, just rename the file
     (`aximm_queue_demo.py` → `mem_queue_demo.py`) and reference it from the index
     without giving it a sibling dir.
   Recommendation: **A** — the "five parallel examples" framing breaks if one is
   buried. But it's more churn; your call.

2. **`pure_stream` — reserve vs scaffold.** Just list it in the index as
   "planned" (no dir), or create an empty `examples/pure_stream/` scaffold now?
   Recommendation: **reserve only** (listed as TBD/planned, no empty dir — empty
   dirs are clutter). Build it later as a moving-average filter (no TLAST).

3. **Internal symbol / entry-module renames.** Rename only paths/imports, or also
   the entry modules (`poly.py` → `stream_inband.py`, `incr.py` → `shared_mem.py`)
   and class names? Recommendation: **rename dirs + doc folders + cross-references
   only; keep entry-module filenames and computation-named classes** to bound the
   churn. Revisit per-example during execution if an import path reads badly.

## Working convention

- Use `git mv` for every rename so history follows the files.
- One commit per example (rename + its reference updates), plus a final commit for
  the `index.md` rewrite. Single PR, multiple commits.
- After each commit: `pytest tests/hw/ tests/examples/ -k "not vitis"` green
  (note, don't fix, the pre-existing `test_dataschema_poly.py` poly.hpp failure —
  **and watch it specifically**, since renaming `poly` may move what it looks
  for; update that test's path if the rename is the cause).
- Pure docs/structure change — no Vitis required.
- This is its own branch/PR, kept entirely separate from any feature work.

## Phases

### Phase 0: Reference sweep (read-only)

Enumerate every reference to `poly`, `increment`/`incr`, `regmap_simp_fun`,
`simp_fun`, and `aximm_queue` across `examples/`, `tests/`, `docs/`, and
`pysilicon/` (build scripts, `run.tcl`, project-dir names like
`pysilicon_poly_proj`, doc `[...](...)` links, `nav_order`). Produce the rename
map and the full blast radius. **Do not touch `mcp/corpus/`.** Resolve the Open
decisions from this sweep's findings before editing.

### Phase 1: Rename `regmap_simp_fun` → `regmap`

`git mv examples/regmap_simp_fun examples/regmap` and
`docs/examples/regmap_simp_fun → docs/examples/regmap`; update imports, test
paths, `run.tcl`/project paths, doc front-matter (title/nav_order) and links.

**Commit:** `examples: rename regmap_simp_fun → regmap (AXI4-Lite control pattern)`

### Phase 2: Rename `poly` → `stream_inband`

`git mv examples/poly examples/stream_inband` and `docs/examples/poly →
docs/examples/stream_inband` (6 doc pages). Update imports, `poly_build.py`
paths, the `view_timing.ipynb`/`timing_analysis.py` references, and the
`test_dataschema_poly.py` path if the sweep shows it points at `examples/poly`.

**Commit:** `examples: rename poly → stream_inband (in-band stream control pattern)`

### Phase 3: Rename `increment` → `shared_mem`

`git mv examples/increment examples/shared_mem`; update imports
(`incr_build.py`, etc.), test paths (`test_incr_*.py`), `run.tcl`/project paths.
Create a `docs/examples/shared_mem/` doc page (increment has none today) — at
least an index page in the regmap/poly doc style.

**Commit:** `examples: rename increment → shared_mem (AXI-MM data + stream control pattern)`

### Phase 4: `mem_queue` (pending Open decision 1)

If (A): `git mv examples/interface/aximm_queue_demo.py
examples/mem_queue/mem_queue_demo.py` (+ `__init__` if the others have one);
move the test; add a `docs/examples/mem_queue/` index page. This page **absorbs
the queue plan's skipped Phase 7 docs** (memory layout, SPSC contract,
`write`/`get` API, blocking vs non-blocking).

**Commit:** `examples: promote aximm_queue demo → mem_queue example (descriptor-queue pattern)`

### Phase 5: Rewrite `docs/examples/index.md`

List the five examples in progression order with one-line "new concept"
descriptions and per-example flow-coverage markers; add the general five-stage
flow statement (Python model → sim → codegen → C/RTL sim → timing). Reserve
`pure_stream` as a "planned" entry (Open decision 2). Fix the example-section
nav so the renamed folders sort in teaching order.

**Commit:** `docs: example index — five-pattern progression + general PySilicon flow`

## Future / out of scope (capture, don't build)

- **Build `pure_stream`** — a moving-average / FIR filter illustrating
  boundary-free streaming (no TLAST), full five-stage flow. Slots in at position 2.
- **`mem_queue` HLS codegen** — the queue is sim-only today; generating its
  `m_axi` ring `write`/`get` is now unblocked by the increment codegen work but
  is a separate effort.
- **`mcp/corpus/` rework** — done at the AI-codegen phase; this rename
  deliberately skips it.
- **MemComponent guide docs** — the retired `memory_simobj.md` plan left its
  optional Phase 3 (a `docs/guide/memory/` SimObj+latency page) unbuilt; not part
  of this rename, but noted so it isn't lost.
