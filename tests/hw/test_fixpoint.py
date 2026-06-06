"""Phase 2: FixedField (ap_fixed) DataSchema field on the IntField base.

Covers specialization/cpp_type, Vitis-matching defaults, quantization-on-assignment
(delegated to fixputils), and that the reused IntField W-bit serialization
round-trips the stored bits exactly — including inside a DataList struct.
"""
import numpy as np
import pytest

from pysilicon.hw.dataschema import DataList, IntField
from pysilicon.hw.fixpoint import FixedField
from pysilicon.utils import fixputils
from pysilicon.utils.fixputils import AP_RND, AP_SAT, AP_TRN, AP_WRAP


def test_specialize_attrs_and_cpp_type():
    F = FixedField.specialize(8, 4)
    assert (F.bitwidth, F.int_bits, F.signed) == (8, 4, True)
    assert (F.q_mode, F.o_mode) == (AP_TRN, AP_WRAP)
    assert F.cpp_type == "ap_fixed<8, 4, AP_TRN, AP_WRAP>"

    U = FixedField.specialize(8, 4, signed=False)
    assert U.cpp_type == "ap_ufixed<8, 4, AP_TRN, AP_WRAP>" and U.signed is False

    M = FixedField.specialize(16, 8, q_mode=AP_RND, o_mode=AP_SAT)
    assert M.cpp_type == "ap_fixed<16, 8, AP_RND, AP_SAT>"


def test_defaults_match_vitis():
    assert (FixedField.q_mode, FixedField.o_mode, FixedField.signed) == (AP_TRN, AP_WRAP, True)


def test_specialize_is_cached():
    assert FixedField.specialize(8, 4) is FixedField.specialize(8, 4)
    assert FixedField.specialize(8, 4) is not FixedField.specialize(8, 4, q_mode=AP_RND)


def test_default_value_is_zero():
    assert FixedField.specialize(8, 4)().val == 0.0


@pytest.mark.parametrize("W,I,signed,q,o", [
    (8, 4, True, AP_TRN, AP_WRAP),
    (8, 4, False, AP_TRN, AP_WRAP),
    (8, 4, True, AP_RND, AP_SAT),
    (16, 8, True, AP_RND, AP_WRAP),
    (8, 0, True, AP_RND, AP_SAT),
])
def test_assignment_quantizes_via_fixputils(W, I, signed, q, o):  # noqa: E741
    F = FixedField.specialize(W, I, signed=signed, q_mode=q, o_mode=o)
    lsb = 2.0 ** (-(W - I))
    for v in [0.0, 1.5 * lsb, -1.5 * lsb, 0.5 * lsb, 3.3 * lsb, 1e3, -1e3]:
        f = F()
        f.val = v
        stored = fixputils.quantize(v, W, I, signed, q, o)
        assert f.val == fixputils.to_float(stored, W, I)
        # .val is exactly representable (idempotent under re-quantization).
        assert fixputils.quantize(f.val, W, I, signed, q, o) == stored


@pytest.mark.parametrize("W,I,signed,q,o", [
    (8, 4, True, AP_TRN, AP_WRAP),
    (8, 4, False, AP_TRN, AP_WRAP),
    (16, 8, True, AP_RND, AP_SAT),
    (12, 6, True, AP_RND, AP_WRAP),
])
def test_serialize_deserialize_round_trip(W, I, signed, q, o):  # noqa: E741
    F = FixedField.specialize(W, I, signed=signed, q_mode=q, o_mode=o)
    lsb = 2.0 ** (-(W - I))
    for v in [0.0, 1.5 * lsb, -2.5 * lsb, 7.0 * lsb, 100.0, -100.0]:
        f = F()
        f.val = v
        packed = f.serialize(word_bw=32)
        restored = F().deserialize(packed, word_bw=32)
        assert restored.val == f.val
        # the packed low-W bits are exactly the ap_fixed .range() pattern.
        stored = fixputils.quantize(v, W, I, signed, q, o)
        assert (int(np.asarray(packed).ravel()[0]) & ((1 << W) - 1)) == fixputils.to_bits(stored, W)


def test_fixedfield_in_struct_round_trips():
    Int16 = IntField.specialize(16, signed=True)
    Q8_4 = FixedField.specialize(8, 4, q_mode=AP_RND, o_mode=AP_SAT)

    class Sample(DataList):
        elements = {"gain": {"schema": Q8_4}, "tag": {"schema": Int16}}

    s = Sample(gain=1.53, tag=-7)      # AP_RND: 1.53*16=24.48 -> 24 -> 1.5
    restored = Sample().deserialize(s.serialize(word_bw=32), word_bw=32)
    assert float(restored.gain) == float(s.gain) == 1.5
    assert int(restored.tag) == -7


def test_import_location():
    # decision 2: FixedField is imported from pysilicon.hw.fixpoint (no dataschema
    # re-export — keeps the import one-way / cycle-free).
    import pysilicon.hw.dataschema as ds
    assert not hasattr(ds, "FixedField")
