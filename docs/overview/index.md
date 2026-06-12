---
title: Overview
parent: Waveflow
nav_order: 1
has_children: true
---

# Overview

Waveflow is a **Python-native framework for algorithm, hardware, and software co-design**.
Instead of fragmenting a hardware system across algorithm notebooks, architecture spreadsheets,
simulation harnesses, HDL, software bindings, and build scripts, Waveflow describes it once as
**structured, executable Python** — so simulation, generated implementation, software, and
tooling all stay aligned around a single model.

This section covers the *why*, the *what*, and the *how* — plus the concrete system that motivates
Waveflow and an honest status:

- **[Motivation](./motivation.md)** — the problem, Waveflow's single-source approach, and who it's for.
- **[The Python model](./pymodel.md)** — what a Waveflow component *is*: schemas, interfaces,
  parameters, and a compute hook.
- **[The Waveflow flow](./flow.md)** — the two-loop design methodology, end to end.
- **[The harness for AI](./aiharness.md)** — why Waveflow is the substrate that makes AI effective
  for hardware design.
- **[SALSA](./salsa.md)** — the reconfigurable wireless system Waveflow was built for.
- **[Project status](./status.md)** — what works today, what's next, and the first integrated milestone.

New to the project? The fastest way in is the
[basic vectorization example](../examples/basic_vec/) — one multiply-accumulate, bit-exact from
Python to Vitis.
