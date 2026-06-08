"""``VmacCmd`` — the VMAC fused-instruction DataSchema.

A configurable vector MAC engine (VMAC) executes the fused op

    D = alpha · A · op(B) + beta · C   [, reduced over rows]

over a **strided** region of shared memory.  ``VmacCmd`` is the runtime instruction: the
operand regions (addr + strides; the matrix shape is global), the ``alpha`` / ``beta``
scalars (direct immediate or indirect pointer+stride, scalar or per-column), the op flags
(``b_one`` / ``c_zero`` / ``b_conj``), the reduction axis, the ``real`` | ``complex`` mode,
and the numeric format/parameters (``IN_BW`` / ``int_bits`` / ``OUT_BW`` / ``SHIFT`` /
``ACC_BW`` + round/saturate).  It is a plain :class:`DataList`, so it serializes /
deserializes (encode/decode round-trips) like any schema.

The Python golden that *executes* a ``VmacCmd`` lives in :mod:`examples.vmac.golden`.
"""
from __future__ import annotations

from enum import IntEnum

from waveflow.hw.dataschema import DataList, EnumField, IntField

# --- field aliases ------------------------------------------------------------
U32 = IntField.specialize(32, signed=False)
I32 = IntField.specialize(32, signed=True)
U16 = IntField.specialize(16, signed=False)
U8 = IntField.specialize(8, signed=False)
U1 = IntField.specialize(1, signed=False)


class VmacMode(IntEnum):
    """Datapath mode — sets the element width (``IN_BW`` real / ``2·IN_BW`` complex)."""
    REAL = 0
    COMPLEX = 1


ModeField = EnumField.specialize(VmacMode)


class Region(DataList):
    """A strided operand region: ``M[i, j] = mem[addr + i·row_stride + j·col_stride]``."""
    elements = {
        "addr": {"schema": U32, "description": "base offset into shared memory"},
        "row_stride": I32,
        "col_stride": I32,
    }


class Scalar(DataList):
    """An ``alpha`` / ``beta`` operand: direct immediate (``re``/``im`` stored ints) or
    indirect (per-column pointer ``addr`` + ``stride``; ``row_stride 0`` broadcasts)."""
    elements = {
        "direct": {"schema": U1, "description": "1 = immediate re/im; 0 = indirect addr/stride"},
        "re": I32,                    # immediate stored integer (real part)
        "im": I32,                    # immediate stored integer (imag part; complex mode)
        "addr": U32,
        "stride": I32,
    }


class VmacCmd(DataList):
    """The VMAC fused instruction (see module docstring)."""
    elements = {
        # global matrix shape (operands share it; dst is (1, n_cols) when reduced)
        "n_rows": U16,
        "n_cols": U16,
        # strided operand / destination regions
        "a": Region,
        "b": Region,
        "c": Region,
        "d": Region,
        # scaling scalars
        "alpha": Scalar,
        "beta": Scalar,
        # op flags
        "b_one": {"schema": U1, "description": "op(B) = 1 (skip the A·B multiply)"},
        "c_zero": {"schema": U1, "description": "drop the beta·C term"},
        "b_conj": {"schema": U1, "description": "op(B) = conj(B) (complex; no-op for real)"},
        "reduce_rows": {"schema": U1, "description": "sum the rows (per-column reduction)"},
        # datapath mode + numeric format / parameters
        "mode": ModeField,
        "in_bw": U8,
        "int_bits": U8,               # I of the operand format (F = in_bw - int_bits)
        "out_bw": U8,
        "shift": U8,                  # output right-shift (the single lossy step)
        "acc_bw": U8,                 # accumulator width budget (golden asserts no overflow)
        "q_rnd": {"schema": U1, "description": "output rounding: 0 = AP_TRN, 1 = AP_RND"},
        "o_sat": {"schema": U1, "description": "output overflow: 0 = AP_WRAP, 1 = AP_SAT"},
    }
