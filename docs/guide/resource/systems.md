---
title: Systems
parent: Resource Analysis Tools
nav_order: 4
has_children: false
audience: python
api: [estimate_system, SystemEstimate, synth_system, SystemSynth, KernelResourcesStep, SystemSynthStep, LeafResourcesStep, ResourceReportStep, measure_instances]
summary: "A system's resources: every RTL instance of its top -- the Vitis kernels, the hand-written leaves its interfaces bring, the vendor IP -- priced from its own measurement and grouped by the pysim object it realizes, plus the system integration term, with the measured total of a whole-top synthesis beside the prediction. The composite-kernel decomposition, one level up."
---
# Systems

A [composite kernel](./composite.md) decomposes as *Σ modules + integration*.  A **system** -- several
kernels reached over a bus, each behind a [memory-mapped adaptor](../interface/axi_mm/slave.md), with
AMD's crossbar and an on-chip memory ([markov](../../examples/markov/), [mm_fir](../../examples/mm_fir/))
-- decomposes the same way one level up, and the parts are of three kinds:

| kind | in markov's top | measured by |
|---|---|---|
| Vitis kernel | `u_gen`, `u_chain`, and the two bus writers `u_u_fwd_wr`, `u_u_crd_wr` | its csynth report (`hls_estimate`) |
| hand-written leaf (`waveflow/build/rtl/`) | three `axi_slave_front`s, the queue / credit leaves, `mm_bram_port` + `bram_t2p`, an `mm_sync_fifo` | Vivado, out of context or in the system |
| vendor IP | `u_xbar` (`axi_crossbar`) | Vivado, the same two ways |

**Most of the RTL is not a module.**  The leaves come from *interfaces and adaptors* -- a device's
views, a deep link's FIFO, the crossbar -- and even the two bus writers, which are HLS tops, are brought
by an interface (`MmCreditStreamIF`).  A module walk (`compose`) cannot see them, so a system is priced
from its **instance inventory** instead.

## The instance inventory

`SystemTopSpec.instances()` lists every instance the generated top holds, from the same spec objects
the Verilog is rendered from -- so the list and the top cannot disagree:

```python
from examples.markov.markov_build import build_dag
spec = next(s for s in build_dag().steps() if s.name == "system_rtl").spec
for i in spec.instances():
    print(f"{i.inst:16} {i.module:24} {i.kind:6} {getattr(i.owner, 'name', '')}")
```

```text
u_u_fwd_ff       mm_sync_fifo             leaf   u
u_xbar           xbar_markov_4x3          ip     xbar
u_gen_mm_front   axi_slave_front          leaf   gen_mm
u_gen_qcmd       mm_queue_in              leaf   gen_qcmd
...
u_gen            markov_gen               kernel gen
u_u_fwd_wr       mm_queue_writer_64_128   kernel u
```

Each instance carries its parameters, its sources, an **identity** -- a kernel's structure key, a
leaf's module + parameters + a digest of every source it compiles from, the crossbar's configuration
-- and its **owner**, the pysim object it realizes: the kernel, the view or adaptor, the memory, or the
interface (the routed channel `u` owns its two writers and the FIFO feeding one).  Editing
`mm_queue_in.v` moves that leaf's identity, so its old numbers become unreachable rather than silently
wrong.  The XSI file list is the inventory's too: a system compiles only the leaves it uses.

## Where each number comes from

A system build ([`add_system_steps`](../build/xsi_system.md)) adds four resource steps:

```text
csynth ──> kernel_resources ──────────────┐
system_rtl ──> leaf_resources ────────────┴──> resource_report
system_rtl ──> system_synth   (opt-in; resource_report reads its records)
```

| step | does | cost |
|---|---|---|
| `kernel_resources` | each kernel's csynth report attributed ([as for a composite](./composite.md)), filed as `hls_estimate` records with their owner | seconds |
| `leaf_resources` | each leaf / IP configuration looked up by identity; with `measure_leaves`, a missing one synthesized out of context once and filed | lookup; ~1 min per new configuration |
| `system_synth` | the whole top synthesized in Vivado, every instance attributed from `report_utilization -hierarchical` | minutes; cached by the system's identity |
| `resource_report` | the estimate, row by row, with the measured total beside it | milliseconds |

Records go to the build's [platform](../calib/) (`--platform`), at the part and clock the kernels were
csynth'd for.  A build **writes** its work-tier platform and a lookup **reads** that first and then the
published library of the same name, so the next estimate sees what the build just filed.

## The system synthesis

`python markov_build.py --through system_synth --platform <name>` synthesizes markov's top out of context
(xc7z020, 10 ns; 653 s of Vivado 2025.1) and attributes every inventory instance:

| instance | LUT | FF | BRAM18 |
|---|---:|---:|---:|
| `u_mem_mem` (`bram_t2p`, 512 × 64) | 8769 | 32832 | 0 |
| `u_mem_front` (`axi_slave_front`) | 3625 | 76 | 0 |
| `u_chain` (`markov_chain`) | 2034 | 3115 | 0 |
| `u_gen` (`markov_gen`) | 1192 | 1298 | 0 |
| `u_xbar` (`axi_crossbar`, 4 × 3) | 848 | 680 | 0 |
| `u_u_fwd_wr` (queue writer) | 812 | 1020 | 2 |
| `u_u_crd_wr` (credit writer) | 492 | 622 | 0 |
| eight more leaves | 821 | 485 | 0 |
| **integration** (the top's own cells) | 0 | 2 | 0 |
| **total** | **18593** | **40130** | **2** |

Instances plus integration equal the total exactly -- integration is *defined* as the remainder, and
negative would be legal.  An inventory instance with no row is an error, not a zero: a dropped
instance would make the glue look expensive.

Two things the table shows that no estimate before it could:

- **The "BRAM" is flip-flops.**  `bram_t2p` infers no block RAM here: its 32 Kbit become 32832 FFs and
  8769 LUTs, and the BRAM window's front adds 3625 LUTs of read mux.  The on-chip memory is two thirds
  of markov's LUTs.  A fix belongs in `bram_t2p.v`'s coding style, measured by this same step.
- **csynth overestimates LUT about 2×.**  The `hls_estimate` of each kernel against its in-context
  synthesis:

  | kernel | LUT (hls / vivado) | FF (hls / vivado) |
  |---|---|---|
  | `markov_gen` | 2585 / 1192 | 1905 / 1298 |
  | `markov_chain` | 4423 / 2034 | 3030 / 3115 |
  | `mm_queue_writer_64_128` | 1531 / 812 | 943 / 1020 |
  | `mm_credit_writer_64` | 1097 / 492 | 874 / 622 |

  So *how* a number was obtained (an exact lookup) and *what* it measures (an HLS estimate) are
  separate columns of the report.

## The estimate

`estimate_system(sysm, platform)` prices every instance from the store -- no toolchain, no build -- and
groups them by owner, a multi-view adaptor's views rolled into the adaptor:

| instance | priced by, best first |
|---|---|
| kernel | an in-context record from *this* system's synthesis; else its class's own model (`get_rm`); else its modules' `hls_estimate` records plus its top's integration term |
| leaf / IP | an in-context record; else the configuration's out-of-context record; else its owner's model hook |
| system integration | the system identity's integration record |

A part with no data is a row at `UNCALIBRATED` with zero counters **and** what measuring it would cost
(`weakest()`), never a silent zero.  Before any Vivado run, markov's estimate is its kernels:

```text
markov_top   platform scratch_7020
u (MmCreditStreamIF)            2,628    1,817        2        0   UNCALIBRATED   hls_estimate
xbar (AXIMMCrossBarIF)              0        0        0        0   UNCALIBRATED   --
gen_mm (MemSlaveAdaptor)            0        0        0        0   UNCALIBRATED   --
chain_mm (MemSlaveAdaptor)          0        0        0        0   UNCALIBRATED   --
mem (MemoryMod)                     0        0        0        0   UNCALIBRATED   --
gen (MarkovGen)                 2,585    1,905        0        0   EXACT          hls_estimate
chain (MarkovChain)             4,423    3,030        0        0   EXACT          hls_estimate
system integration                  0        0        0        0   UNCALIBRATED   --
total                           9,636    6,752        2        0   weakest: UNCALIBRATED (u (MmCreditStreamIF))
```

`use_in_context=False` ignores the system's own synthesis records: the **composed prediction**, each
part from its own measurement, which the measured total is then a check on -- the area counterpart of
`compare`.

## Prediction against measurement

With markov's kernels attributed, its eleven leaf / IP configurations measured out of context
(`--through leaf_resources` with `measure_leaves`; 32 minutes the first time, nothing on a re-run) and
the top synthesized, the default estimate reads every instance from the system's own synthesis and
reproduces the measured total:

```text
markov_top   platform scratch_7020
                                  LUT       FF     BRAM      DSP   how            measured by
u (MmCreditStreamIF)            1,362    1,658        2        0   EXACT          vivado_synth (in markov_top)
xbar (AXIMMCrossBarIF)            848      680        0        0   EXACT          vivado_synth (in markov_top)
gen_mm (MemSlaveAdaptor)          246      217        0        0   EXACT          vivado_synth (in markov_top)
chain_mm (MemSlaveAdaptor)        476      178        0        0   EXACT          vivado_synth (in markov_top)
mem (MemoryMod)                12,435   32,982        0        0   EXACT          vivado_synth (in markov_top)
gen (MarkovGen)                 1,192    1,298        0        0   EXACT          vivado_synth (in markov_top)
chain (MarkovChain)             2,034    3,115        0        0   EXACT          vivado_synth (in markov_top)
system integration                  0        2        0        0   EXACT          vivado_synth (in markov_top)
total                          18,593   40,130        2        0   weakest: EXACT (u (MmCreditStreamIF))
measured total                 18,593   40,130        2        0   vivado_synth, 2026-10-11   gap +0.0% LUT, +0.0% FF, +0.0% BRAM
```

The **composed** prediction (`--composed`: each part from its own measurement, none from this
synthesis) is the one a design-space exploration has before it synthesizes anything -- and here it is
far off:

```text
u (MmCreditStreamIF)            2,689    1,833        2        0   EXACT          vivado_synth (ooc) + hls_estimate
xbar (AXIMMCrossBarIF)          1,292      808        0        0   EXACT          vivado_synth (ooc)
gen_mm (MemSlaveAdaptor)          262      219        0        0   EXACT          vivado_synth (ooc)
chain_mm (MemSlaveAdaptor)        538      188        0        0   EXACT          vivado_synth (ooc)
mem (MemoryMod)                83,341   33,001        0        0   EXACT          vivado_synth (ooc)
gen (MarkovGen)                 2,585    1,905        0        0   EXACT          hls_estimate
chain (MarkovChain)             4,423    3,030        0        0   EXACT          hls_estimate
system integration                  0        0        0        0   UNCALIBRATED   --
total                          95,130   40,984        2        0   weakest: UNCALIBRATED (system integration)
measured total                 18,593   40,130        2        0   vivado_synth, 2026-10-11   gap +411.6% LUT, +2.1% FF, +0.0% BRAM
```

Every row is `EXACT` -- an exact lookup -- and the total is still 5× the truth, which is the report's
point about keeping *how* apart from *what*:

- **A tied-off port.**  Out of context, `bram_t2p` is a full two-port memory: 83,194 LUT.  In markov
  its kernel-side port B is tied idle, and synthesis prunes everything that port reached: 8,769.  An
  out-of-context number is an **upper bound** for an instance whose ports the design ties off.
- **HLS estimates.**  The kernels' `hls_estimate` rows are ~2× their synthesized LUT (above).
- **Smearing.**  In context, the memory's read mux is reported inside `u_mem_front` (3,625 LUT, 130
  out of context): the rebuilt hierarchy moves logic across instance boundaries, so a per-instance
  in-context number is a share of the design, while the total stays exact.

FF, by contrast, composes within 2%.

From the command line (`--json` for an agent):

```bash
python -m waveflow.build.system_estimate examples.markov.markov_build:system \
    --platform <name> --top markov_top [--composed] [--json]
```

## See also

- [Composite kernels](./composite.md) -- the same three-term decomposition for one kernel.
- [XSI system simulation](../build/xsi_system.md) -- the system DAG these steps belong to.
- `plans/system_resources.md` -- the plan, its stages and its open questions.
