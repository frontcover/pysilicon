---
title: Motivation
parent: Overview
nav_order: 1
---

# Motivation

## The problem

Designing hardware for complex algorithms is hard, and the shift toward AI-assisted design has
sharpened — not solved — a cluster of problems that compound one another:

- **AI doesn't scale to whole systems.** Large language models are excellent at generating
  small, local code fragments, but they degrade on large projects that demand consistent
  structure, architecture, and cross-cutting interfaces. Without a structured substrate to
  anchor them, their output doesn't compose into a coherent design.

- **Simulation is too slow where it matters most.** RTL simulation is essential for sign-off but
  far too slow for early architecture exploration — you can't sweep bit widths, buffering, memory
  organization, or scheduling when every run takes minutes or hours. Faster abstractions usually
  buy that speed by giving up the bit-exactness you need to trust the result.

- **The system is fragmented.** The description is spread across algorithm notebooks, architecture
  spreadsheets, simulation harnesses, ad-hoc interface code, HLS or RTL, software bindings, tests,
  and build scripts. Every design change has to be re-translated by hand across these layers, and
  much of the original intent is lost along the way.

- **Results aren't reproducible.** When builds depend on manual steps and one-off scripts,
  outcomes are hard to reproduce, regressions are hard to catch, and timing or resource numbers
  can't be traced back to the design that produced them.

- **Correctness is asserted, not demonstrated.** Generated or hand-written hardware has to be
  shown to match the algorithm model. Without a built-in, bit-exact check against a golden
  reference, correctness is a claim — and AI-generated output, in particular, is *plausible*
  rather than *verified*.

These problems reinforce each other: fragmentation makes reproducibility and verification harder,
slow simulation discourages exploration, and the lack of structure is exactly what keeps AI from
scaling past local fragments.

## Waveflow's approach: a single, executable source of truth

Waveflow attacks these together. It describes the key elements of a hardware system — **data
schemas, interfaces, components, behavior, and build relationships** — as structured Python, so
that simulation, downstream implementation, software integration, and tooling all stay aligned
around one executable model. That one move addresses each problem directly:

- **structure for AI** — typed schemas and explicit interfaces let generation stay *local* and
  contract-guided, so an agent fills in one well-bounded component at a time instead of a whole
  system at once;
- **fast, bit-exact simulation** — event-level, vectorized models run orders of magnitude faster
  than RTL while staying value-exact;
- **one source, no drift** — the model *is* the system; there is no second copy to keep in sync;
- **deterministic builds** — a `BuildDag` makes every `gen → simulate → synthesize → verify` run
  explicit and repeatable;
- **bit-exact verification** — generated hardware is checked against the Python golden,
  bit-for-bit, on the real toolchain.

The core thesis is that for many systems — especially domain-specific accelerators — the hardest
problem is **not generating RTL**. It is keeping a coherent, executable specification across
algorithms, architecture, interfaces, simulation, software, implementation, and documentation.
Waveflow is aimed at that broader problem.

## Why Python

Python is already the working language for a large fraction of algorithm development in wireless,
DSP, machine learning, scientific computing, and architecture exploration. Rather than treating
Python as a thin wrapper around external hardware tools, Waveflow uses it as the place where
system structure lives directly: types and schemas, interfaces and transactions, component
hierarchies, simulation behavior, and generated artifacts.

This lowers the barrier for domain experts who are fluent in Python but don't want to commit to a
full RTL implementation just to explore a design.

## Who it is for

- **Hardware architects** exploring system structure and interfaces before RTL lock-in.
- **Researchers in wireless, DSP, and ML** studying algorithm–hardware co-design — a more
  realistic model than a notebook, a faster workflow than RTL-first.
- **Accelerator teams** who need architecture, simulation, software interfaces, and
  implementation flows to stay aligned.
- **Tool builders** who want a structured, machine-readable hardware representation for automation
  and AI-assisted workflows.

Next: [what makes Waveflow different](./keyfeatures.md).
