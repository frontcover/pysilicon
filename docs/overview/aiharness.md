---
title: The harness for AI
parent: Overview
nav_order: 2
---

# Waveflow: the harness for AI

**Waveflow is the harness that makes AI effective for hardware design.**

AI agents can generate HLS and drive design-space exploration — but they're only as good as the
substrate they work on, and raw HDL/HLS gives them none of what they need. Waveflow gives them
four:

- **Fast simulation** — vectorized, orders of magnitude faster than RTL, so an agent can try many
  designs *inside its loop* instead of waiting on one toolchain run.
- **Structured architecture** — typed schemas and well-defined interfaces make code generation
  **local**: an agent fills in one component against an explicit interface *contract*, not a
  monolithic kernel it has to get entirely right at once. This is what lets AI scale past local
  fragments to whole systems.
- **Deterministic, reproducible builds** — the build graph runs the same way every time, so a
  generated design can be rebuilt, compared, and trusted.
- **Built-in, bit-exact validation** — every result is checkable against the real toolchain, so
  the output is **verified**, not just plausible.

Waveflow is that substrate.

## AI is downstream, not the center

AI is a **first-class downstream consumer** of the representation — a real strength, and an active
area of development — but it is grounded *by* the substrate, not the center of it. The codegen
pipeline itself is deterministic (structured `hwgen`, not an LLM), and Waveflow is the substrate
**beneath** an agent, not just an orchestration layer **over** one. That is the difference between
AI output that is merely plausible and AI output you can trust.

Where AI plugs into the flow — assisting **codegen** and driving the **agentic DSE** loop — is
shown in [the Waveflow flow](./flow.md).
