---
title: Basic Vectorization (MAC)
parent: Examples
nav_order: 1
has_children: false
---
# Basic Vectorization — one MAC, bit-exact

`basic_vec` is the **front-door for [vectorization](../../guide/vectorization/)**:
the smallest possible demonstration that a Python-vectorized golden model and a
vectorized Vitis kernel produce **the same bits**. It is a *data/schema* example —
it teaches how to **represent and compute on data**, before any module-to-module
interface is introduced.

The whole example is one elementwise multiply-accumulate:

```python
y = a * b + c
```

computed over arrays — **no per-element Python loop** — for each of the three
numeric kinds, with the result asserted equal to a Vitis C-sim **bit-for-bit**:

- **integer** — growth-aware operators (`a*b` is `Wa+Wb` wide, `+c` adds a carry
  bit); the Python `Int17` result matches `ap_int<17> y = a*b + c;`.
- **float** — numpy `float32` passthrough; the kernel is built with
  `-ffp-contract=off` so its `a*b + c` is the same **two roundings** numpy does.
- **fixed** — full-precision `a*b + c` then one explicit `quantize` back to the
  working format, matching `ap_fixed<...> y = a*b + c;`.

This is the **teaching** counterpart to the rigorous all-modes/all-widths sweep in
`examples/schemas/fixedpoint`; the two share the same conformance machinery
(`BuildDag` + `run_dag_cli` + gen→csim→compare-bits).

## What it demonstrates

- The two computing paths from the [vectorization guide](../../guide/vectorization/):
  the **type-preserving operators** (`a*b + c`, full-precision growth, explicit
  `quantize`) producing a golden bit-vector, versus the raw numpy `.val` escape.
- That keeping data in numpy arrays end-to-end is what makes functional simulation
  **fast *and* bit-exact** — the core Waveflow differentiator.

## File map

The example lives in [`examples/basic_vec/`](../../../examples/basic_vec/):

- `basic_vec_build.py` — builds the three MAC cases: computes each Python golden
  with the operators, derives the result type, renders the matching kernel, and runs
  the gen→csim→compare-bits conformance DAG.
- `kernels.py` — the three minimal vectorized Vitis kernels (int / float / fixed)
  over the *same* `a*b + c`, deliberately readable (the guide pulls from them).
- `run.tcl` — the Vitis HLS C-simulation driver (`-ffp-contract=off` for the float
  kernel).

## Running it

```bash
# Generate kernels + input vectors + the Python golden bits (no Vitis needed):
python examples/basic_vec/basic_vec_build.py --through gen

# The full bit-exact conformance against Vitis C-sim (requires Vitis HLS):
python examples/basic_vec/basic_vec_build.py --through run
```

The `run` stage asserts, per kind, that the Vitis output bits equal the Python
operator bits **exactly**; any mismatch stops the build.

## Next

- [Vectorization guide](../../guide/vectorization/) — the selling point, the two
  paths, and the per-kind detail (integer / float / fixed) this example embodies.
- [Fixed-point (FixedField)](../../guide/schema/fixpoint.md) — the fixed-point type
  behind the fixed case.
