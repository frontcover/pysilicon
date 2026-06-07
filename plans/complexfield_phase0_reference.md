# ComplexField — Phase 0 reference (read-only, PAUSE for review)

Settles the four load-bearing design points before any code: **value representations**,
the **Waveflow int-complex C++ struct**, the **`cmult`/`cadd` result-format rules** (reusing
the FixedField derivation), and the **float-complex-multiply edge** to verify empirically.
Plus the conformance case matrix. Everything below is grounded in the merged code
(`fixpoint.py`, `fixputils.py`, `dataschema.py`, `examples/schemas/fixedpoint/`).

---

## 0. What we compose (verified in the tree)

- `FixedField(IntField)` carries `(W=bitwidth, I=int_bits, signed, q_mode, o_mode)` on its
  `specialize` class; `.val` is the **stored W-bit integer** (`np.int64`/`np.uint64`).
  (`waveflow/hw/fixpoint.py:33`)
- Fixed arithmetic = **free functions** `mult/add/sub/quantize/fixed_sum` over
  `DataArray[FixedField]`, each deriving the result `Format` from `fixputils`
  (`fixpoint.py:153`). The `Format` constructor fires the **>64-bit guard**
  (`fixputils.Format.__post_init__`).
- Result-format derivation (`fixputils`):
  - `mult_format(a,b)` → `Format(Wa+Wb, Ia+Ib, signed_a or signed_b)` — fractions add.
  - `add_format(a,b)` → `frac=max(Fa,Fb)`, `int=max(Ia,Ib)+1`, `signed_a or signed_b`.
  - `sub_format(a,b)` → same widths as add, **always signed**.
  - All three call `_require_same_sign` → **mixed signed/unsigned raises** (inherited).
- The **operator layer (PR #56)**: `DataArray.__mul__/__add__/__sub__` →
  `_datarray_binop` → `_elem_kind` dispatch. `fixed` lowers to `fixpoint.mult/add/sub`;
  `int` to `_int_binop` (grows `Wa+Wb` / `max+1`, 64-bit fail-fast); `float` to
  `_float_binop` (numpy passthrough, no growth). (`dataschema.py:4254`+)
- **Conformance rig** (`examples/schemas/fixedpoint/`): `build_cases()` →
  `gen_case_sources()` (writes `kernel.cpp`, `in_a.txt`, `in_b.txt`, `expected.json`,
  `run.tcl`) → `csim_and_compare()` (runs Vitis csim, compares emitted stored bits to the
  Python golden bits, `exact == zero LSB disagreement`). Driven by `BuildDag` +
  `run_dag_cli`. Kernels in `kernels.py` reconstruct operands bit-for-bit via `.range()`,
  compute full-precision + quantize-on-assign, emit `y.range(W-1,0)`.
- Fraction oracle pattern (`tests/utils/test_fixputils.py`): exact rational arithmetic on
  stored ints → `to_bits`, asserted bit-equal to the vectorized path.

ComplexField **adds nothing to the fixed math** — it calls these on the re/im components.

---

## 1. Value representations (per inner)

A `ComplexField` is **two inner components (re, im) of the *same* inner format**.

| inner | Python `.val` dtype | rationale |
|-------|--------------------|-----------|
| **float32** | `np.complex64` | native numpy complex; `.real`/`.imag` are `float32` |
| **float64** | `np.complex128` | native numpy complex |
| **fixed / int** | **numpy structured dtype** `np.dtype([('re', D), ('im', D)])` where `D = np.int64` (signed) / `np.uint64` (unsigned) | numpy has **no integer-complex dtype**; this is the "custom type". `v['re']`/`v['im']` are int-array views → vectorized, loop-free. |

**Decision: structured dtype, not a `ComplexInt` wrapper.** It is interleaved re/im in
memory (matches `std::complex<T>` and numpy complex), vectorizes natively (`v['re']`,
`v['im']`), and `init_value()` is just `np.zeros(shape, dtype=struct)`. The component
field type `D` follows the inner exactly as `IntField`/`FixedField.init_value()` does
(`int64`/`uint64`), so re/im are stored ints in the inner's own format — `cmult`/`cadd`
operate on `v['re']`/`v['im']` arrays via the existing `fixputils` ops with **zero new
fixed-point math**.

`DataArray[ComplexField]` reuse: for float the element init is an `np.complex64/128`
scalar (an `np.generic`) → `DataArray.init_value` makes a flat complex array. For
fixed/int the element init is a **structured scalar** (`np.void`, also an `np.generic`) →
flat structured array. No parallel array class. (`dataschema.py:2534`+)

---

## 2. Interleaved I/Q serialization

`width = 2 × inner.bitwidth`. Pack **re first, then im** — `inner.serialize(re)` then
`inner.serialize(im)` — so a `DataArray[ComplexField]` maps directly onto a
`std::complex<T>` array (contiguous `[real, imag]`, arrays interleaved).

- **fixed/int**: re/im are stored ints already → reuse `IntField`'s W-bit word packing for
  each, low half = re, high half = im.
- **float**: re = `value.real`, im = `value.imag`, each serialized via `FloatField`'s
  IEEE bit-view (`_value_to_field_bits`). complex64 → two 32-bit words; complex128 → two
  64-bit words.

C++ side reconstructs `re = words.low`, `im = words.high` and assembles the complex value.

---

## 3. C++ mapping per inner

| inner | C++ type | arithmetic kernel |
|-------|----------|-------------------|
| **float** | `std::complex<float>` (csim) / `complex_wrapper<float>` synth-float = Future | `std::complex<float>` `operator*`/`+` directly (this is the float edge — §5) |
| **fixed** | `std::complex<ap_fixed<W,I,Q,O>>` — the synthesizable headline | **explicit 4-multiply component formula** at the grown format (§4), *not* `std::complex::operator*` (which would quantize back — see note) |
| **int** | **Waveflow-emitted complex struct** (`std::complex<ap_int>` is non-standard) | explicit component formula on the two `ap_int<W>` |

### 3a. The Waveflow int-complex struct (emitted into the kernel)

```cpp
template <int W>
struct wf_cint {
    ap_int<W> re;
    ap_int<W> im;
};
// (unsigned inner emits the ap_uint<W> variant for round-trip; see §4 on cmult/conj sign)
```

For round-trip we only need the struct + bit (de)serialization via `.range(W-1,0)` on each
field. For arithmetic we **do not** rely on a templated `operator*` (kept simple and
explicit, mirroring `render_binop`): the kernel reads `re`/`im` stored bits, runs the
component formula in `ap_int`, declares the result re/im at the **grown** width, writes the
bits. (`std::complex<ap_int>` is intentionally avoided — the standard only specifies
`std::complex` for float/double, so int-complex is Waveflow's own struct.)

> **Critical note — why fixed arithmetic does NOT use `std::complex::operator*`.**
> `std::complex<T>::operator*` returns `std::complex<T>` — the **same T** — so
> `std::complex<ap_fixed<W,I>> * std::complex<ap_fixed<W,I>>` **quantizes the product back
> to `ap_fixed<W,I>`** at the inner Q/O modes. That contradicts our full-precision-growth
> model (req. 3a). So the fixed/int **arithmetic kernels emit the explicit component
> formula** with the result re/im declared at the grown `ap_fixed<Wr,Ir>` (full precision,
> bit-exact with the composed Python `cmult`/`cadd`). The operands and result are still
> `std::complex<ap_fixed>` values (req. 1 "vs std::complex<ap_fixed>"); only the multiply
> is spelled out so the growth is captured rather than discarded. The **round-trip**
> kernel uses `std::complex<ap_fixed>` directly.

---

## 4. `cmult` / `cadd` / `conj` — formulas + result-format derivation

Composition only — re/im are passed to the inner field's existing ops. Let the inner
format be `F` (the same for both components of an operand).

### cadd
```
re = ar + br ;  im = ai + bi
```
Each is `fixpoint.add` → result inner format `R = add_format(F_a, F_b)`
(`int=max+1`, `frac=max`, `signed = signed_a or signed_b`). re and im share `R`.
**Float**: numpy `a.val + b.val` (no growth). **Unsigned inner: fine** (sign-preserving).

### cmult
```
re = ar·br − ai·bi
im = ar·bi + ai·br
```
Compose per term, then combine:
```
P  = mult_format(F_a, F_b)            # (Wa+Wb, Ia+Ib, signed_a or signed_b)
re = sub(ar·br, ai·bi)  → sub_format(P,P) = (2W+1, 2I+1, signed=True)   [same-format inputs]
im = add(ar·bi, ai·br)  → add_format(P,P) = (2W+1, 2I+1, signed=P.signed)
```
For a **signed inner** (`F_a==F_b==F`): `re` and `im` both land at
`R = Format(2W+1, 2I+1, signed=True)` — identical → that is the ComplexField result inner.
This is exactly what Vitis `ap_fixed<W,I> a*b → ap_fixed<2W,2I>`, then
`ap_fixed<2W,2I> ± ap_fixed<2W,2I> → ap_fixed<2W+1,2I+1>` produces, so the explicit-formula
kernel is bit-exact. Inherits the **64-bit guard**: a 32-bit fixed inner → `2·32+1=65` →
`Format` raises (fail-fast), as intended.

**conj**
```
re = ar ;  im = −ai          (im negated → result is signed)
```

### Unsigned inner — the one sign subtlety (recommendation, please confirm)

`cmult`/`conj` **produce signed results** (`re = ar·br − ai·bi` and `im = −ai` can be
negative); for an unsigned inner the re (signed) and im (unsigned) component formats would
disagree and the im sum needs an extra bit to live in a signed format — messy and exotic.

**Recommendation for v1:** `cadd` supports unsigned inner; **`cmult`/`conj` require a
signed inner** (raise `NotImplementedError("complex multiply/conj produce signed results;
use a signed inner")`). Round-trip conformance covers signed **and** unsigned; arithmetic
conformance (cmult/cadd) uses **signed** inners (the realistic complex case — RF/FFT data
is signed). `cadd` is also exercised on an unsigned inner. *(Alternative if you prefer:
promote unsigned→signed inside cmult/conj with a widened result format. I lean to the
raise — cleaner, keeps bit-exactness obvious, matches "complex is signed".)*

### Operator surface (sugar over the functions, like FixedField)
`ComplexField` plugs into the merged operator layer: extend `_elem_kind` with a `"complex"`
kind (duck-typed on a ComplexField marker) and add `_complex_binop`:
- `*` → `cmult`, `+` → `cadd`, `-` → `csub` (cadd with negated b; v1 may expose only `+`/`*`
  per req. 6 "v1 ops: cadd, cmult, conj" — `conj` stays a free function).
- For **float** complex, `_complex_binop` is numpy native (`a.val * b.val`).
- For **fixed/int**, it lowers to `cmult`/`cadd` which call `fixpoint.mult/add/sub` (resp.
  `_int_binop`) on the `['re']`/`['im']` views — **no reimplemented fixed math**.

---

## 5. The float-complex-multiply edge (verify empirically — do not assume)

Round-trip for float is exact (IEEE bit-view both ways). The **edge is the multiply**:

> Does numpy `complex64` (and `complex128`) elementwise `*` equal Vitis/libstdc++
> `std::complex<float>` (`<double>`) `operator*` **bit-for-bit** for finite operands?

Both *nominally* use the naive formula `(ac−bd) + (ad+bc)i` with per-op IEEE rounding, but
they can differ if either side: uses an FMA, computes an intermediate in `double`, or takes
the C99 Annex G nan/inf fixup branch. We **test with finite normal operands** (no
inf/nan/subnormal-stress in v1), compare emitted bits, and:
- if bit-exact → assert it (headline: "float arithmetic confirmed");
- if a rounding/ordering edge appears → **document the exact tolerated case** and pin it
  (per working convention). We do **not** silently loosen.

`cadd` for float is `(ar+br)+(ai+bi)i` — independent IEEE adds, expected exact; still
verified.

---

## 6. Conformance case matrix (`examples/schemas/complex/`)

Reuse the rig verbatim: `ComplexConfig` (inner config + kind), `build_cases()` →
`gen_case_sources` → `csim_and_compare`, `BuildDag` (`GenConformanceStep` /
`RunConformanceStep`), `run_dag_cli`, `@pytest.mark.vitis` with a real Vitis run (skip
**only** if `toolchain.find_vitis_path()` is None — no soft-skip otherwise).

New kernel renderers in `examples/schemas/complex/kernels.py`:
- `render_complex_quantize_real` — load (re,im) doubles into the complex type (round-trip).
- `render_complex_cmult` / `render_complex_cadd` — explicit component formula; result re/im
  at the grown format; per inner (`std::complex<ap_fixed>`, `std::complex<float>`,
  `wf_cint`). Interleaved I/Q in `in_*.txt`: re then im per element.

| inner | round-trip | cmult | cadd | configs |
|-------|-----------|-------|------|---------|
| **fixed (signed)** | ✓ vs `std::complex<ap_fixed>` | ✓ (headline bit-exact) | ✓ | curated: `s4_2`, `s8_4`, `s16_8`, `s8_8`, `s8_0` (the FixedField set; arithmetic on a subset whose `2W+1 ≤ 64`) |
| **fixed (unsigned)** | ✓ vs `std::complex<ap_ufixed>` | — (signed-only, §4) | ✓ | `u8_4` |
| **int** | ✓ vs `wf_cint` | ✓ vs `wf_cint` | ✓ | a couple of signed int widths (e.g. `s8`, `s16`) |
| **float32** | ✓ vs `std::complex<float>` | ✓ **(the edge — §5)** | ✓ | — |
| **float64** | ✓ vs `std::complex<double>` | ✓ (the edge) | ✓ | — |

Golden bits via the **Fraction oracle composed on re/im**: exact rational
`re = ar·br − ai·bi`, `im = ar·bi + ai·br` on stored ints → `to_bits` at the grown format
(fixed/int); numpy reference for float. Same oracle drives the Python unit tests (Phase 1)
and the `expected.json` golden (Phase 4).

---

## 7. Phase deltas (where code lands)

- **P1** `waveflow/hw/complexfield.py`: value core + `cadd`/`cmult`/`conj` (vectorized;
  compose `fixpoint`/`_int_binop` for fixed/int, numpy for float) + Fraction-oracle tests.
- **P2** `ComplexField(DataField)` + interleaved I/Q (de)serialize + `DataArray[ComplexField]`
  + operator-layer hook (`_elem_kind` "complex", `_complex_binop`). Round-trip / arith /
  format-derivation tests.
- **P3** codegen: `cpp_type` per inner (`std::complex<...>` / emitted `wf_cint`) +
  bit (de)serialization exprs + complex-arithmetic kernels; csim-compiles per inner.
- **P4** `examples/schemas/complex/` conformance — **bit-exact on real Vitis** (fixed+int),
  float confirmed. **MILESTONE, PAUSE.**
- **P5** `docs/guide/schema/complex.md` (nav 7; bump `codegen` to 8).

---

## Decisions confirmed (locked for Phase 1)

1. **Unsigned inner + cmult/conj → RAISE.** `cadd` supports unsigned inner; `cmult`/`conj`
   require a signed inner (`NotImplementedError`). Round-trip conformance covers signed +
   unsigned; arithmetic (cmult/cadd) conformance is signed (cadd also exercised on `u8_4`).
2. **Expose `-` (csub).** Operator surface = `+` (cadd), `-` (csub = cadd with negated b),
   `*` (cmult); `conj` as a free function. Symmetric with the FixedField operator surface.
   csub result inner = `sub_format(F,F)` (always signed) — the inner-format sub rule.
3. **Int conformance widths = `s8` and `s16`** (signed). cmult grows `s8→17`, `s16→33` bits
   — both within the 64-bit guard. Round-trip also covers these; arithmetic on both.
