# Plan: the system model — a `System` class, the cut as a partition, every element realized by its own hook

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

### D1. A `System` base class that states the contract

`waveflow/simulation/system.py`:

```python
class System(ABC):
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

The two existing systems derive from it.  `discover()` becomes a thin shim over `System`, then goes.

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

`run()` belongs to `System`: it simulates the graph.  The `pysim` step, the scenario file and the trace
`compare` are verification choices of the XSI flow (pysim as the oracle), not part of what a system is.

## What changes for an author

Before (markov, today): a dataclass that builds a `Simulation`, devices, a crossbar and a host, and
binds them; the framework finds the crossbar and host by searching.

After: the same construction, but
- the class derives from `System`;
- the devices, memory and crossbar are added to a DUT composite, and `dut()` returns it;
- nothing else.  Device composites, writer placement and realizations come from the framework.

## Stages

Each stage keeps the 13 markov / mm_fir XSI gates (1870, 618, 611; traces identical) and, where the
Verilog can stay byte-identical, asserts that it does.

**S0 -- `System` and its contract.**  Add the base class; `MarkovSystem` and `MmFirSystem` derive
from it and implement `dut()` (returning today's cut as a flat composite, if that is the smaller step)
and `run()`.  `discover()` delegates to the system.  Gate: no behaviour change; generated Verilog
byte-identical; a test that a class missing `dut()` fails with a message naming the contract.

> **Built (2026-10-11, branch `system-model-s0`).**  `waveflow/simulation/system.py`, not `hw/`: a
> system owns the `Simulation` and holds software as well as hardware.  `System` is an ABC with
> abstract `dut()` and `run()`, and `elements()` over the graph.  In S0 `dut()` returns the cut as a
> **list** (markov `[gen, chain, mem]`, mm_fir `[fir]`).  A flat composite would re-parent the
> modules, which is S1's calibration-key question, so the tree root waits for S1.  `discover()`
> refuses a non-`System` by naming the contract and takes the cut from `dut()`.  It still finds the
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
> - `DutTop` (`waveflow/simulation/system.py`) is the root.  `DutTop.of(name, sim, comps=, ifs=)`
>   builds it.  `System.dut()` returns it and `System.cut()` flattens it for `inside=` (each device's
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

**S3 -- the generic walk.**  D4.  The walk names no interface or module type.  Gate: Verilog
byte-identical, plus one new system with **no crossbar** (two kernels joined by a stream, host on
streams) through `system_rtl` and XSI.

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

**S6 -- docs.**  `build/xsi_system.md` opens with `System` (what a system must provide, D1), then the
cut, then `SystemTopSpec` as the result of the walk.  A guide page on writing a system replaces "read
`discover`".  `flows/index.md`'s two flows are the cut drawn two ways.

## Open

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
  `run_system_xsi` and the system top take a `System` instead of "anything with `.sim`".
- `plans/offchip_memory.md` -- its exclusion from the default cut becomes "not in `dut()`".
