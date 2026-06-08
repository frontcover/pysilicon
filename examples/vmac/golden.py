"""``VmacAccel`` — the VMAC accelerator: structural params + the bit-exact Python golden.

A VMAC instance is parameterized by its **structural** widths — fixed at synthesis, the
HLS template params: ``mem_dwidth`` (MEM_BW), ``mem_awidth``, ``data_bw`` (IN_BW),
``acc_bw``, ``out_bw``.  ``VmacAccel.specialize(...)`` sets them and **cascades** the
specialization into the command schema (``Cmd = VmacCmd.specialize(mem_awidth, data_bw)``),
so a command's field widths track the silicon that consumes it::

    Accel = VmacAccel.specialize(mem_dwidth=512, mem_awidth=32, data_bw=16, acc_bw=48, out_bw=16)
    cmd   = Accel.Cmd(...)            # a VmacCmd specialized to this accelerator
    dst   = Accel.execute(cmd, mem)   # the bit-exact golden

``execute`` runs the fused op ``D = α·A·op(B) + β·C [, reduce]`` over a shared-memory
array, composing the merged ``FixedField`` / ``ComplexField`` operators (``mult`` /
``cmult``, ``add`` / ``cadd``, ``conj``), the wide-accumulator column reduction
:func:`~waveflow.hw.complexfield.csum` for ``reduce_rows``, and the output requantize
(right-shift ``SHIFT`` + round + saturate) via the ``ap_fixed``-exact integer requantizer.
Vectorized — no per-element Python loop.

The datapath: **multiply (data_bw × data_bw) → wide accumulate (full precision, ≤ acc_bw)
→ right-shift shift → round + saturate → write (out_bw)**.  The right-shift is the single
lossy step (an ``ap_fixed`` assignment), so the golden is bit-exact with the Vitis kernel
(Phase 3).  ``mem`` is the shared memory: a 1-D ``int64`` array of stored integers (``real``
mode) or a 1-D structured ``[('re','im')]`` array (``complex`` mode); operands are strided
regions ``M[i, j] = mem[addr + i·row_stride + j·col_stride]``.  ``execute`` writes the
requantized result into ``mem`` at the ``d`` region (so commands compose) and returns the
dst ``DataArray``.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from examples.vmac.vmac_cmd import VmacCmd, VmacMode
from waveflow.hw import fixpoint
from waveflow.hw.complexfield import ComplexField, cadd, cmult, conj, csum
from waveflow.hw.dataschema import DataArray
from waveflow.hw.fixpoint import FixedField
from waveflow.utils import complexutils as cx
from waveflow.utils import fixputils
from waveflow.utils.fixputils import Format, OMode, QMode


class VmacAccel:
    """A VMAC accelerator instance: structural widths + the Python golden ``execute``."""

    # structural params (synthesis-time; the HLS template params)
    mem_dwidth: int = 512
    mem_awidth: int = 32
    data_bw: int = 32                       # IN_BW — operand element width
    acc_bw: int = 64                        # accumulator width budget
    out_bw: int = 32                        # writeback element width
    Cmd: type[VmacCmd] = VmacCmd            # the command schema (specialized below)
    _specializations: dict[tuple[Any, ...], type["VmacAccel"]] = {}

    @classmethod
    def specialize(
        cls,
        *,
        mem_dwidth: int,
        mem_awidth: int,
        data_bw: int,
        acc_bw: int,
        out_bw: int,
    ) -> type["VmacAccel"]:
        """Return a cached accelerator with these structural widths; cascades into ``Cmd``.

        Same params → same class object (stable schema identity for codegen)."""
        key = (cls, int(mem_dwidth), int(mem_awidth), int(data_bw), int(acc_bw), int(out_bw))
        cached = cls._specializations.get(key)
        if cached is not None:
            return cached
        sub = type(f"VmacAccel_d{mem_dwidth}_a{mem_awidth}_w{data_bw}", (cls,), {
            "mem_dwidth": int(mem_dwidth),
            "mem_awidth": int(mem_awidth),
            "data_bw": int(data_bw),
            "acc_bw": int(acc_bw),
            "out_bw": int(out_bw),
            "Cmd": VmacCmd.specialize(mem_awidth=int(mem_awidth), data_bw=int(data_bw)),
            "__module__": cls.__module__,
        })
        cls._specializations[key] = sub
        return sub

    # --- private datapath helpers --------------------------------------------
    @staticmethod
    def _fixed_cls(fmt: Format) -> type[FixedField]:
        return FixedField.specialize(fmt.W, fmt.int_bits, fmt.signed, fmt.q_mode, fmt.o_mode)

    @staticmethod
    def _region_idx(reg, n_rows: int, n_cols: int) -> np.ndarray:
        """The strided index matrix: ``addr + i·row_stride + j·col_stride``."""
        rows = np.arange(n_rows)[:, None] * int(reg.row_stride)
        cols = np.arange(n_cols)[None, :] * int(reg.col_stride)
        return int(reg.addr) + rows + cols

    @classmethod
    def _operand(cls, M: np.ndarray, in_fmt: Format, complex_mode: bool) -> DataArray:
        """Wrap a strided matrix view (stored ints / structured) as a DataArray operand."""
        inner = cls._fixed_cls(in_fmt)
        elem = ComplexField.specialize(inner) if complex_mode else inner
        shape = M.shape if M.shape else (1,)
        return DataArray.specialize(elem, max_shape=tuple(shape))(M)

    @classmethod
    def _scalar(cls, sc, n_cols: int, in_fmt: Format, mem: np.ndarray,
                complex_mode: bool) -> DataArray:
        """Build an alpha/beta operand: direct immediate (shape (1,)) or indirect per-column."""
        if bool(sc.direct):
            if complex_mode:
                M = cx.make_complex([int(sc.re)], [int(sc.im)], in_fmt)
            else:
                M = np.array([int(sc.re)], dtype=np.int64)
        else:
            idx = int(sc.addr) + np.arange(n_cols) * int(sc.stride)
            M = mem[idx]
        return cls._operand(M, in_fmt, complex_mode)

    @staticmethod
    def _mult(a: DataArray, b: DataArray, complex_mode: bool) -> DataArray:
        return cmult(a, b) if complex_mode else fixpoint.mult(a, b)

    @staticmethod
    def _add(a: DataArray, b: DataArray, complex_mode: bool) -> DataArray:
        return cadd(a, b) if complex_mode else fixpoint.add(a, b)

    @staticmethod
    def _acc_width(t: DataArray, complex_mode: bool) -> int:
        et = t.element_type
        return et.inner_type.get_bitwidth() if complex_mode else et.get_bitwidth()

    @classmethod
    def _requantize(cls, t: DataArray, out_bw: int, shift: int, q: QMode, o: OMode,
                    complex_mode: bool) -> DataArray:
        """Right-shift ``shift`` + round + saturate to ``out_bw`` — the single lossy step."""
        tf = t.element_type.inner_format() if complex_mode else t.element_type.get_format()
        out_frac = tf.frac_bits - shift
        if out_frac < 0:
            raise ValueError(
                f"shift={shift} exceeds accumulator fractional bits {tf.frac_bits}; "
                "the output would need more integer bits than out_bw.")
        target = Format(out_bw, out_bw - out_frac, tf.signed, q, o)
        if complex_mode:
            re = fixputils.quantize(cx.re_of(t.val), tf, target)
            im = fixputils.quantize(cx.im_of(t.val), tf, target)
            elem = ComplexField.specialize(cls._fixed_cls(target))
            struct = cx.make_complex(re, im, target)
            return DataArray.specialize(elem, max_shape=struct.shape)(struct)
        return fixpoint.quantize(t, cls._fixed_cls(target))

    @classmethod
    def _writeback(cls, mem: np.ndarray, reg, dst: DataArray) -> None:
        val = np.asarray(dst.val)
        if val.ndim == 1:                                   # reduced -> single row of columns
            idx = int(reg.addr) + np.arange(val.shape[0]) * int(reg.col_stride)
        else:
            idx = cls._region_idx(reg, val.shape[0], val.shape[1])
        mem[idx] = val

    # --- the golden ----------------------------------------------------------
    @classmethod
    def execute(cls, cmd: VmacCmd, mem: np.ndarray) -> DataArray:
        """Execute a ``VmacCmd`` over ``mem``; write the dst region and return the dst array."""
        complex_mode = VmacMode(int(cmd.mode)) == VmacMode.COMPLEX
        in_fmt = Format(cls.data_bw, int(cmd.int_bits), True)
        n, m = int(cmd.n_rows), int(cmd.n_cols)
        mem = np.asarray(mem)

        def region(reg) -> DataArray:
            return cls._operand(mem[cls._region_idx(reg, n, m)], in_fmt, complex_mode)

        # op(B) and A·op(B)
        a = region(cmd.a)
        if bool(cmd.b_one):
            ab = a
        else:
            b = region(cmd.b)
            if bool(cmd.b_conj) and complex_mode:           # conj is a no-op for real data
                b = conj(b)
            ab = cls._mult(a, b, complex_mode)

        # alpha · A·op(B)
        alpha = cls._scalar(cmd.alpha, m, in_fmt, mem, complex_mode)
        t = cls._mult(alpha, ab, complex_mode)

        # + beta · C
        if not bool(cmd.c_zero):
            beta = cls._scalar(cmd.beta, m, in_fmt, mem, complex_mode)
            t = cls._add(t, cls._mult(beta, region(cmd.c), complex_mode), complex_mode)

        # optional row reduction (wide accumulator)
        if bool(cmd.reduce_rows):
            t = csum(t, axis=0)

        # accumulator-width budget check (the provisioned acc_bw must hold full precision)
        acc_w = cls._acc_width(t, complex_mode)
        if acc_w > cls.acc_bw:
            raise ValueError(
                f"accumulator width {acc_w} exceeds acc_bw={cls.acc_bw}; widen acc_bw.")

        dst = cls._requantize(t, cls.out_bw, int(cmd.shift), cmd.q_mode, cmd.o_mode, complex_mode)
        cls._writeback(mem, cmd.d, dst)
        return dst
