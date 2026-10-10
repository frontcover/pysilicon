# Multi-agent flows for hardware design — discussion notes

**Status: DISCUSSION NOTES, not a plan.** Written 2026-10-10 at the end of the `mcp_frames` work, to
start a conversation about an open problem.  Nothing here is decided.  The claims are
labelled: **measured** (from runs in this repo), **observed** (seen once, not tested) or
**conjecture**.

## The question

So far, Waveflow has guided *one* agent through a design: choose a frame, write the
specification, stop for review, build.  The question here is whether the better long-term goal
for a harness is to help an agent **split a design task among several agents, each holding only
the context its part needs**, and whether Waveflow's structure is what makes a good split
possible.

## What prompted it

1. **Context is carried, not just read.**  *(Measured.)*  Everything an agent reads stays in its
   context and is re-sent, mostly from cache, on every later call.  In the with/without-Waveflow
   bus test (`examples/mcp_test/scale_sum_func.md`), both agents started at ~20k tokens of fixed
   context.  The Waveflow agent read ~90k tokens of references during its specification stage,
   started its build stage at 142k, and spent 12.1M of its 14.4M input tokens in that stage:
   about twice as many calls as the baseline, each carrying about twice the context.  A read's
   cost is roughly its size × the calls left in the session × the cache-read price.  The same
   holds for a synthesis report, a log, or the agent's own code.
2. **Total cost is not the problem.**  With Waveflow, cost came out roughly the same as without
   it, and optimizing tokens now would change what we are trying to measure.  But the mechanism
   points at something: what agent A reads does not sit in agent B's context.  Splitting work
   saves tokens *and* gives parallelism.
3. **Independence is a verification property.**  *(Observed.)*  The no-Waveflow agent's 1,200
   lines passed a testbench written by the same agent, with the same understanding of AXI:
   self-consistent, not verified.  The frames try to get independence *in time*, within one
   agent: freeze the specification, then build against it.  Separate agents get it
   *structurally*: an agent that writes the golden model and scenarios without ever seeing the
   implementation is an independent check by construction.
4. **Agents do not split on their own.**  *(Observed, five runs.)*  None of the blind-test agents
   launched a subagent or workflow.  The tools were in their tool list, though not on the
   harness's allowlist, so a fair test should allow them.  In the session that ran these tests,
   subagents were used only for read-only surveys.  General agents are good at fanning out
   *reads*.  Splitting *work whose parts must fit together* needs to know which interfaces are
   stable and what depends on what.

## The conjecture

A good split of a hardware design is a **partition along stable interfaces, ordered by
dependencies, weighed by cost.**  A generic harness has to guess all three.  Waveflow already
holds them in machine-readable form:

| a split needs | where it lives in Waveflow |
| --- | --- |
| components and the contracts between them | the module hierarchy, ports, schemas (`DataList`), memory-mapped views and the address map |
| what depends on what | the `BuildDag`: steps and the artifacts each consumes and produces |
| what each piece costs | the timing events (`waveflow.events`): csynth per top, XSI per block, pysim per run |
| how to do each piece | the frames, and per-block recipes |

If so, the role of structure changes.  It is not a device for saving keystrokes, which AI makes
cheap, but a device for **coordinating agents**: contracts between them, a schedule, and a cost
model.  That role does not shrink as code gets cheaper; it probably grows, because the more agents
work in parallel, the more the seams between them matter.

## Natural divisions in a design with n components

- **Per block.**  Implement and unit-verify each kernel separately.  Each agent needs its schema,
  its slice of the testbench graph, the lane routines and `loop_optimization.md`, not the whole
  system.
- **Independent verification.**  An agent writes golden models and scenarios from the spec alone,
  never seeing an implementation.
- **A reader.**  An agent turns the reference designs into a compact, complete brief (schemas, key
  API calls, build targets); the builders read the brief, with the full file one call away.
- **Per-block calibration in parallel.**  Each block's resource and cycle sweep is independent.
  This is the same partition the DSE paper relies on (`plans/paper_cg_dse_vision.md`): the
  decomposition that makes the resource model additive also makes the agents' work parallel.
- **Integration.**  An agent holds only the interfaces and each block's brief, never block
  internals, and runs the system gate.
- **Design-space search.**  A driver agent sees only `analyze_events` summaries and pysim results,
  never build output.  In a loop of hundreds of evaluations a single agent would accumulate every
  iteration's output, so here splitting is close to necessary, not just cheaper.

The specification-then-build structure of the frames already supplies most of what a clean
hand-off needs: the frame's "decided by the spec" list is the contract, and the frozen
specification-stage files are the hand-off.  Files are a better interface between agents than
conversation: durable, reviewable, and read only by the agent that needs them.

## What splitting costs

- **Hand-offs lose information.**  A brief drops a detail the next agent needs; it guesses or
  re-reads.
- **Agents drift apart.**  Two agents with different readings of an API produce pieces that do not
  fit.  The interface must be precise: a schema in a file, not prose.
- **Fixed context is paid per agent**, and overlapping reads are paid more than once.
- **Coordination is work.**  Someone sequences the agents, merges their output, and decides what
  to do when one fails.
- **Integration bugs move to the seams.**  Exactly where single-agent work was strongest.

## Evidence from this arc worth keeping in mind

- **RTL-only failures exist and are found late.**  *(Measured.)*  The m_axi pointer-FIFO deadlock
  (`plans/maxi_pointer_fifo.md`, fixed in PR #247) passed pysim and csim and hung only at RTL.
  The interleaver, a reference example, hung after 6 of 12 jobs once a test ran enough of them.
  An independent verification agent that writes *longer and nastier* scenarios than the builder
  would is one answer; the baseline agent's 95%-stall test found a Vitis bug the same way.
- **Evaluation cost bounds a search, not writing cost.**  *(Measured.)*  pysim ~0.1 s per point;
  csynth 20–60 s per top; XSI 20–30 s, ~1% of it simulating; Vitis cosim ~3 min
  (`docs/guide/build/timing_events.md`).  A scheduler that knows these costs can decide what to run
  in parallel and what to batch.

## Open questions

1. **Do general agents already split well, given the tools?**  Five runs is not evidence either way;
   the tools were not allowed.
2. **Who decides the split?**  The agent, from the design?  The harness, from the `BuildDag` and the
   hierarchy?  A human, as part of the specification?
3. **What is the unit of hand-off?**  A file per interface?  A brief per block?  A schema plus
   scenarios?
4. **How is integration verified when no agent has seen the whole design?**  The system gate and the
   trace comparison exist; is that enough?
5. **How do agents learn from each other's failures?**  If one block's agent finds a Vitis trap, how
   do the others hear about it before they step in it?
6. **Where does a human review?**  Today: one stop after the specification.  With n agents: per
   block, per interface, or once at integration?
7. **Is this a Waveflow feature, or a property of the design description** that any harness could
   read?  The latter would be a stronger claim.
8. **What does it mean for teaching?**  Students already split work along module boundaries; the
   same contracts may serve both.

## Experiments that would start answering them

- **One spec, three arms.**  A multi-component spec, for example the three-kernel `hist_pipe`
  prompt (`waveflow/mcp/frames/bus_system/prompts/03_pipeline_histogram.md`):
  1. one agent alone;
  2. agents that split the work themselves, subagents and workflows allowed;
  3. agents following a split Waveflow proposes from the design (hierarchy + DAG + costs).

  Compare tokens, wall time, whether the pieces fit at integration, and whether the independent
  verifier finds anything the single agent missed.  The blind-test harness and the timing events
  measure all of it; the harness would need to allow the subagent and workflow tools.
- **Independent verifier alone.**  The cheapest experiment: rerun a past blind test with an extra
  agent that writes scenarios from the spec without seeing the implementation, and see whether its
  scenarios break anything.
- **A design-space search driven by an agent**, single-context against split, to measure context
  growth over many iterations.

## Related

- `plans/mcp_frames.md` — the blind tests and the with/without-Waveflow comparison.
- `docs/guide/ai_tooling/frames.md` — the specification/build structure the hand-offs would reuse.
- `docs/guide/build/timing_events.md` — the costs a scheduler would use.
- `plans/paper_cg_dse_vision.md` — the per-block decomposition shared with the resource model.
- `plans/maxi_pointer_fifo.md` — an RTL-only failure, and what found it.
