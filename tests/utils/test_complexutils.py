"""Exhaustive tests for the complex value core (:mod:`waveflow.utils.complexutils`).

Every fixed/int complex op is proven bit-equal to an independent exact **Python-int**
oracle composed on the re/im stored-int components (the FixedField integer oracle, lifted
to complex).  Float ops are checked vs a numpy reference.  The >64 guard is proven to fire
on a derived complex format that would exceed int64, and the unsigned cmult/conj raise is
proven.
"""
import numpy as np
import pytest

from waveflow.utils import complexutils as cx
from waveflow.utils.complexutils import (
    cadd, cadd_format, cmult, cmult_format, complex_dtype, conj, conj_format, csub,
    csub_format, im_of, int_format, make_complex, re_of,
)
from waveflow.utils.fixputils import Format, add_format, mult_format, sub_format

# --- curated signed inners (fixed: W/I; int: I==W) ----------------------------
FIXED = [Format(4, 2, True), Format(8, 4, True), Format(8, 8, True),
         Format(8, 0, True), Format(16, 8, True)]
INTS = [int_format(8, True), int_format(16, True)]
SIGNED = FIXED + INTS


def _codes(fmt, n=9):
    """A spread of valid stored ints (within the signed W-bit range), as an array."""
    lo, hi = -(1 << (fmt.W - 1)), (1 << (fmt.W - 1)) - 1
    return np.linspace(lo, hi, n).astype(np.int64)


def _operand(fmt, seed):
    """A structured complex array of stored ints for inner ``fmt``."""
    re = _codes(fmt)
    im = np.roll(_codes(fmt), seed)
    return make_complex(re, im, fmt)


# --- exact Python-int oracles (composed on re/im) -----------------------------
def _shift_add(x, fx, y, fy, fr, sign):
    return [(int(a) << (fr - fx)) + sign * (int(b) << (fr - fy)) for a, b in zip(x, y)]


def oracle_cadd(va, a, vb, b, sign=+1):
    fr = (add_format if sign > 0 else sub_format)(a, b).frac_bits
    re = _shift_add(re_of(va), a.frac_bits, re_of(vb), b.frac_bits, fr, sign)
    im = _shift_add(im_of(va), a.frac_bits, im_of(vb), b.frac_bits, fr, sign)
    return re, im


def oracle_cmult(va, a, vb, b):
    ar, ai, br, bi = re_of(va), im_of(va), re_of(vb), im_of(vb)
    re = [int(r1) * int(r2) - int(i1) * int(i2)
          for r1, i1, r2, i2 in zip(ar, ai, br, bi)]
    im = [int(r1) * int(i2) + int(i1) * int(r2)
          for r1, i1, r2, i2 in zip(ar, ai, br, bi)]
    return re, im


# --- representation -----------------------------------------------------------
def test_complex_dtype_and_pack_roundtrip():
    fmt = Format(8, 4, True)
    assert complex_dtype(fmt) == np.dtype([("re", np.int64), ("im", np.int64)])
    assert complex_dtype(Format(8, 4, False)) == np.dtype([("re", np.uint64), ("im", np.uint64)])
    re, im = np.array([1, -2, 3]), np.array([-4, 5, -6])
    v = make_complex(re, im, fmt)
    np.testing.assert_array_equal(re_of(v), re)
    np.testing.assert_array_equal(im_of(v), im)


def test_make_complex_shape_mismatch_raises():
    with pytest.raises(ValueError):
        make_complex(np.array([1, 2]), np.array([1]), Format(8, 4, True))


# --- format derivation --------------------------------------------------------
def test_format_derivation_rules():
    a, b = Format(8, 4, True), Format(8, 2, True)
    assert cadd_format(a, b) == add_format(a, b)
    assert csub_format(a, b) == sub_format(a, b)
    p = mult_format(a, b)
    assert cmult_format(a, b) == sub_format(p, p)
    # signed, 2W+1 / 2I+1 for equal inners
    assert cmult_format(a, a) == Format(2 * 8 + 1, 2 * 4 + 1, True)
    assert conj_format(a) == Format(8 + 1, 4 + 1, True)
    # int inner: 2W+1
    assert cmult_format(int_format(8), int_format(8)) == Format(17, 17, True)
    assert cmult_format(int_format(16), int_format(16)) == Format(33, 33, True)


# --- arithmetic vs oracle -----------------------------------------------------
@pytest.mark.parametrize("fmt", SIGNED, ids=lambda f: f"W{f.W}I{f.int_bits}")
def test_cadd_csub_match_oracle(fmt):
    va, vb = _operand(fmt, 1), _operand(fmt, 3)
    for op, sign, fmt_fn in [(cadd, +1, cadd_format), (csub, -1, csub_format)]:
        out, r = op(va, fmt, vb, fmt)
        assert r == fmt_fn(fmt, fmt)
        assert out.dtype == complex_dtype(r)
        ore, oim = oracle_cadd(va, fmt, vb, fmt, sign)
        np.testing.assert_array_equal(re_of(out), ore)
        np.testing.assert_array_equal(im_of(out), oim)


@pytest.mark.parametrize("fmt", SIGNED, ids=lambda f: f"W{f.W}I{f.int_bits}")
def test_cmult_matches_oracle(fmt):
    if cmult_format(fmt, fmt).W > 64:
        pytest.skip("derived width > 64 (guard tested separately)")
    va, vb = _operand(fmt, 2), _operand(fmt, 5)
    out, r = cmult(va, fmt, vb, fmt)
    assert r == cmult_format(fmt, fmt)
    assert out.dtype == complex_dtype(r)
    ore, oim = oracle_cmult(va, fmt, vb, fmt)
    np.testing.assert_array_equal(re_of(out), ore)
    np.testing.assert_array_equal(im_of(out), oim)


def test_cadd_different_formats_aligns():
    a, b = Format(8, 4, True), Format(8, 2, True)        # different frac -> alignment
    va, vb = _operand(a, 1), _operand(b, 2)
    out, r = cadd(va, a, vb, b)
    assert r == add_format(a, b)
    ore, oim = oracle_cadd(va, a, vb, b, +1)
    np.testing.assert_array_equal(re_of(out), ore)
    np.testing.assert_array_equal(im_of(out), oim)


@pytest.mark.parametrize("fmt", SIGNED, ids=lambda f: f"W{f.W}I{f.int_bits}")
def test_conj_matches_oracle(fmt):
    va = _operand(fmt, 4)
    out, r = conj(va, fmt)
    assert r == conj_format(fmt)
    np.testing.assert_array_equal(re_of(out), re_of(va).astype(np.int64))
    np.testing.assert_array_equal(im_of(out), -im_of(va).astype(np.int64))


def test_cmult_w64_no_wrap():
    # s31_* inner -> cmult W = 2*31+1 = 63 (<= 64); products must stay exact in int64.
    fmt = Format(31, 16, True)
    re = np.array([2**30 - 1, -(2**30), 12345, -67890], dtype=np.int64)
    im = np.array([-(2**30), 2**30 - 1, -54321, 9876], dtype=np.int64)
    va = make_complex(re, im, fmt)
    vb = make_complex(np.roll(re, 1), np.roll(im, 1), fmt)
    out, r = cmult(va, fmt, vb, fmt)
    assert r.W == 63
    ore, oim = oracle_cmult(va, fmt, vb, fmt)
    np.testing.assert_array_equal(re_of(out), ore)   # exact (Python big-int oracle)
    np.testing.assert_array_equal(im_of(out), oim)


# --- guards -------------------------------------------------------------------
def test_cmult_over_64_raises():
    fmt = Format(32, 16, True)                            # cmult -> W=65
    va = make_complex(np.array([1]), np.array([1]), fmt)
    with pytest.raises(NotImplementedError):
        cmult(va, fmt, va, fmt)
    with pytest.raises(NotImplementedError):
        cmult(make_complex(np.array([1]), np.array([1]), int_format(32)), int_format(32),
              make_complex(np.array([1]), np.array([1]), int_format(32)), int_format(32))


def test_unsigned_cmult_conj_raise():
    fmt = Format(8, 4, False)
    va = make_complex(np.array([1, 2]), np.array([3, 4]), fmt)
    with pytest.raises(NotImplementedError):
        cmult(va, fmt, va, fmt)
    with pytest.raises(NotImplementedError):
        conj(va, fmt)


def test_mixed_sign_raises():
    a, b = Format(8, 4, True), Format(8, 4, False)
    va = make_complex(np.array([1]), np.array([1]), a)
    vb = make_complex(np.array([1]), np.array([1]), b)
    with pytest.raises(NotImplementedError):
        cadd(va, a, vb, b)


# --- float path ---------------------------------------------------------------
@pytest.mark.parametrize("dt", [np.complex64, np.complex128])
def test_float_arithmetic_vs_numpy(dt):
    a = np.array([1 + 2j, -3 + 0.5j, 0 - 1j, 2.5 + 2.5j], dtype=dt)
    b = np.array([0.5 - 1j, 4 + 2j, -2 + 3j, 1 + 1j], dtype=dt)
    np.testing.assert_array_equal(cx.cadd_float(a, b), a + b)
    np.testing.assert_array_equal(cx.csub_float(a, b), a - b)
    np.testing.assert_array_equal(cx.cmult_float(a, b), a * b)
    np.testing.assert_array_equal(cx.conj_float(a), np.conj(a))
    assert cx.cmult_float(a, b).dtype == dt          # no growth
