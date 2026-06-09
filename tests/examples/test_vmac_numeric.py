"""VMAC numeric-model tests — the datapath format spec + requantize == ap_fixed.

VMAC is **complex-only** and the format is **structural** (on :class:`VmacAccel`).  Two
things are proven here, all in Python (no Vitis):

1. **The datapath format derivation** — ``VmacAccel.accumulator_format`` / ``output_format``
   match an *independent*, hand-derived format algebra (product widens; the β·C add grows an
   integer bit; the row reduction adds ``⌈log₂ n_rows⌉``; complex's ``cmult`` / ``conj`` add
   their extra bit in the *integer* part).  The output sits at the input fractional scale
   ``F_out = F_in`` (so ``I_out = out_bw − F_in``), and the requantize shift is *derived*:
   ``SHIFT = F_acc − F_in`` (= ``F_in`` when ``b_one`` else ``2·F_in``).

2. **requantize == an ap_fixed assignment** — the golden's requantize (a ``fixputils.quantize``
   format conversion that drops ``SHIFT`` fractional bits) is bit-identical to the hardware
   ``ap_fixed<out_bw, …, q_mode, o_mode> y = acc >> SHIFT`` (round + saturate), checked against
   **two independent references**: an int-domain shift/round/saturate and a ``Fraction``
   value-domain quantize — over rounding ties, saturation, negatives, signed, and ``SHIFT = 0``.

Plus the fail-loud guards (ACC_BW too small, out_bw too small) and a width × (q, o)
end-to-end sweep against the independent oracle.
"""
import math
from fractions import Fraction

import numpy as np
import pytest

from examples.vmac.vmac import VmacAccel
from waveflow.hw.dataschema import DataArray
from waveflow.hw.fixpoint import FixedField
from waveflow.utils import complexutils as cx
from waveflow.utils.fixputils import Format
from tests.examples.test_vmac_golden import _cfg, _pair, run


# --- independent ap_fixed references for the requantize ----------------------
def hw_shift_round_sat(acc_stored, shift, out_bw, q_rnd, o_sat):
    """Independent int-domain ``ap_fixed<out_bw,…> y = acc >> shift`` (round half up,
    then saturate / wrap), signed — the literal hardware op the kernel must hit."""
    acc_stored = int(acc_stored)
    if shift == 0:
        q = acc_stored
    else:
        q = acc_stored >> shift                          # arithmetic floor (toward -inf)
        if q_rnd:
            q += (acc_stored >> (shift - 1)) & 1         # round half up (tie -> +inf)
    lo, hi = -(1 << (out_bw - 1)), (1 << (out_bw - 1)) - 1
    if o_sat:
        return max(lo, min(hi, q))
    y = q & ((1 << out_bw) - 1)                          # two's-complement wrap
    return y - (1 << out_bw) if (y >> (out_bw - 1)) & 1 else y


def frac_value_quant(acc_stored, acc_frac, out_frac, out_bw, q_rnd, o_sat):
    """Independent value-domain reference: quantize the exact real value
    ``acc_stored·2^-acc_frac`` into ``Format(out_bw, out_bw - out_frac)``."""
    value = Fraction(int(acc_stored), 1) / (Fraction(2) ** acc_frac)
    scaled = value * (Fraction(2) ** out_frac)
    q = math.floor(scaled + Fraction(1, 2)) if q_rnd else math.floor(scaled)
    lo, hi = -(1 << (out_bw - 1)), (1 << (out_bw - 1)) - 1
    if o_sat:
        return max(lo, min(hi, q))
    y = q & ((1 << out_bw) - 1)
    return y - (1 << out_bw) if (y >> (out_bw - 1)) & 1 else y


def _craft_values(acc_W, shift):
    """Stored-int accumulator values that stress the requantize: rounding ties (and ±1
    around them), negatives, and large magnitudes that overflow out_bw into saturation."""
    half = (1 << (shift - 1)) if shift > 0 else 0
    vals = set()
    for base in range(-6, 7):
        v = base << shift
        vals.update([v, v + half, v - half, v + half - 1, v + half + 1, v + 1, v - 1])
    lo, hi = -(1 << (acc_W - 1)), (1 << (acc_W - 1)) - 1
    vals.update([lo, hi, lo // 2, hi // 2, 0, -1, 1])    # extremes -> saturation
    return np.array(sorted(v for v in vals if lo <= v <= hi), dtype=np.int64)


# (acc_W, acc_I, out_bw, shift) — all valid: 0 <= out_frac = (acc_W-acc_I)-shift <= out_bw
_REQUANT_CONFIGS = [
    (16, 8, 12, 0),     # SHIFT = 0 (no rounding; pure narrow + saturate)
    (16, 8, 8, 4),
    (16, 8, 8, 8),      # out_frac = 0 (integer output)
    (24, 12, 8, 8),
    (24, 12, 10, 4),
    (12, 6, 8, 6),      # out_frac = 0
    (20, 4, 8, 12),
    (32, 16, 12, 10),
]


@pytest.mark.parametrize("acc_W,acc_I,out_bw,shift", _REQUANT_CONFIGS)
@pytest.mark.parametrize("q_rnd,o_sat", [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_requantize_complex_equals_ap_fixed(acc_W, acc_I, out_bw, shift, q_rnd, o_sat):
    """The complex requantize re/im each == the int-domain and value-domain references.
    (Spans arbitrary acc_W/shift to stress the requantize primitive, independent of the
    structural format that selects a specific shift end-to-end.)"""
    acc_fmt = Format(acc_W, acc_I, True)
    acc_frac = acc_fmt.frac_bits
    out_frac = acc_frac - shift
    out_cls = FixedField.specialize(out_bw, out_bw - out_frac, True, *(_qo(q_rnd, o_sat)))
    re = _craft_values(acc_W, shift)
    im = np.roll(re, 3)
    from waveflow.hw.complexfield import ComplexField
    t = DataArray.specialize(ComplexField.specialize(FixedField.specialize(acc_W, acc_I, True)),
                             max_shape=(len(re),))(cx.make_complex(re, im, acc_fmt))
    out = VmacAccel._requantize(t, out_cls).val
    for comp, src in (("re", re), ("im", im)):
        hw = [hw_shift_round_sat(s, shift, out_bw, q_rnd, o_sat) for s in src]
        frac = [frac_value_quant(s, acc_frac, out_frac, out_bw, q_rnd, o_sat) for s in src]
        np.testing.assert_array_equal(np.asarray(out[comp]), hw)    # int-domain shift/round/sat
        np.testing.assert_array_equal(np.asarray(out[comp]), frac)  # value-domain quantize


def _qo(q_rnd, o_sat):
    from waveflow.utils.fixputils import OMode, QMode
    return (QMode.AP_RND if q_rnd else QMode.AP_TRN, OMode.AP_SAT if o_sat else OMode.AP_WRAP)


# --- format derivation: independent hand-derived algebra ----------------------
def _expected_acc(data_bw, int_bits, b_one, b_conj, c_zero, reduce_rows, n_rows):
    """Independent (W, I) accumulator-format derivation (complex), the rules by hand."""
    def mul(A, B):                                       # cmult: +1 int bit (sub_format)
        return (A[0] + B[0] + 1, A[1] + B[1] + 1)

    def add(A, B):                                       # aligned add (+1 int bit)
        frac = max(A[0] - A[1], B[0] - B[1])
        ints = max(A[1], B[1]) + 1
        return (ints + frac, ints)

    in_f = (data_bw, int_bits)
    if b_one:
        ab = in_f
    else:
        op_b = (data_bw + 1, int_bits + 1) if b_conj else in_f   # conj = sub_format(in, in)
        ab = mul(in_f, op_b)
    acc = mul(in_f, ab)
    if not c_zero:
        acc = add(acc, mul(in_f, in_f))
    if reduce_rows:
        growth = (n_rows - 1).bit_length()               # ceil(log2 n_rows)
        acc = (acc[0] + growth, acc[1] + growth)
    return acc


_FLAG_COMBOS = [
    dict(b_one=0, b_conj=0, c_zero=0, reduce_rows=0),
    dict(b_one=1, b_conj=0, c_zero=0, reduce_rows=0),
    dict(b_one=0, b_conj=0, c_zero=1, reduce_rows=0),
    dict(b_one=1, b_conj=0, c_zero=1, reduce_rows=1),
    dict(b_one=0, b_conj=1, c_zero=1, reduce_rows=1),
    dict(b_one=0, b_conj=1, c_zero=0, reduce_rows=0),
    dict(b_one=0, b_conj=0, c_zero=0, reduce_rows=1),
]


@pytest.mark.parametrize("flags", _FLAG_COMBOS)
@pytest.mark.parametrize("data_bw,int_bits,n_rows", [(8, 4, 5), (12, 8, 8), (6, 3, 4)])
def test_accumulator_format_matches_hand_derivation(flags, data_bw, int_bits, n_rows):
    accel = VmacAccel(mem_dwidth=512, mem_awidth=32, data_bw=data_bw,
                      int_bits=int_bits, acc_bw=128, out_bw=data_bw)
    cmd = accel.Cmd()
    cmd.n_rows, cmd.n_cols = n_rows, 2
    for k, v in flags.items():
        setattr(cmd, k, v)
    acc = accel.accumulator_format(cmd)
    exp_W, exp_I = _expected_acc(data_bw, int_bits, **flags, n_rows=n_rows)
    assert (acc.W, acc.int_bits) == (exp_W, exp_I)
    assert acc.signed is True
    assert acc.frac_bits == (2 if flags["b_one"] else 3) * (data_bw - int_bits)  # F = depth·F_in


def test_output_format_structural_scale_and_codegen_target():
    # F_out = F_in (structural); derived shift = F_acc - F_in.
    accel = VmacAccel(mem_dwidth=512, mem_awidth=32, data_bw=8, int_bits=4, acc_bw=64, out_bw=12)
    cmd = accel.Cmd()
    cmd.n_rows, cmd.n_cols = 4, 2
    cmd.b_one, cmd.c_zero = 0, 1                          # F_acc = 3·4 = 12 (complex)
    acc = accel.accumulator_format(cmd)
    out = accel.output_format(cmd)
    f_in = int(accel.data_bw) - int(accel.int_bits)
    assert out.get_format().frac_bits == f_in                       # F_out = F_in (structural)
    assert out.int_bits == accel.out_bw - f_in                      # I_out = out_bw - F_in
    assert accel.derived_shift(cmd) == acc.frac_bits - f_in         # SHIFT = F_acc - F_in
    assert out.get_bitwidth() == accel.out_bw
    assert out.cpp_type == f"ap_fixed<12, {out.int_bits}, AP_TRN, AP_WRAP>"   # Phase-3 target


def test_accumulator_format_matches_execute_invariant():
    # execute() asserts (and would raise) if the derived accumulator format disagrees with
    # the operator-composed actual; a passing run proves the two coincide.
    got, exp = run(_cfg(in_bw=8, int_bits=4, out_bw=8, b_conj=1, reduce_rows=1),
                   _pair([[3, -4]], [[1, 2]]), _pair([[2, 1]], [[-1, 1]]),
                   _pair([[0, 0]], [[0, 0]]), _pair(16, 0), _pair(0, 0))
    np.testing.assert_array_equal(got[0], exp[0])
    np.testing.assert_array_equal(got[1], exp[1])


# --- width × (q, o) end-to-end sweep vs the oracle (complex-only) -------------
def _rand(rng, data_bw, shape):
    hi = (1 << (data_bw - 1)) - 1
    return rng.integers(-hi, hi + 1, shape, dtype=np.int64)


# (data_bw, int_bits, out_bw, acc_bw)
_WIDTHS = [(8, 4, 8, 64), (10, 5, 10, 64), (12, 8, 12, 80), (6, 3, 8, 48)]


@pytest.mark.parametrize("data_bw,int_bits,out_bw,acc_bw", _WIDTHS)
@pytest.mark.parametrize("q_rnd,o_sat", [(0, 0), (1, 1)])
def test_width_sweep_matches_oracle(data_bw, int_bits, out_bw, acc_bw, q_rnd, o_sat):
    rng = np.random.default_rng(hash((data_bw, int_bits, q_rnd, o_sat)) & 0xFFFF)
    n, m = 4, 3

    def op():
        return _pair(_rand(rng, data_bw, (n, m)), _rand(rng, data_bw, (n, m)))

    aimm, bimm = (8, -4), (-6, 3)               # immediates must fit data_bw
    base = dict(in_bw=data_bw, int_bits=int_bits, out_bw=out_bw, acc_bw=acc_bw,
                q_rnd=q_rnd, o_sat=o_sat)
    for flags in ({"c_zero": 1}, {}, {"b_one": 1}, {"reduce_rows": 1, "c_zero": 1},
                  {"b_conj": 1, "c_zero": 1, "reduce_rows": 1}):
        cfg = _cfg(**base, **flags)
        got, exp = run(cfg, op(), op(), op(), _pair(*aimm), _pair(*bimm))
        np.testing.assert_array_equal(got[0], exp[0], err_msg=f"re {cfg} {flags}")
        np.testing.assert_array_equal(got[1], exp[1], err_msg=f"im {cfg} {flags}")


# --- fail-loud guards ---------------------------------------------------------
def _accel(**kw):
    base = dict(mem_dwidth=512, mem_awidth=32, data_bw=8, int_bits=4, acc_bw=64, out_bw=8)
    base.update(kw)
    return VmacAccel(**base)


def _cmd(accel, *, b_one=0, c_zero=1, reduce_rows=0, n_rows=4):
    cmd = accel.Cmd()
    cmd.n_rows, cmd.n_cols = n_rows, 2
    cmd.b_one, cmd.c_zero, cmd.reduce_rows = b_one, c_zero, reduce_rows
    return cmd


def test_failloud_acc_bw_too_small():
    accel = _accel(data_bw=8, int_bits=4, acc_bw=10, out_bw=8)   # full-MAC acc ~ 25 bits > 10
    cmd = _cmd(accel, b_one=0, c_zero=0)
    with pytest.raises(ValueError, match="exceeds acc_bw"):
        accel.output_format(cmd)


def test_failloud_out_bw_too_small_for_fraction():
    # F_in = data_bw - int_bits = 8; out_bw = 4 < F_in -> I_out < 0.
    accel = _accel(data_bw=8, int_bits=0, acc_bw=64, out_bw=4)
    cmd = _cmd(accel, b_one=1)
    with pytest.raises(ValueError, match="too small"):
        accel.output_format(cmd)


def test_failloud_propagates_through_execute():
    accel = _accel(data_bw=8, int_bits=4, acc_bw=10, out_bw=8)
    cmd = _cmd(accel, b_one=0, c_zero=0)
    mem = np.zeros(256, dtype=np.int64)
    cmd.a = {"addr": 0, "row_stride": 2}
    cmd.b = {"addr": 8, "row_stride": 2}
    cmd.c = {"addr": 16, "row_stride": 2}
    cmd.d = {"addr": 24, "row_stride": 2}
    cmd.alpha = {"direct": 1, "re": 1, "im": 0, "addr": 0, "stride": 0}
    cmd.beta = {"direct": 1, "re": 1, "im": 0, "addr": 0, "stride": 0}
    with pytest.raises(ValueError, match="exceeds acc_bw"):
        accel.execute(cmd, mem)
