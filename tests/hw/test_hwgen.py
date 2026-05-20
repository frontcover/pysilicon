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
