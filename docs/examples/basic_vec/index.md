---
title: Basic Vectorization (MAC)
parent: Examples
nav_order: 1
has_children: true
---
# Basic Vectorization — one MAC, bit-exact

`basic_vec` is the **front-door for [vectorization](../../guide/vectorization/)**: the
smallest demonstration that a Python-vectorized golden model and a vectorized Vitis kernel
produce **the same bits**. It is a *data/schema* example — how to **represent and compute on
data**, before any module-to-module interface is introduced.

The whole example is one elementwise multiply-accumulate, computed over arrays (no
per-element Python loop) for each of the three numeric kinds, with the result asserted equal
to Vitis C-sim **bit-for-bit**:

```python
y = a * b + c
```

| kind | Python | Vitis kernel | bit-exact because |
|------|--------|--------------|-------------------|
| **integer** | growth-aware operators (`Int17` result) | `ap_int<17> y = a*b + c;` | integer arithmetic is exact; the operators track the growth |
| **float** | numpy `float32` passthrough | `a*b + c`, built `-ffp-contract=off` | same two roundings (no fused FMA) |
| **fixed** | `quantize(a*b + c, Q)` | `ap_fixed<8,4> y = a*b + c;` | full precision, then quantize-on-assign |

This is the **teaching** counterpart to the rigorous all-modes/all-widths sweep in
`examples/schemas/fixedpoint` — the two share the same conformance machinery (`BuildDag` +
`run_dag_cli` + gen→csim→compare-bits). The *concepts* (operators, the two paths, growth
rules) live in the [vectorization guide](../../guide/vectorization/); this walkthrough shows
them end-to-end on one example.

## The walkthrough

1. **[The Python model](./python.md)** — the vectorized golden: declare the arrays, apply
   `a*b + c`, derive the result type, emit the golden bits.
2. **[The Vitis equivalent](./vitis.md)** — the hand-written C++ kernels that mirror the op.
3. **[Confirming the match](./eval.md)** — the build DAG, the Vitis C-sim, the bit comparison.

## File map

In [`examples/basic_vec/`](../../../examples/basic_vec/):
- `basic_vec_build.py` — the three MAC cases + the gen→csim→compare conformance DAG.
- `kernels.py` — the three minimal, **hand-written** Vitis kernels (int / float / fixed).
- `run.tcl` — the Vitis C-sim driver (`-ffp-contract=off` for the float kernel).

## Running it

```bash
python examples/basic_vec/basic_vec_build.py --through gen   # kernels + vectors + golden (no Vitis)
python examples/basic_vec/basic_vec_build.py --through run   # the bit-exact csim conformance (Vitis)
```

The `run` stage asserts, per kind, that the Vitis output bits equal the Python operator bits
**exactly**; any mismatch stops the build.
