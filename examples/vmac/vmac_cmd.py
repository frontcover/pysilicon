"""``VmacCmd`` — the VMAC fused-instruction DataSchema (runtime tier).

A configurable vector MAC engine (VMAC) executes the fused op

    D = alpha · A · op(B) + beta · C   [, reduced over rows]

over a **strided** region of shared memory.  Parameters split into two tiers (see
:class:`~examples.vmac.golden.VmacAccel`): the **structural** widths that size silicon
(``mem_dwidth`` / ``mem_awidth`` / ``data_bw`` / ``acc_bw`` / ``out_bw``) live on the
accelerator, and the **runtime** instruction lives here in ``VmacCmd``: the operand
regions (addr + strides; the matrix shape is global), the ``alpha`` / ``beta`` scalars
(direct immediate or indirect pointer+stride, scalar or per-column), the op flags
(``b_one`` / ``c_zero`` / ``b_conj`` / ``reduce_rows``), the ``real`` | ``complex`` mode,
the fractional split ``int_bits``, the output ``shift``, and the round / saturate flags.

Because the command's field widths are set by the accelerator that produces/consumes it
(``addr`` is ``mem_awidth`` bits; the immediate ``re`` / ``im`` are ``data_bw`` bits), the
accelerator **specializes** the schema — the ``specialize`` parameterization pattern: params
on the class, values on the instance.  ``VmacCmd`` stays a plain :class:`DataList`, so it
serializes / deserializes and code-generates like any schema.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Any

from waveflow.hw.dataschema import BooleanField, DataList, EnumField, IntField
from waveflow.utils.fixputils import OMode, QMode

# --- field aliases (names match the auto-generated IntField subclass __name__) ----
UInt32 = IntField.specialize(32, signed=False)
Int32 = IntField.specialize(32, signed=True)
UInt16 = IntField.specialize(16, signed=False)
UInt8 = IntField.specialize(8, signed=False)

# default structural widths for an unspecialized schema (the accelerator overrides these)
_DEFAULT_AWIDTH = 32
_DEFAULT_DATA_BW = 32


class VmacMode(IntEnum):
    """Datapath mode — sets the element width (``IN_BW`` real / ``2·IN_BW`` complex)."""
    REAL = 0
    COMPLEX = 1


ModeField = EnumField.specialize(VmacMode)


# --- element builders (rebuilt from the structural widths) --------------------
def _region_elements(mem_awidth: int) -> dict[str, Any]:
    addr = IntField.specialize(mem_awidth, signed=False)
    offset = IntField.specialize(mem_awidth, signed=True)
    return {
        "addr": {"schema": addr, "description": "base offset into shared memory"},
        "row_stride": offset,
        "col_stride": offset,
    }


def _scalar_elements(mem_awidth: int, data_bw: int) -> dict[str, Any]:
    addr = IntField.specialize(mem_awidth, signed=False)
    offset = IntField.specialize(mem_awidth, signed=True)
    imm = IntField.specialize(data_bw, signed=True)
    return {
        "direct": {"schema": BooleanField, "description": "True = immediate re/im; False = indirect addr/stride"},
        "re": imm,                    # immediate stored integer (real part), data_bw bits
        "im": imm,                    # immediate stored integer (imag part; complex mode)
        "addr": addr,
        "stride": offset,
    }


class Region(DataList):
    """A strided operand region: ``M[i, j] = mem[addr + i·row_stride + j·col_stride]``."""
    elements = _region_elements(_DEFAULT_AWIDTH)
    _specializations: dict[tuple[Any, ...], type[Region]] = {}

    @classmethod
    def specialize(cls, mem_awidth: int) -> type[Region]:
        """Return a cached ``Region`` whose ``addr`` / strides are ``mem_awidth`` bits."""
        key = (cls, int(mem_awidth))
        cached = cls._specializations.get(key)
        if cached is not None:
            return cached
        sub = type(f"Region_a{mem_awidth}", (cls,), {
            "elements": _region_elements(mem_awidth),
            "__module__": cls.__module__,
            "__doc__": cls.__doc__,
        })
        cls._specializations[key] = sub
        return sub


class Scalar(DataList):
    """An ``alpha`` / ``beta`` operand: direct immediate (``re`` / ``im`` stored ints) or
    indirect (per-column pointer ``addr`` + ``stride``; ``stride 0`` broadcasts)."""
    elements = _scalar_elements(_DEFAULT_AWIDTH, _DEFAULT_DATA_BW)
    _specializations: dict[tuple[Any, ...], type[Scalar]] = {}

    @classmethod
    def specialize(cls, mem_awidth: int, data_bw: int) -> type[Scalar]:
        """Return a cached ``Scalar`` (``addr`` = ``mem_awidth`` bits; ``re`` / ``im`` =
        ``data_bw`` bits)."""
        key = (cls, int(mem_awidth), int(data_bw))
        cached = cls._specializations.get(key)
        if cached is not None:
            return cached
        sub = type(f"Scalar_a{mem_awidth}_w{data_bw}", (cls,), {
            "elements": _scalar_elements(mem_awidth, data_bw),
            "__module__": cls.__module__,
            "__doc__": cls.__doc__,
        })
        cls._specializations[key] = sub
        return sub


def _cmd_elements(mem_awidth: int, data_bw: int) -> dict[str, Any]:
    reg = Region.specialize(mem_awidth)
    scalar = Scalar.specialize(mem_awidth, data_bw)
    return {
        # global matrix shape (operands share it; dst is (1, n_cols) when reduced)
        "n_rows": UInt16,
        "n_cols": UInt16,
        # strided operand / destination regions
        "a": reg,
        "b": reg,
        "c": reg,
        "d": reg,
        # scaling scalars
        "alpha": scalar,
        "beta": scalar,
        # op flags
        "b_one": {"schema": BooleanField, "description": "op(B) = 1 (skip the A·B multiply)"},
        "c_zero": {"schema": BooleanField, "description": "drop the beta·C term"},
        "b_conj": {"schema": BooleanField, "description": "op(B) = conj(B) (complex; no-op for real)"},
        "reduce_rows": {"schema": BooleanField, "description": "sum the rows (per-column reduction)"},
        # datapath mode + runtime numeric format
        "mode": ModeField,
        "int_bits": UInt8,            # I of the operand format (F = data_bw - int_bits)
        "shift": UInt8,               # output right-shift (the single lossy step)
        "q_rnd": {"schema": BooleanField, "description": "output rounding: False = AP_TRN, True = AP_RND"},
        "o_sat": {"schema": BooleanField, "description": "output overflow: False = AP_WRAP, True = AP_SAT"},
    }


class VmacCmd(DataList):
    """The VMAC fused instruction — the runtime tier (see module docstring)."""
    elements = _cmd_elements(_DEFAULT_AWIDTH, _DEFAULT_DATA_BW)
    _specializations: dict[tuple[Any, ...], type[VmacCmd]] = {}

    @classmethod
    def specialize(cls, mem_awidth: int, data_bw: int) -> type[VmacCmd]:
        """Return a cached ``VmacCmd`` whose region/scalar field widths track the
        accelerator's ``mem_awidth`` (addresses) and ``data_bw`` (immediates)."""
        key = (cls, int(mem_awidth), int(data_bw))
        cached = cls._specializations.get(key)
        if cached is not None:
            return cached
        sub = type(f"VmacCmd_a{mem_awidth}_w{data_bw}", (cls,), {
            "elements": _cmd_elements(mem_awidth, data_bw),
            "__module__": cls.__module__,
            "__doc__": cls.__doc__,
        })
        cls._specializations[key] = sub
        return sub

    @property
    def q_mode(self) -> QMode:
        """The quantization mode this command selects (from its ``q_rnd`` flag)."""
        return QMode.AP_RND if bool(self.q_rnd) else QMode.AP_TRN

    @property
    def o_mode(self) -> OMode:
        """The overflow mode this command selects (from its ``o_sat`` flag)."""
        return OMode.AP_SAT if bool(self.o_sat) else OMode.AP_WRAP
