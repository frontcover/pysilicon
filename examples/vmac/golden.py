"""The VMAC Python golden — the bit-exact reference for ``D = α·A·op(B) + β·C [, reduce]``.

``execute(cmd, mem)`` runs a :class:`~examples.vmac.vmac_cmd.VmacCmd` over a shared-memory
array, composing the merged ``FixedField`` / ``ComplexField`` operators (``mult`` / ``cmult``,
``add`` / ``cadd``, ``conj``) for the multiply-accumulate, the wide-accumulator column
reduction :func:`~waveflow.hw.complexfield.csum` for ``reduce_rows``, and the output
requantize (right-shift ``SHIFT`` + round + saturate) via the ``ap_fixed``-exact integer
requantizer.  Everything is **vectorized** — no per-element Python loop.

The datapath: **multiply (IN_BW × IN_BW) → wide accumulate (full precision, ≤ ACC_BW) →
right-shift SHIFT → round + saturate → write (OUT_BW)**.  The right-shift is the single
lossy step; it is exactly an ``ap_fixed`` assignment, so the golden is bit-exact with the
Vitis kernel (Phase 3).

``mem`` is the shared memory: a 1-D ``int64`` array of stored integers (``real`` mode) or a
1-D structured ``[('re','im')]`` array (``complex`` mode).  Operands are **strided** regions
``M[i, j] = mem[addr + i·row_stride + j·col_stride]``.  ``execute`` writes the requantized
result into ``mem`` at the ``d`` region (so commands compose) and returns the dst
``DataArray``.
"""
from __future__ import annotations

import numpy as np

from examples.vmac.vmac_cmd import VmacCmd, VmacMode
from waveflow.hw import fixpoint
from waveflow.hw.complexfield import ComplexField, cadd, cmult, conj, csum
from waveflow.hw.dataschema import DataArray
from waveflow.hw.fixpoint import FixedField
from waveflow.utils import complexutils as cx
from waveflow.utils import fixputils
from waveflow.utils.fixputils import Format, OMode, QMode


def _q(cmd: VmacCmd) -> QMode:
    return QMode.AP_RND if int(cmd.q_rnd) else QMode.AP_TRN


def _o(cmd: VmacCmd) -> OMode:
    return OMode.AP_SAT if int(cmd.o_sat) else OMode.AP_WRAP


def _fixed_cls(fmt: Format) -> type[FixedField]:
    return FixedField.specialize(fmt.W, fmt.int_bits, fmt.signed, fmt.q_mode, fmt.o_mode)


def _region_idx(reg, n_rows: int, n_cols: int) -> np.ndarray:
    """The strided index matrix for a region: ``addr + i·row_stride + j·col_stride``."""
    rows = np.arange(n_rows)[:, None] * int(reg.row_stride)
    cols = np.arange(n_cols)[None, :] * int(reg.col_stride)
    return int(reg.addr) + rows + cols


def _operand(M: np.ndarray, in_fmt: Format, complex_mode: bool) -> DataArray:
    """Wrap a strided matrix view (stored ints / structured) as a DataArray operand."""
    inner = _fixed_cls(in_fmt)
    elem = ComplexField.specialize(inner) if complex_mode else inner
    shape = M.shape if M.shape else (1,)
    return DataArray.specialize(elem, max_shape=tuple(shape))(M)


def _scalar(sc, n_cols: int, in_fmt: Format, mem: np.ndarray, complex_mode: bool) -> DataArray:
    """Build an alpha/beta operand: direct immediate (shape (1,)) or indirect per-column."""
    if int(sc.direct):
        if complex_mode:
            M = cx.make_complex([int(sc.re)], [int(sc.im)], in_fmt)
        else:
            M = np.array([int(sc.re)], dtype=np.int64)
    else:
        idx = int(sc.addr) + np.arange(n_cols) * int(sc.stride)
        M = mem[idx]
    return _operand(M, in_fmt, complex_mode)


def _mult(a: DataArray, b: DataArray, complex_mode: bool) -> DataArray:
    return cmult(a, b) if complex_mode else fixpoint.mult(a, b)


def _add(a: DataArray, b: DataArray, complex_mode: bool) -> DataArray:
    return cadd(a, b) if complex_mode else fixpoint.add(a, b)


def _acc_width(t: DataArray, complex_mode: bool) -> int:
    et = t.element_type
    return et.inner_type.get_bitwidth() if complex_mode else et.get_bitwidth()


def _requantize(t: DataArray, out_bw: int, shift: int, q: QMode, o: OMode,
                complex_mode: bool) -> DataArray:
    """Right-shift ``SHIFT`` + round + saturate to ``OUT_BW`` — the single lossy step,
    an ``ap_fixed`` assignment via the integer requantizer."""
    tf = t.element_type.inner_format() if complex_mode else t.element_type.get_format()
    out_frac = tf.frac_bits - shift
    if out_frac < 0:
        raise ValueError(
            f"SHIFT={shift} exceeds accumulator fractional bits {tf.frac_bits}; "
            "the output would need more integer bits than OUT_BW.")
    target = Format(out_bw, out_bw - out_frac, tf.signed, q, o)
    if complex_mode:
        re = fixputils.quantize(cx.re_of(t.val), tf, target)
        im = fixputils.quantize(cx.im_of(t.val), tf, target)
        elem = ComplexField.specialize(_fixed_cls(target))
        struct = cx.make_complex(re, im, target)
        return DataArray.specialize(elem, max_shape=struct.shape)(struct)
    return fixpoint.quantize(t, _fixed_cls(target))


def _writeback(mem: np.ndarray, reg, dst: DataArray, complex_mode: bool) -> None:
    val = np.asarray(dst.val)
    if val.ndim == 1:                                   # reduced -> single row of columns
        idx = int(reg.addr) + np.arange(val.shape[0]) * int(reg.col_stride)
    else:
        idx = _region_idx(reg, val.shape[0], val.shape[1])
    mem[idx] = val


def execute(cmd: VmacCmd, mem: np.ndarray) -> DataArray:
    """Execute a ``VmacCmd`` over ``mem``; write the dst region and return the dst array."""
    mode = VmacMode(int(cmd.mode))
    complex_mode = mode == VmacMode.COMPLEX
    in_fmt = Format(int(cmd.in_bw), int(cmd.int_bits), True)
    n, m = int(cmd.n_rows), int(cmd.n_cols)
    mem = np.asarray(mem)

    def region(reg) -> DataArray:
        return _operand(mem[_region_idx(reg, n, m)], in_fmt, complex_mode)

    # op(B) and A·op(B)
    a = region(cmd.a)
    if int(cmd.b_one):
        ab = a
    else:
        b = region(cmd.b)
        if int(cmd.b_conj) and complex_mode:            # conj is a no-op for real data
            b = conj(b)
        ab = _mult(a, b, complex_mode)

    # alpha · A·op(B)
    alpha = _scalar(cmd.alpha, m, in_fmt, mem, complex_mode)
    t = _mult(alpha, ab, complex_mode)

    # + beta · C
    if not int(cmd.c_zero):
        beta = _scalar(cmd.beta, m, in_fmt, mem, complex_mode)
        t = _add(t, _mult(beta, region(cmd.c), complex_mode), complex_mode)

    # optional row reduction (wide accumulator)
    if int(cmd.reduce_rows):
        t = csum(t, axis=0)

    # accumulator-width budget check (the provisioned ACC_BW must hold full precision)
    acc_w = _acc_width(t, complex_mode)
    if acc_w > int(cmd.acc_bw):
        raise ValueError(
            f"accumulator width {acc_w} exceeds ACC_BW={int(cmd.acc_bw)}; widen ACC_BW.")

    dst = _requantize(t, int(cmd.out_bw), int(cmd.shift), _q(cmd), _o(cmd), complex_mode)
    _writeback(mem, cmd.d, dst, complex_mode)
    return dst
