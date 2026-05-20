"""Tests for the C++ codegen pass (pysilicon/build/hwgen.py)."""
from __future__ import annotations

from enum import IntEnum

import pytest

from pysilicon.build.hwgen import CodegenCtx, to_cpp
from pysilicon.hw.hw_component import HwComponent
from pysilicon.hw.hwstmt import (
    CaseStmt,
    ContinueStmt,
    HwVar,
    ReturnStmt,
    SeqStmt,
    WhileStmt,
)
from pysilicon.simulation.simulation import Simulation


class DemoCmdType(IntEnum):
    DATA = 0
    END  = 1


class DemoError(IntEnum):
    OK         = 0
    BAD_INPUT  = 1


def _ctx() -> CodegenCtx:
    comp = HwComponent(name='c', sim=Simulation())
    return CodegenCtx(comp=comp)


# ---------------------------------------------------------------------------
# Phase 1: control-flow statements
# ---------------------------------------------------------------------------

def test_return_no_value():
    assert to_cpp(ReturnStmt(value=None), _ctx()) == "    return;"


def test_continue_stmt():
    assert to_cpp(ContinueStmt(), _ctx()) == "    continue;"


def test_while_with_return_body():
    stmt = WhileStmt(body=SeqStmt(stmts=[ReturnStmt(value=None)]))
    expected = (
        "    while (true) {\n"
        "        return;\n"
        "    }"
    )
    assert to_cpp(stmt, _ctx()) == expected


def test_case_stmt_bare_var():
    stmt = CaseStmt(
        var=HwVar(name='err', typ=None),
        field=None,
        value=DemoError.OK,
        op='!=',
        if_true=SeqStmt(stmts=[ReturnStmt(value=None)]),
    )
    expected = (
        "    if (err != DemoError::OK) {\n"
        "        return;\n"
        "    }"
    )
    assert to_cpp(stmt, _ctx()) == expected


def test_case_stmt_field_access():
    stmt = CaseStmt(
        var=HwVar(name='cmd', typ=None),
        field='cmd_type',
        value=DemoCmdType.END,
        op='==',
        if_true=SeqStmt(stmts=[ReturnStmt(value=None)]),
    )
    expected = (
        "    if (cmd.cmd_type == DemoCmdType::END) {\n"
        "        return;\n"
        "    }"
    )
    assert to_cpp(stmt, _ctx()) == expected


def test_seq_stmt_joins_with_newlines():
    stmt = SeqStmt(stmts=[ReturnStmt(value=None), ContinueStmt()])
    assert to_cpp(stmt, _ctx()) == "    return;\n    continue;"


def test_case_stmt_with_else():
    stmt = CaseStmt(
        var=HwVar(name='err', typ=None),
        field=None,
        value=DemoError.OK,
        op='==',
        if_true=SeqStmt(stmts=[ContinueStmt()]),
        if_false=SeqStmt(stmts=[ReturnStmt(value=None)]),
    )
    expected = (
        "    if (err == DemoError::OK) {\n"
        "        continue;\n"
        "    } else {\n"
        "        return;\n"
        "    }"
    )
    assert to_cpp(stmt, _ctx()) == expected


def test_unhandled_stmt_raises_not_implemented():
    class _Bogus:
        pass

    with pytest.raises(NotImplementedError):
        to_cpp(_Bogus(), _ctx())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Phase 2: Stream statements
# ---------------------------------------------------------------------------

class _FakeSchema:
    @classmethod
    def cpp_class_name(cls) -> str:
        return "DemoCmdHdr"


class _FakeEndpoint:
    """Stand-in object used as the bound `self` of a stream method."""


class _FakeBoundMethod:
    """Carries ``__self__`` so ``_endpoint_name`` can locate the endpoint."""
    def __init__(self, endpoint):
        self.__self__ = endpoint


def _comp_with_endpoints(**endpoints):
    """Create a HwComponent and stash endpoints as attributes for vars() lookup."""
    comp = HwComponent(name='c', sim=Simulation())
    for name, ep in endpoints.items():
        setattr(comp, name, ep)
    return comp


def test_stream_get_emits_decl_and_read():
    from pysilicon.hw.interface import StreamGetStmt
    s_in = _FakeEndpoint()
    comp = _comp_with_endpoints(s_in=s_in)
    ctx = CodegenCtx(comp=comp)
    stmt = StreamGetStmt(
        method=_FakeBoundMethod(s_in),
        inputs=[_FakeSchema],
        outputs=[HwVar(name='cmd', typ=_FakeSchema)],
    )
    expected = (
        "    DemoCmdHdr cmd;\n"
        "    cmd.read_axi4_stream<WORD_BW>(s_in);"
    )
    assert to_cpp(stmt, ctx) == expected


def test_stream_write_emits_call():
    from pysilicon.hw.interface import StreamWriteStmt
    m_out = _FakeEndpoint()
    comp = _comp_with_endpoints(m_out=m_out)
    ctx = CodegenCtx(comp=comp)
    stmt = StreamWriteStmt(
        method=_FakeBoundMethod(m_out),
        inputs=[HwVar(name='resp', typ=None)],
        outputs=[],
    )
    assert to_cpp(stmt, ctx) == "    resp.write_axi4_stream<WORD_BW>(m_out, true);"


def test_stream_drain_emits_flush():
    from pysilicon.hw.interface import StreamDrainStmt
    s_in = _FakeEndpoint()
    comp = _comp_with_endpoints(s_in=s_in)
    ctx = CodegenCtx(comp=comp)
    stmt = StreamDrainStmt(
        method=_FakeBoundMethod(s_in),
        inputs=[],
        outputs=[],
    )
    assert to_cpp(stmt, ctx) == (
        "    streamutils::flush_axi4_stream_to_tlast<WORD_BW>(s_in);"
    )


def test_endpoint_name_not_found_raises():
    from pysilicon.hw.interface import StreamGetStmt
    rogue = _FakeEndpoint()
    comp = _comp_with_endpoints()  # no endpoint set
    ctx = CodegenCtx(comp=comp)
    stmt = StreamGetStmt(
        method=_FakeBoundMethod(rogue),
        inputs=[_FakeSchema],
        outputs=[HwVar(name='x', typ=_FakeSchema)],
    )
    with pytest.raises(RuntimeError, match="not found"):
        to_cpp(stmt, ctx)
