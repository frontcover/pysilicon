# Plan: the system model — a `HwSystem` class, the cut as a partition, every element realized by its own hook

**Status:** discussion, 2026-10-11.  Nothing built.  Written after `plans/ip_packaging_routes.md`
Stage 0 (PR #252) gave the system top a device hierarchy, and before that plan's Stages 1-4 build
per-device synthesis and packaging on top of it.  Those stages wait on this one.

## The problem

The basis of every system flow -- XSI simulation, global synthesis, the resource estimate, and next
per-device synthesis and packaging -- is "a system", and **no class says what a system is.**  What a
downstream step needs is discovered by the consumer:

| what the framework relies on | where it is found out |
|---|---|
| the object has `.sim` and `.run()` | `system_dag.PysimStep` calls `sysm.run()`; `discover` reads `sysm.sim` |
| exactly one crossbar and one software host | `discover(sysm)` (`build/system_xsi.py:58`) filters `sim._sim_objs` by `isinstance` |
| the default cut | `discover` again: each crossbar slave's `mm_device.kernel`, then each `MemoryMod` on the crossbar |
| a kernel's device | an attribute `mm_device` that `build_mm_device` sets on views and adaptors (`hw/mm_device.py:235`) |
| which bus writers exist | `system_top_spec` follows kernels' AXIS peers to `MmStreamWriter`s (`build/system_top.py:494`); the writers belong to no module |

`MarkovSystem` and `MmFirSystem` derive from nothing.  An author learns the contract by reading
`discover` and `system_top_spec`.

**Two element types get special treatment.**
- **The crossbar**, because the walk starts from it: its slave slots and address map produce the MI
  slots, the fronts and the writers' targets.  A system with no crossbar (kernels joined only by
  streams) cannot use `system_top` at all.
- **`SwHost`**, because it is the one testbench participant with a C++ twin run as fibers.  It already
  declares `bfm_model()` like any participant, but `system_tb_spec` still adds its endpoints header by
  `isinstance` (`build/system_top.py:1010`).

Neither is a principle.  Each is the shape of the first two examples, frozen into the walk.

**And the hierarchy is inferred.**  The device grouping that Stage 0 added (`_group` in
`system_top.py`) is derived by rules from a flat graph.  It is right for every memory-mapped system we
have, but it is not something a design can state, override or nest.

## The model

    System    = the element graph: modules and interfaces, built once, simulated by run()
    cut       = a partition of that graph into DUT and TB, chosen per build
    DUT       = a hierarchy of modules and interfaces realized as RTL (kernels, leaves, vendor IP)
    TB        = everything else, realized as models beside the DUT (pysim, C++ BFMs under XSI, later the board)

This is the user's "System = TBTop + DUTTop", with three refinements that the framework already
believes or that the system flows have shown.

1. **DUT and TB are roles, not classes.**  `plans/design_cut.md` rejected `ExtMod` for exactly this
   reason: a class freezes a per-build role.  The same module is DUT in one build and TB in another.
   The unit harness (`plans/unit_harness.md`) cuts around one device, making its neighbours TB
   stand-ins.  So the system holds the graph and **declares a default cut**; a build may draw another.

2. **Interfaces are realized too.**  The DUT is not just modules:
   - the crossbar becomes a vendor IP instance;
   - a routed credit stream (`MmCreditStreamIF`) becomes two bus writers and a FIFO;
   - a deep `StreamIF` between two kernels becomes an `mm_sync_fifo`, a shallow one becomes nets;
   - an `IrqIF` becomes a wire.

   So an interface needs a realization hook just as a module does, and the cut runs through edges: the
   host's bus port is where TB meets DUT.  This is what made the crossbar look special.  It is not
   special as a crossbar; it was the first interface whose realization is an instance.

3. **The DUT hierarchy is the packaging hierarchy.**  A device is a sub-tree of the DUT; one IP per
   device (`ip_packaging_routes.md`) is one IP per node at that level.  Making the hierarchy explicit
   gives packaging, per-device synthesis and the unit harness one tree to name.

## Design

### D1. A `HwSystem` base class that states the contract

`waveflow/hw/hw_system.py`:

```python
class HwSystem(ABC):
    """A design and its environment: the element graph, simulated by run(), cut per build."""
    sim: Simulation
    def dut(self) -> HwModule: ...              # the default DUT: the root of the synthesizable tree (S1; a list in S0)
    def run(self) -> Any: ...                   # simulate the whole graph in pysim
    def elements(self) -> Iterator[SimObj]: ... # every module and interface, by walking the graph
```

- **Not an `HwModule`.**  A system is the world: DUT, testbench models and software.  It is never
  synthesized as a whole, so it should not carry a module's targets, ports or identity.
- **`dut()` is the default cut.**  A build passes another (`inside=` today, a DUT tree or sub-tree
  later).  No `isinstance` scan.
- The crossbar and the host stop being looked up.  If a flow needs the host, it is found by role: the
  TB participants with a bus master facing the DUT.

The two existing systems derive from it.  `discover()` becomes a thin shim over `HwSystem`, then goes.

### D2. The DUT as a real module tree

The DUT root is a composite `HwModule` whose children are the devices and the memory; each device is a
composite holding its kernel, its views or adaptor, and the bus writers that act for it.  For markov:

    MarkovDut
    ├── gen_dev    (MarkovGen, adaptor + views qcmd, u_crd, the forward writer and its FIFO)
    ├── chain_dev  (MarkovChain, adaptor + views qu, qresp, the credit writer)
    ├── mem        (MemoryMod: front, window, RAM)
    └── xbar       (AXIMMCrossBarIF, an internal interface of MarkovDut)

- `MmSlaveDevice` (`hw/mm_device.py:145`) becomes a composite `HwModule` registered with `add_comp`,
  instead of a side record found through `mm_device` attributes.
- Stage 0's derived grouping becomes the **default** the composites are built with; a test checks the
  tree reproduces it exactly (markov's 15 instances in the same three sub-tops).
- **Where the writers go** is decided where they are created.  `MmCreditStreamIF` builds the writers
  at `bind` time, when it knows both endpoints; it hands each writer to the device of the endpoint it
  acts for (forward writer to the sender, credit writer to the receiver).  The rule stays the same; it
  moves from the walk to the element that knows it.
- **The `ip_pkg` boundary is a node of this tree**, chosen per build like the cut: "package each child
  of the DUT root" is today's default; "package `gen_dev` and `chain_dev` together" (the direct-wired
  markov) is a different choice of nodes, not a different model.

### D3. Realization hooks for interfaces

Modules already declare how they are realized: `kernel_task()` inside the cut, `bfm_model()` and
`rtl_module()` beside it.  Interfaces have only `xsi_model()` (both ends outside) and `rtl_interfaces()`.
Add the inside case:

| interface | realization inside the DUT |
|---|---|
| `AXIMMCrossBarIF` | vendor IP: its `AxiXbarConfig` (today `AxiXbarConfig.from_crossbar`, `build/axi_xbar.py:107`), its SI and MI slots, the address map |
| `MmCreditStreamIF` | two writer modules (already children of their devices, D2) and the FIFO on the forward link |
| `StreamIF` | nets, or an `mm_sync_fifo` when its depth exceeds the default |
| `IrqIF` | a wire, or a top output when its sink is outside the cut |

The code exists already, as branches inside `system_top_spec`.  This moves each branch onto the
interface it describes.

### D4. A generic walk

`SystemTopSpec.from_system(system, cut=...)` walks the DUT tree and **asks** each element for its
realization:
- each module through `kernel_task` / `rtl_module`, or as a composite to descend into;
- each interface through D3.

The crossbar-centric code becomes the crossbar's realization; nothing in the walk names a crossbar.  A
system with no crossbar then works, because nothing requires one.

### D5. The TB side through hooks only

Every TB participant is realized through `bfm_model()` / `bfm_models()`, already the case for
`SwHost`.  The `SwHost`-only branch in `system_tb_spec` (the generated endpoints header) moves into
what `SwHost.bfm_model()` declares, so the harness generator has no participant types.

### D6. Simulation is the system's; verification is the flow's

`run()` belongs to `HwSystem`: it simulates the graph.  The `pysim` step, the scenario file and the trace
`compare` are verification choices of the XSI flow (pysim as the oracle), not part of what a system is.

## What changes for an author

Before (markov, today): a dataclass that builds a `Simulation`, devices, a crossbar and a host, and
binds them; the framework finds the crossbar and host by searching.

After: the same construction, but
- the class derives from `HwSystem`;
- the devices, memory and crossbar are added to a DUT composite, and `dut()` returns it;
- nothing else.  Device composites, writer placement and realizations come from the framework.

## Stages

Each stage keeps the 13 markov / mm_fir XSI gates (1870, 618, 611; traces identical) and, where the
Verilog can stay byte-identical, asserts that it does.

**S0 -- `HwSystem` and its contract.**  Add the base class; `MarkovSystem` and `MmFirSystem` derive
from it and implement `dut()` (returning today's cut as a flat composite, if that is the smaller step)
and `run()`.  `discover()` delegates to the system.  Gate: no behaviour change; generated Verilog
byte-identical; a test that a class missing `dut()` fails with a message naming the contract.

> **Built (2026-10-11, branch `system-model-s0`).**  `waveflow/hw/hw_system.py`, not `hw/`: a
> system owns the `Simulation` and holds software as well as hardware.  `HwSystem` is an ABC with
> abstract `dut()` and `run()`, and `elements()` over the graph.  In S0 `dut()` returns the cut as a
> **list** (markov `[gen, chain, mem]`, mm_fir `[fir]`).  A flat composite would re-parent the
> modules, which is S1's calibration-key question, so the tree root waits for S1.  `discover()`
> refuses a non-`HwSystem` by naming the contract and takes the cut from `dut()`.  It still finds the
> crossbar and the host by type, until S3/S4.

**S1 -- devices as composites.**  `build_mm_device` returns a composite `HwModule`; `MmCreditStreamIF`
places its writers in the devices at `bind`.  The DUT root holds the devices, the memory and the
crossbar.  `SystemTopSpec`'s grouping is read from the tree, not derived.  Gate: the tree reproduces
Stage 0's grouping exactly; Verilog byte-identical; **calibration keys unchanged** (see Open).

> **Built (2026-10-12).**
> - `MmSlaveDevice` is an `HwModule` composite (device named `<kernel>_dev`).  Its children are the
>   kernel, the views and the adaptor, and its interfaces are the view streams.
> - `MmCreditStreamIF.bind` adds each writer, and the stream feeding it, to the nearest `MmSlaveDevice`
>   ancestor of the endpoint's kernel.  The ancestor climb is needed because `MarkovChain`'s `s_u`
>   belongs to its child `chain_core`.  A kernel in no device leaves its writer unplaced, since any
>   other composite would take the writer as a task.
> - `DutTop` (`waveflow/hw/hw_system.py`) is the root.  `DutTop.of(name, sim, comps=, ifs=)`
>   builds it.  `HwSystem.dut()` returns it and `HwSystem.cut()` flattens it for `inside=` (each device's
>   kernel, then each memory).
> - `system_top._group` reads kernels' and writers' devices off the tree.  The stream-feeding rule
>   remains only as the fallback for a writer in no device.
>
> **Gates:**
> - Verilog byte-identical for markov and both mm_fir shapes, with and without probes (212,241 bytes);
> - pysim end times identical;
> - **all 59 calibration keys identical**.  `parent` is in `_CONTEXT_ATTRS`, and the views'
>   `mm_device` attribute did not move them either;
> - a test moves the credit writer to the other device by hand: the sub-tops follow the tree, and the
>   link then crosses as ports of both.

**S2 -- interface realization hooks.**  D3 for the four interfaces; `system_top_spec`'s branches move
onto them.  Gate: Verilog byte-identical.

> **Built (2026-10-12), for three of the four:**
> - `AXIMMCrossBarIF.rtl_config(name, id_width=)` returns its `AxiXbarConfig` (the vendor IP);
> - `StreamIF.rtl_fifo_depth(between_kernels=)` returns the `mm_sync_fifo` depth, or 0 for nets;
> - `MmCreditStreamIF.writers()` returns its two writers.  `_channel_of` finds a writer's channel
>   through it, instead of matching bus-master identities.
>
> `system_top_spec` asks the hooks.  It no longer calls `from_crossbar` or applies the depth rule.
> `IrqIF` got no hook: "a line whose sink is outside the cut becomes a top output" is a fact about the
> cut, not about the line, so it stays in the walk.  The walk's *order* (SI then MI slots from the
> crossbar, writers found from kernels' stream peers) is still crossbar-shaped.  Removing that is S3.
> Gate: Verilog and harness byte-identical.

**S3 -- the generic walk.**  D4.  The walk names no interface or module type.  Gate: Verilog
byte-identical, plus one new system with **no crossbar** (two kernels joined by a stream, host on
streams) through `system_rtl` and XSI.

> **First step built (2026-10-12): the bus and the host are found by role** (D1).  `discover()` no
> longer filters elements by `AXIMMCrossBarIF` / `SwHost`:
> - the bus is the one link of the DUT tree that declares `rtl_config` (S2);
> - the host is the bus master on it whose owner is outside the DUT tree (the walk climbs `parent`).
>
> No module in the system flow (`system_*.py`, `sw_host_gen.py`, `mm_device.py`) tests for either
> type now.  Gate: Verilog and harness byte-identical; a test removes the crossbar from the DUT and
> sees the refusal.
>
> **Not built: the generic walk itself.**  `system_top_spec` still starts from the bus and lays out SI
> and MI slots as a crossbar does.

**S3 revised (2026-10-12, with the user): the crossbar is optional, and the cut may run through any
port with a BFM.**  The first draft of the gate (a no-crossbar system with a software host on
streams) asked for the wrong thing.  Three things had been conflated:

| | required? |
|---|---|
| a crossbar *inside* the DUT (how devices reach each other) | **no**: one kind of link among several; kernels may be joined by streams |
| an AXI boundary between the DUT and the testbench | **no**: any port kind with a BFM dual (AXI-Stream, `m_axi`, AXI-MM, interrupts), exactly as the single-kernel flow already allows through `tb_top_spec` |
| a *software* host reaching the DUT by bus and interrupts | **yes, for now**: that is the shape of the fiber runtime, and it is what the board has (the PS reaches the fabric through AXI and interrupts).  A stream-facing software host is deferred until a design needs one |

So a system's testbench may hold BFM models as well as, or instead of, a software host, and the DUT's
ports that face them are top ports of their own kind.

**Vendor IP may sit on either side of the cut** (the user's RFDC case):
- **inside**: the crossbar.  Its realization is generated IP (`rtl_config`, S2), synthesized with the
  DUT;
- **outside**: the RFDC.  It cannot be brought into the DUT as vendor IP today, so the DUT stops at its
  AXI-Stream ports, and the RFDC is a testbench participant: a pysim model, and a C++ BFM under XSI
  through its `bfm_model()` (`waveflow/hw/rfdc.py`; per port, `RfdcAdcMaster` / `RfdcDacSlave`).
  That is how the RF examples work today at kernel scale (`RfBlkDelayTB`, a composite `FreeRunMod`
  testbench).

A testbench participant therefore has up to three realizations: a pysim model, an XSI model (a C++ BFM
or the host's C++ program), and **on the board**, the real thing beside the DUT.  For the host that is
PS software; for the RFDC it is the RFDC IP, a cell of the block design wired to the DUT IP's
AXI-Stream ports.  `ip_packaging_routes.md` Stage 4 (the block-design generator) has to place those
testbench-side vendor IPs as block-design cells, not only the DUT's devices.

**The revised S3 work, in order** (branch `system-model-s3`, after PR #253 merged S0-S2, S4-S6):
0. **Rename `System` → `HwSystem`**, moved to `waveflow/hw/hw_system.py` beside `hw_module.py`.
   The object is not XSI-specific: pysim runs it, synthesis reads its DUT, packaging will build
   from it.  So `XsiSystem` would be too narrow, and `System` stays free for a larger scope (SALSA's
   boards and tiles).  Done first, while there are two subclasses.  *(Built.)*
1. **The walk without a bus.**  `system_top_spec` takes the DUT tree; the crossbar, when present, is
   one link realized through `rtl_config`.  A DUT port not on the bus becomes a top port of its own
   kind (AXIS, `m_axi`, BRAM), as `composite_top_spec` already derives a single kernel's boundary.
2. **The harness for any participant.**  `system_tb_spec` binds each participant's ports to whatever
   top port they face (an SI slot, an interrupt, or now a stream or `m_axi` port), the way
   `tb_top_spec` binds a single kernel's.  The host stays optional: a system may have none.
3. **Discovery by role, generalized.**  `discover()` stops requiring exactly one bus and one host:
   zero or one bus; the testbench is every participant outside the DUT tree, each realized by its
   `bfm_model()`.
4. **mem_copy, then an RF example, as `HwSystem`s** (the gates below).
5. **The docs, once there is one mechanism.**
   - `flows/concurrent_layers.md` rewritten around `HwSystem` at both scales, in DUT / testbench
     vocabulary.  "RTL top" becomes "the DUT's Verilog", and the "XSI simulation top" is retired.
   - A kernel-scale figure there: mem_copy as a system with no crossbar, in `system_cut.tex`'s
     colours.  The colours are the three kinds of module (blue kernel, green RTL, tan BFM) plus
     purple for an interface realized as vendor IP.
   - The flow-steps diagram redrawn from the `HwSystem` and recoloured to match.

**Gates:**
- markov and mm_fir unchanged: Verilog and harness byte-identical, the 18 XSI gates;
- **mem_copy as a `HwSystem` with no crossbar**: its `dut()` is the kernel, and its testbench is the
  stream driver, the sink and the memory BFM.  It passes its existing gate (2908 cycles, bit-exact)
  through the system flow.  This is also the first step of folding the single-kernel mechanism into
  `HwSystem` (Open);
- **an RF example as a `HwSystem`**, the RFDC in the testbench through its `bfm_model()`, passing its
  existing gate.  This checks the vendor-IP-outside case.

**S4 -- the TB side through hooks.**  D5.  Gate: the harness is byte-identical; `system_tb_spec` names
no participant type.

> **Built (2026-10-12, before S2/S3: it was independent and small).**  `BfmModel.generated_files`:
> `(file name, render)` pairs a model needs generated beside the harness, rendered when the harness
> is.  `SwHost.bfm_model()` declares its `<Host>_endpoints.h` there, and `system_tb_spec` collects
> every model's files instead of `isinstance(p, SwHost)`.  Gate: the harness for markov and both
> mm_fir shapes, with and without probes (30 files, 61,873 bytes), is byte-identical; a test checks
> that `system_tb_spec`'s source names no participant type.

**S5 -- cuts other than the default.**  A build passes a DUT sub-tree: one device as the DUT, its
neighbours as TB.  This is the unit harness's first need, and the per-device synthesis's.  Gate: one
markov device through `system_rtl` with its neighbours as BFMs (the RTL builds; running it is
`unit_harness.md`'s job).

> **First half built (2026-10-12): a sub-top as a unit.**
> - `SystemTopSpec.render_sub_verilog(name)` (`render_sub_top`) renders one device or the memory as
>   a standalone file: exactly the module the whole top holds for it.
> - `sources_of(name)` gives its compile list (leaf sources and csynth'd modules).
> - `system_xsi.elaborate_sub_top(spec, name, root=, work_dir=)` compiles and elaborates it with only
>   those sources.
> - New XSI gates elaborate markov's three sub-tops and mm_fir's device in both shapes alone
>   (`WANT_XSI_GATES` 168 → 173).  The whole top's Verilog is still byte-identical.
>
> Still open: the second half, a cut that *is* a sub-tree, with the neighbours as testbench models.
> It needs the walk to render a crossbar outside the cut (the device's AXI ports as top ports facing
> BFMs).  That is S3's generic walk, or `unit_harness.md`'s first stage.  The standalone sub-top is
> also what `ip_packaging_routes.md` Stage 1 (per-device out-of-context synthesis) builds on.

**S6 -- docs.**  `build/xsi_system.md` opens with `HwSystem` (what a system must provide, D1), then the
cut, then `SystemTopSpec` as the result of the walk.  A guide page on writing a system replaces "read
`discover`".  `flows/index.md`'s two flows are the cut drawn two ways.

> **Built (2026-10-12), except the last sentence:**
> - `xsi_system.md` opens with "The system: `HwSystem`": the contract table (`sim`, `dut()`, `run()`,
>   `cut()`, `elements()`), the DUT as a tree of devices, markov's class as the example, and DUT and
>   testbench as roles.  Then `SystemTopSpec`, whose "Constructing it" now takes `HwSystem.cut()`.
> - The page summary and `api:` name `HwSystem` / `DutTop` / `MmSlaveDevice`.
> - The markov example's system page has "Declaring the DUT" (S1).
>
> Not done: recasting `flows/index.md`'s two flows as "the cut drawn two ways".  The sequential and
> concurrent flows differ by the DUT's *kind* (control-driven or free-running), which is not a cut, so
> the sentence needs your call before it goes into the guide.
## Open

- **Two mechanisms for one idea (found 2026-10-12, writing the flows docs).**  A single-kernel XSI
  testbench is a composite `FreeRunMod` (`MemCopyTB`, `potential_targets = {sequential_xsi_tb}`),
  whose children the cut splits into the RTL top and the BFMs.  A system is a `HwSystem` whose `dut()`
  is a tree.  They are the same idea at two scales, and a single-kernel testbench is a system with no
  crossbar: exactly S3's missing gate.  Folding the first into the second would leave the flows guide
  one mechanism to teach.  `flows/concurrent_system.md` ("Two scales, two mechanisms today")
  describes both as they are and points here.
- **Calibration keys.**  `HwModule` keeps its RTL registries out of the dataclass fields so that
  calibration keys stay stable (`hw/hw_module.py:368-375`).  Re-parenting kernels under device
  composites may change paths or structure signatures, and with them the record-store keys.  S1 must
  measure this first (key every kernel and leaf before and after) and keep the keys stable, or re-key
  the stores deliberately.
- **Is the DUT root one composite or a node set?**  One composite is simplest.  A cut that is not a
  sub-tree (two devices out of three) is then a set of nodes.  Decide in S5 from the unit harness's
  need.
- **Direct-wired markov.**  At device granularity its credit stream crosses two devices
  (`ip_packaging_routes.md` Open).  With D2, the answer is a choice of tree: both kernels in one device
  composite.  Confirm when it is first packaged.
- **`Simulation`'s flat object list** stays.  The tree is the structure; the list is the scheduler's.
- **Off-chip memory** (`plans/offchip_memory.md`) is excluded from `discover`'s default cut today.  Under
  D1 it is simply not in `dut()`.

## Relationship to other plans

- `plans/design_cut.md` -- the cut is a build choice and roles are not classes.  This plan applies that
  to systems and extends realization hooks to interfaces.
- `plans/ip_packaging_routes.md` -- paused after Stage 0.  Its Stages 1-4 resume on the DUT tree (D2)
  and the generic walk (D4): per-device synthesis and packaging name tree nodes.
- `plans/unit_harness.md` -- needs S5 (a device as the DUT, neighbours as TB).
- `plans/system_dag.md`, `plans/host_runtime.md`, `plans/xsi_system_top.md` -- `add_system_steps`,
  `run_system_xsi` and the system top take a `HwSystem` instead of "anything with `.sim`".
- `plans/offchip_memory.md` -- its exclusion from the default cut becomes "not in `dut()`".
