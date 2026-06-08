"""Phase-1 VMAC command tests — ``VmacCmd`` encode/decode round-trips.

``VmacCmd`` is a plain ``DataList`` (with nested ``Region`` / ``Scalar`` sub-lists and an
``EnumField`` mode), so it must serialize and deserialize back to an identical value across
word widths — the wire-format contract for the (Phase-3) HLS kernel.
"""
import pytest

from examples.vmac.vmac_cmd import Region, Scalar, VmacCmd, VmacMode


def _full_cmd() -> VmacCmd:
    cmd = VmacCmd()
    cmd.n_rows, cmd.n_cols = 6, 4
    cmd.a = {"addr": 0, "row_stride": 4, "col_stride": 1}
    cmd.b = {"addr": 24, "row_stride": 4, "col_stride": 1}
    cmd.c = {"addr": 48, "row_stride": 1, "col_stride": 6}        # transposed access
    cmd.d = {"addr": 72, "row_stride": 4, "col_stride": 1}
    cmd.alpha = {"direct": 1, "re": -16, "im": 8, "addr": 0, "stride": 0}
    cmd.beta = {"direct": 0, "re": 0, "im": 0, "addr": 96, "stride": 1}
    cmd.b_one, cmd.c_zero, cmd.b_conj, cmd.reduce_rows = 0, 1, 1, 1
    cmd.mode = VmacMode.COMPLEX
    cmd.in_bw, cmd.int_bits, cmd.out_bw = 16, 8, 12
    cmd.shift, cmd.acc_bw = 13, 48
    cmd.q_rnd, cmd.o_sat = 1, 1
    return cmd


@pytest.mark.parametrize("word_bw", [16, 32, 64])
def test_vmac_cmd_roundtrip(word_bw):
    cmd = _full_cmd()
    restored = VmacCmd().deserialize(cmd.serialize(word_bw), word_bw)
    assert restored.val == cmd.val


def test_nested_region_scalar_roundtrip():
    reg = Region(addr=12, row_stride=-3, col_stride=2)           # negative stride survives
    r2 = Region().deserialize(reg.serialize(32), 32)
    assert r2.val == reg.val == {"addr": 12, "row_stride": -3, "col_stride": 2}

    sc = Scalar(direct=1, re=-100, im=77, addr=5, stride=-1)
    s2 = Scalar().deserialize(sc.serialize(32), 32)
    assert s2.val == sc.val


def test_default_cmd_roundtrips():
    cmd = VmacCmd()                                              # all defaults / zeros
    restored = VmacCmd().deserialize(cmd.serialize(32), 32)
    assert restored.val == cmd.val


def test_mode_enum_field_roundtrips_both_values():
    for mode in (VmacMode.REAL, VmacMode.COMPLEX):
        cmd = VmacCmd()
        cmd.mode = mode
        restored = VmacCmd().deserialize(cmd.serialize(32), 32)
        assert int(restored.mode) == int(mode)


def test_signed_fields_preserve_negatives():
    cmd = _full_cmd()
    cmd.a = {"addr": 10, "row_stride": -4, "col_stride": -1}
    cmd.alpha = {"direct": 1, "re": -32768, "im": -1, "addr": 0, "stride": 0}
    restored = VmacCmd().deserialize(cmd.serialize(32), 32)
    assert restored.a.row_stride == -4 and restored.a.col_stride == -1
    assert restored.alpha.re == -32768 and restored.alpha.im == -1
    assert restored.val == cmd.val
