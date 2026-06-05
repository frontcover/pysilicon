"""Phase 2: multi-buffer, multi-type m_axi IR + lowering for the histogram.

The histogram kernel issues three array ops against one m_axi bundle at three
different MemAddr fields, with two element types (Float32 reads, Uint32 write)
and per-buffer compile-time bounds. This asserts the extractor produces the
three stmts with the right (elem_type, count, addr, max) and that each lowers to
the correct ``<elem>_array_utils::{read,write}_array`` call.
"""
from __future__ import annotations

from examples.shared_mem.hist import HistAccel
from pysilicon.build.hwcodegen import extract_kernel
from pysilicon.build.hwgen import (
    CodegenCtx,
    _emit_mm_array_read,
    _emit_mm_array_write,
)
from pysilicon.hw.hwstmt import MMArrayReadStmt, MMArrayWriteStmt
from pysilicon.simulation.simulation import Simulation


def _collect_mm_stmts(tree):
    out = []

    def walk(n):
        if isinstance(n, (MMArrayReadStmt, MMArrayWriteStmt)):
            out.append(n)
        if hasattr(n, "stmts"):
            for s in n.stmts:
                walk(s)
        for attr in ("if_true", "if_false", "body"):
            sub = getattr(n, attr, None)
            if sub is not None:
                walk(sub)

    walk(tree)
    return out


def _extract():
    sim = Simulation()
    comp = HistAccel(name="hist_accel", sim=sim)
    return comp, _collect_mm_stmts(extract_kernel(comp))


def test_three_array_stmts_with_distinct_addrs_types_bounds():
    """Extractor yields three array stmts: two Float32 reads + one Uint32 write,
    at three distinct addresses, each with its own compile-time max bound."""
    _comp, mm = _extract()
    assert len(mm) == 3

    data, edges, counts = mm
    assert isinstance(data, MMArrayReadStmt)
    assert isinstance(edges, MMArrayReadStmt)
    assert isinstance(counts, MMArrayWriteStmt)

    # Two element types over the one bundle.
    assert data.elem_type.cpp_class_name() == "float"
    assert edges.elem_type.cpp_class_name() == "float"
    assert counts.elem_type.cpp_class_name() == "ap_uint<32>"

    # Per-buffer compile-time bounds from the explicit max_count= args.
    assert data.max_expr.param_name == "max_ndata"
    assert edges.max_expr.param_name == "max_nbins"
    assert counts.max_expr.param_name == "max_nbins"


def test_data_read_lowers_to_float32_array_utils():
    comp, mm = _extract()
    ctx = CodegenCtx(comp=comp)
    cpp = _emit_mm_array_read(mm[0], ctx)
    assert "static float data[max_ndata];" in cpp
    assert ("float32_array_utils::read_array<32>("
            "m_mem + memmgr::byte_addr_to_word_index<32>(cmd.data_addr), "
            "data, cmd.ndata);") in cpp


def test_edges_read_lowers_with_binop_count():
    """The edge count is the BinOp ``nbins - 1`` — it must lower verbatim."""
    comp, mm = _extract()
    ctx = CodegenCtx(comp=comp)
    cpp = _emit_mm_array_read(mm[1], ctx)
    assert "static float edges[max_nbins];" in cpp
    assert "byte_addr_to_word_index<32>(cmd.bin_edges_addr)" in cpp
    assert "edges, cmd.nbins - 1);" in cpp


def test_counts_write_lowers_to_uint32_array_utils():
    comp, mm = _extract()
    ctx = CodegenCtx(comp=comp)
    cpp = _emit_mm_array_write(mm[2], ctx)
    assert ("uint32_array_utils::write_array<32>("
            "counts, m_mem + memmgr::byte_addr_to_word_index<32>(cmd.cnt_addr), "
            "cmd.nbins);") in cpp


def test_missing_max_count_fails_loudly():
    """A read with no max_count= has no resolvable buffer bound — fail loudly
    rather than emit an unsized array (no global max_n fallback)."""
    import pytest

    from pysilicon.build.hwcodegen import SynthesisError

    comp, mm = _extract()
    ctx = CodegenCtx(comp=comp)
    stmt = mm[0]
    stmt.kwargs = {}   # drop the max_count
    with pytest.raises(SynthesisError, match="no compile-time buffer bound"):
        _emit_mm_array_read(stmt, ctx)
