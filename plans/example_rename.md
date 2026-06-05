# Example Overhaul: pattern-named, teaching-ordered example set

## Goal

Restructure the example set around the **intro-hardware teaching progression**:
pattern-named directories, an ordered `docs/examples/index.md` that states the
general PySilicon flow, and — crucially — examples *interesting enough that
students see the point*. The headline shared-memory example becomes the
**histogram** (richer than the increment toy), upgraded to be **codegen-driven**
like the rest. Increment is retained as a minimal codegen regression, not a
headline.

This is an **umbrella plan**. The meaty sub-effort — regenerating the histogram
kernel/TB from a Python `HistAccel` HwComponent — has its own detailed plan:
[plans/histogram_codegen.md](histogram_codegen.md). This plan owns the renames +
the docs restructure + sequencing.

## The five examples (the progression)

| Order | Vehicle | Dir | New concept introduced | Status |
|---|---|---|---|---|
| 1 | simple function | `examples/regmap/` (was `regmap_simp_fun`) | register-mapped control (AXI4-Lite) | rename only |
| 2 | moving-average filter | `examples/pure_stream/` | streaming dataflow — no packet boundary / no TLAST / no control | **reserved (TBD)** |
| 3 | polynomial | `examples/stream_inband/` (was `poly`) | packetization (TLAST) + in-band control on the stream | rename only |
| 4 | **histogram** | `examples/shared_mem/` (was `histogram`) | data in memory (AXI-MM), control over a dedicated stream | rename **+ codegen upgrade** ([histogram_codegen.md](histogram_codegen.md)) |
| 5 | vector unit | `examples/mem_queue/` | control *also* in memory, via a descriptor queue | **reserved (TBD)** |

**Increment dropped (update):** `examples/increment/` was the scaffolding toy
that de-risked the m_axi codegen path. With `shared_mem` (histogram) now
codegen-driven, it strictly subsumes increment's coverage (multi-buffer ⊇
single-buffer, float+uint ⊇ uint-only, validation ⊇ trivial-status), and the
clean multi-buffer lowering has no single-`max_n` codepath to keep increment
working — so increment was removed (see the histogram_codegen effort). The
`shared_mem` example is the m_axi codegen reference.

**Settled by review (do not re-litigate):**
- shared_mem = histogram, codegen-driven; increment dropped (subsumed).
- The histogram codegen upgrade IS in scope for this effort.
- `pure_stream` and `mem_queue` are **reserved slots** — listed as planned, not
  built now. `aximm_queue` stays where it is (`examples/interface/aximm_queue_demo.py`,
  sim-only); the future `mem_queue` example is a vecunit driven by an AXI-MM
  descriptor queue, which needs queue HLS codegen first (a separate effort).
- **Pattern-named directories** (`regmap`, `stream_inband`, `shared_mem`), not
  computation names. Computation-named files/symbols stay *inside*
  (`hist.cpp`, `HistAccel`, `PolyAccelComponent`); each doc page carries both —
  e.g. "Histogram accelerator — shared-memory pattern." The dir names the
  pattern; the content keeps its mathematical identity.

## The general flow (for index.md)

`index.md` states the canonical path every *full* example walks:

1. **Python model** — golden numerical behavior (numpy / PyTorch).
2. **Python simulation** — the SimPy transactional sim (Components + Interfaces).
3. **Code generation** — Vitis HLS C++ kernel + testbench emitted from the model.
4. **C and RTL simulation** — Vitis C-sim, then C-synth + RTL co-simulation.
5. **Timing extraction** — cycle/burst measurement from cosim, fed back to the
   Python timing model.

Per-example coverage markers keep it honest: regmap / stream_inband / shared_mem
= all five; mem_queue = stages 1–2 (codegen TBD); pure_stream = planned.

## Scope

**In scope:** directory + doc-folder renames; the histogram codegen upgrade (via
the companion plan); `docs/examples/index.md` rewrite; updating all internal
references (imports, test paths, doc nav_order/front-matter/links, build-script
and project-dir paths).

**Out of scope:** `mcp/corpus/` (reworked at the AI-codegen phase — do NOT
re-path now); building `pure_stream`; building the vecunit `mem_queue`; the
MemComponent guide-docs page left by the retired `memory_simobj.md`.

## Working convention

- `git mv` for every rename so history follows the files.
- One commit per example (rename + reference updates); the histogram codegen
  upgrade follows its companion plan's commit structure; a final commit for the
  `index.md` rewrite. Single PR, multiple commits.
- After each commit: `pytest tests/hw/ tests/examples/ -k "not vitis"` green.
  **Watch `test_dataschema_poly.py` specifically** — renaming `poly` may move
  what it looks for; update its path if so (it currently has a pre-existing
  poly.hpp failure — distinguish that from a rename-induced break).
- Vitis-gated steps (the histogram codegen csim/cosim) run under `-m vitis`.
- Own branch/PR, separate from feature work.

## Phases

### Phase 0: Reference sweep (read-only)
Enumerate every reference to `poly`, `increment`/`incr`, `regmap_simp_fun`/
`simp_fun`, `histogram`/`hist`, and `aximm_queue` across `examples/`, `tests/`,
`docs/`, `pysilicon/` (build scripts, `run.tcl`, project dirs like
`pysilicon_hist_proj`, doc links, `nav_order`). Produce the rename map + blast
radius. **Skip `mcp/corpus/`.** Resolve the Open decision from the findings.

### Phase 1: `regmap_simp_fun` → `regmap`
`git mv` example + doc folder; update imports, test paths, `run.tcl`/project
paths, doc front-matter + links.
**Commit:** `examples: rename regmap_simp_fun → regmap (AXI4-Lite control pattern)`

### Phase 2: `poly` → `stream_inband`
`git mv` example + doc folder (6 pages); update `poly_build.py`,
`timing_analysis.py`, `view_timing.ipynb`, and `test_dataschema_poly.py` paths.
**Commit:** `examples: rename poly → stream_inband (in-band stream control pattern)`

### Phase 3: `histogram` → `shared_mem` + codegen upgrade
`git mv examples/histogram examples/shared_mem` (+ doc folder, new if absent);
then execute [plans/histogram_codegen.md](histogram_codegen.md) to make it
codegen-driven (build `HistAccel`, generate kernel/TB, validate against the
existing hand-written `hist.cpp`/`hist_tb.cpp` + cosim/burst harness). Multiple
commits per that plan.
**Commits:** per histogram_codegen.md, prefixed `shared_mem:`

### Phase 4: Rewrite `docs/examples/index.md`
Five examples in progression order with "new concept" one-liners + coverage
markers; the general five-stage flow statement; reserve `pure_stream` and
`mem_queue` as planned; fix the example-section nav to sort in teaching order.
**Commit:** `docs: example index — five-pattern progression + general PySilicon flow`

## Future / out of scope (capture, don't build)
- **Build `pure_stream`** — moving-average/FIR, boundary-free streaming (no
  TLAST), full five-stage flow. Position 2.
- **Build `mem_queue`** — vecunit driven by an AXI-MM descriptor queue. Needs
  AXI-MM queue HLS codegen (nonexistent; unblocked by the increment codegen work).
- **`mcp/corpus/` rework** — at the AI-codegen phase.
- **MemComponent guide docs** — the retired `memory_simobj.md` left a
  `docs/guide/memory/` SimObj+latency page unbuilt. Not part of this overhaul.
