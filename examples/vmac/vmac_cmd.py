"""``VmacCmd`` — the VMAC fused-instruction DataSchema (runtime tier).

A configurable vector MAC engine (VMAC) executes the **complex** fused op

    D = alpha · A · op(B) + beta · C   [, reduced over rows]

over a row-major region of shared memory.  VMAC is **complex-only** (every element is an
interleaved ``re`` / ``im`` pair of ``data_bw``-bit stored ints); there is no real mode.

Parameters split into two tiers (see :class:`~examples.vmac.vmac.VmacAccel`): everything
that **sizes or types the datapath** is **structural** and lives on the accelerator as
``HwParam`` (``mem_dwidth`` / ``mem_awidth`` / ``data_bw`` / ``int_bits`` / ``acc_bw`` /
``out_bw`` / ``q_rnd`` / ``o_sat``), so ``A_T`` / ``ACC_T`` / ``OUT_T`` are compile-time —
no dynamic types.  The **runtime** instruction lives here in ``VmacCmd`` and carries only
the **op + geometry**: the matrix shape (``n_rows`` / ``n_cols``), the row-major operand
regions (``a`` / ``b`` / ``c`` / ``d`` — addr + row_stride pitch), the op flags
(``b_one`` / ``c_zero`` / ``b_conj`` / ``reduce_rows``), and the ``alpha`` / ``beta``
scalars (direct immediate or indirect pointer + stride, scalar or per-column).  **No format
fields** — the requantize shift is *derived* from the flags + the structural format (the
accumulator fractional depth is ``2·F_in`` or ``3·F_in`` per ``b_one`` / ``c_zero``), a
variable shift on a fixed-width accumulator, not a dynamic type.

The width-bearing fields are set by the accelerator that produces/consumes the command
(``addr`` is ``mem_awidth`` bits; the immediate ``re`` / ``im`` are ``data_bw`` bits).
These schemas are **Level-2 declarative** (:class:`~waveflow.hw.dataschema.ParamSchema`):
each declares :class:`~waveflow.hw.param.Param` attributes and a dict-literal ``elements``
that references them directly — the core ``IntField.specialize`` / ``Region.specialize``
calls defer on the symbolic params, and ``specialize(**vals)`` resolves + caches (with the
``Region`` / ``Scalar`` cascade sharing params).  ``VmacCmd`` is still a plain ``DataList``,
so it serializes / deserializes and code-generates like any schema.
"""
from __future__ import annotations

from waveflow.hw import BooleanField, IntField, Param, ParamSchema

# --- field aliases (names match the auto-generated IntField subclass __name__) ----
UInt16 = IntField.specialize(16, signed=False)


class Region(ParamSchema):
    """A row-major operand region: ``M[i, j] = mem[addr + i·row_stride + j]``.

    Columns are the **contiguous, unit-stride packed inner dimension** — the wide bus reads
    ``pf = MEM_BW / element_bits`` contiguous elements per cycle, so an arbitrary column
    stride would only defeat it (scattered reads → one element/beat).  ``row_stride`` is the
    outer **pitch** (in elements) between successive rows, letting a region be a sub-matrix of
    a larger buffer."""

    mem_awidth = Param(32)
    elements = {
        "addr": {"schema": IntField.specialize(mem_awidth, signed=False),
                 "description": "base offset into shared memory"},
        "row_stride": {"schema": IntField.specialize(mem_awidth, signed=True),
                       "description": "outer pitch (in elements) between successive rows"},
    }


class Scalar(ParamSchema):
    """An ``alpha`` / ``beta`` operand: direct immediate (``re`` / ``im`` stored ints) or
    indirect (per-column pointer ``addr`` + ``stride``; ``stride 0`` broadcasts)."""

    mem_awidth = Param(32)
    data_bw = Param(32)
    elements = {
        "direct": {"schema": BooleanField,
                   "description": "True = immediate re/im; False = indirect addr/stride"},
        "re": IntField.specialize(data_bw, signed=True),    # immediate stored int (real part)
        "im": IntField.specialize(data_bw, signed=True),    # immediate stored int (imag part)
        "addr": IntField.specialize(mem_awidth, signed=False),
        "stride": IntField.specialize(mem_awidth, signed=True),
    }


class VmacCmd(ParamSchema):
    """The VMAC fused instruction — the runtime tier (see module docstring).

    Region/scalar field widths track the accelerator's ``mem_awidth`` (addresses) and
    ``data_bw`` (immediates); the cascade runs through ``Region`` / ``Scalar`` specialize
    (both share ``mem_awidth`` with ``VmacCmd`` and resolve to the same cached classes).
    """

    mem_awidth = Param(32)
    data_bw = Param(32)
    elements = {
        # global matrix shape (operands share it; dst is (1, n_cols) when reduced)
        "n_rows": UInt16,
        "n_cols": UInt16,
        # row-major operand / destination regions (cascade: share mem_awidth)
        "a": {"schema": Region.specialize(mem_awidth=mem_awidth),
              "description": "operand A region (left multiplicand of A·op(B))"},
        "b": {"schema": Region.specialize(mem_awidth=mem_awidth),
              "description": "operand B region (op(B): identity, or conj(B) in complex mode)"},
        "c": {"schema": Region.specialize(mem_awidth=mem_awidth),
              "description": "addend C region (the β·C term; unused when c_zero)"},
        "d": {"schema": Region.specialize(mem_awidth=mem_awidth),
              "description": "destination D region (reduced to a single row when reduce_rows)"},
        # scaling scalars (cascade: share mem_awidth and data_bw)
        "alpha": Scalar.specialize(mem_awidth=mem_awidth, data_bw=data_bw),
        "beta": Scalar.specialize(mem_awidth=mem_awidth, data_bw=data_bw),
        # op flags — the only runtime *control* (loop-invariant if/mux, no II hit).
        # No format fields: int_bits / out_bw / q_rnd / o_sat are structural (on VmacAccel),
        # and the requantize shift is derived from these flags + that structural format.
        "b_one": {"schema": BooleanField, "description": "op(B) = 1 (skip the A·op(B) multiply)"},
        "c_zero": {"schema": BooleanField, "description": "drop the beta·C term"},
        "b_conj": {"schema": BooleanField, "description": "op(B) = conj(B) (negate B's imag part)"},
        "reduce_rows": {"schema": BooleanField, "description": "sum the rows (per-column reduction)"},
    }
