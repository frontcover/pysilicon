---
title: What a crossbar costs
parent: Examples
nav_order: 9.552
example_dir: examples/xbar_profile
summary: "A measurement census rather than a design: AMD's axi_crossbar in every small shape (1x2 to 4x4), both connectivity modes (SAMD crossbar, SASD shared bus) and two data widths, each synthesized, placed and routed out of context at 250 MHz and run under XSI between host and memory BFMs. The area, the timing-closure margin and the latency of every configuration are filed into a platform's module record store -- the same records a resource model fits from -- and the tables on this page are rendered back out of that store."
---

# What a crossbar costs

[mm_fir](../mm_fir/) put AMD's `axi_crossbar` between a host and a kernel, and the
[crossbar guide](../../guide/interface/axi_mm/crossbar.md) measured what a transaction through it costs
in cycles. Two questions were left open: **how much fabric** a crossbar takes, and **how both answers
change** with its shape and its connectivity mode. This example measures them for every small
configuration and keeps the numbers where a model can use them.

The crossbar has two architectures (`AxiXbarConfig(mode=...)`):

| mode | what it is |
|---|---|
| **SAMD** -- shared address, multiple data | a true crossbar: one address arbiter, but a data path per master/slave pair, so different pairs transfer at once. The IP's default, and what every other example builds. |
| **SASD** -- shared address, shared data | a shared bus: one transaction at a time across the whole interconnect, for the least area. |

## What is measured

Every point of a grid -- shapes `1x2`, `2x1`, `2x2`, `3x3`, `4x4` (masters x slaves), both modes,
data widths 32 and 64 -- goes through four rungs
([`xbar_profile_build.py`](../../../examples/xbar_profile/xbar_profile_build.py)):

| rung | what it does | costs |
|---|---|---|
| `ip` | `create_ip` for the configuration (`generate_axi_xbar`) | ~20 s, cached |
| `synth` | out-of-context synthesis, place and route at 4 ns (`synth_axi_xbar`); utilization after each, routed setup/hold slack, logic levels, critical path | ~4 min, cached |
| `latency` | the IP under XSI, a host BFM on every master port and a memory BFM on every slave; the probe set below | ~30 s |
| `records` | files the measurements into the platform's module record store | -- |

There is no `1x1`: the IP refuses it, and a crossbar with one master and one slave routes nothing.
`1x2` (a host reaching two slaves) and `2x1` (two masters sharing a memory) are the small shapes
designs actually build.

The **latency probes** (`xbar_latency_probes`) each start every operation in the same cycle and
report the span from the first address phase to the last response, inclusive:

| probe | traffic | what it shows |
|---|---|---|
| `w1`, `r1` | master 0 to slave 0, one beat, alone | the per-transaction cost |
| `w16`, `r16` | the same with 16 beats | the per-beat cost |
| `par_w16`, `par_r16` | master *k* to slave *k*, for every *k*, at once | whether different pairs overlap -- the SAMD/SASD difference |
| `same_w16`, `same_r16` | every master to slave 0 at once | arbitration: bursts to one slave serialize in either mode |

Every read checks it gets back what its write stored, so a probe that moved the wrong data fails
rather than reporting a latency.

## The results

<!-- XBAR_TABLES -->

## Reading them

<!-- XBAR_READING -->

## The part: a stand-in for the RFSoC

The board this is for is the RFSoC 4x2 (`xczu48dr-ffvg1517-2-e`). Synthesizing for an RFSoC needs a
Vivado license that covers the device, and the no-cost edition does not -- `synth_design` stops with
*"A valid license was not found for feature 'Synthesis' and/or device 'xczu48dr'"*, while `create_ip`
and XSI work. So the numbers above are measured on the closest part the no-cost edition covers, the
ZCU104's **`xczu7ev-ffvc1156-2-e`**:

- **same fabric** -- both are Zynq UltraScale+ (CLB, LUT6, the same routing architecture);
- **same speed grade**, -2;
- **the same circuit.** `create_ip` builds the crossbar's RTL from the *family*
  (`C_FAMILY = zynquplus`), and generating it for both parts gives byte-identical synthesis wrapper,
  HDL library and simulation model.

So the latencies are exactly the RFSoC's (it is the same RTL), the area should match to within
placement noise, and the closure margin is the one number to re-measure on the real part. With a
license that covers it:

```bash
python -m examples.xbar_profile.xbar_profile_build --part xczu48dr-ffvg1517-2-e --platform \
    --shape 4x4 --mode SAMD --dw 64
```

Records carry the part they were measured on (their provenance), and the work-tier platform is named
for it (`zu7ev_xbar_250mhz`), so nothing measured on the stand-in can pass for an RFSoC number.

**Out of context, only internal paths are timed.** The crossbar's ports have no partition pins, so
Vivado times its register-to-register paths and leaves the port-to-register ones unconstrained. A
slack here says the crossbar's own logic closes at 250 MHz; whether a system closes depends on what
sits on either side of it.

## Where the numbers live

Not in this page, and not in a results file. Each measurement is a **record** in the platform's
module store (`waveflow/calib/record_store.py`), filed under the crossbar's content key:

```python
from waveflow.calib.record_store import ModuleStore
from examples.xbar_profile.xbar_profile import xbar_config

cfg = xbar_config("4x4", "SAMD", 64)
ident = cfg.identity()                    # axi_crossbar-<digest of the hardware fields>
store = ModuleStore("examples/xbar_profile/calib/work/zu7ev_xbar_250mhz")
store.best(ident.key, "resource", identity=ident).payload    # vivado_impl: lut, ff, ..., wns_ns, met
store.read(ident.key, "timing", identity=ident)              # one xsi record per probe
```

- `identity()` hashes the hardware -- mode, shape, widths, address windows -- and not the name or the
  part, so two designs that build the same crossbar share a key and a synthesis.
- A key holds a `vivado_synth` and a `vivado_impl` resource record and one `xsi` timing record per
  probe, `{features: {op, beats, masters, slaves}, cycles}`. `ModuleStore.best` prefers the routed
  number.
- The tables above are rendered from the store by
  [`xbar_profile_table.py`](../../../examples/xbar_profile/xbar_profile_table.py), the way any
  later reader would see it.

That is what makes a **crossbar resource model** a small step rather than a project:
`corpus_from_records(store, cls_name="AxiCrossbar")` is already the frame one fits from, with
`n_si`, `n_mi`, `data_width` and `mode` as its columns. The sweep writes to an untracked work-tier
platform; `waveflow_calib publish` promotes a platform into the tracked library when someone decides
the numbers belong there (see [the platform workflow](../../guide/platform/workflow.md); the record store is [per-module records](../../guide/calib/modules.md)).

## Running it

```bash
python -m examples.xbar_profile.xbar_profile_sweep --dry-run   # IPs only: no synthesis license needed
python -m examples.xbar_profile.xbar_profile_sweep             # the census, ~1.5 h (cached per point)
python -m examples.xbar_profile.xbar_profile_sweep --shape 4x4 --resume
python -m examples.xbar_profile.xbar_profile_table             # the tables, from the store

python -m examples.xbar_profile.xbar_profile_build --shape 3x3 --mode SASD --dw 32   # one point
```

Needs Vivado (`create_ip`, synthesis, xsim) and a C++ compiler for the XSI testbench -- the mingw
`g++` bundled with Vivado on Windows. The plan this implements is
`plans/interconnect_platform_model.md`.
