"""Walk a resolved ``HwStmt`` tree and emit C++ source as a string."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pysilicon.hw.hwstmt import (
    CaseStmt,
    ContinueStmt,
    FieldRef,
    HwStmt,
    HwVar,
    Ref,
    ReturnStmt,
    SeqStmt,
    WhileStmt,
)

if TYPE_CHECKING:
    from pysilicon.hw.hw_component import HwComponent


@dataclass
class CodegenCtx:
    comp: HwComponent
    params: dict[str, str] = field(default_factory=dict)
    endpoint_names: dict[int, str] = field(default_factory=dict)
    indent: int = 1

    def pad(self) -> str:
        return "    " * self.indent

    def child(self) -> CodegenCtx:
        return CodegenCtx(
            comp=self.comp,
            params=self.params,
            endpoint_names=self.endpoint_names,
            indent=self.indent + 1,
        )


def to_cpp(stmt: HwStmt, ctx: CodegenCtx) -> str:
    """Emit C++ source for a statement (and its children). Returns a string."""
    if isinstance(stmt, WhileStmt):
        return _emit_while(stmt, ctx)
    if isinstance(stmt, SeqStmt):
        return _emit_seq(stmt, ctx)
    if isinstance(stmt, ReturnStmt):
        return _emit_return(stmt, ctx)
    if isinstance(stmt, ContinueStmt):
        return f"{ctx.pad()}continue;"
    if isinstance(stmt, CaseStmt):
        return _emit_case(stmt, ctx)
    raise NotImplementedError(
        f"Codegen for {type(stmt).__name__} not implemented yet"
    )


def _emit_while(stmt: WhileStmt, ctx: CodegenCtx) -> str:
    body = to_cpp(stmt.body, ctx.child())
    return f"{ctx.pad()}while (true) {{\n{body}\n{ctx.pad()}}}"


def _emit_seq(stmt: SeqStmt, ctx: CodegenCtx) -> str:
    return "\n".join(to_cpp(child, ctx) for child in stmt.stmts)


def _emit_return(stmt: ReturnStmt, ctx: CodegenCtx) -> str:
    if stmt.value is None:
        return f"{ctx.pad()}return;"
    return f"{ctx.pad()}return {_emit_expr(stmt.value, ctx)};"


def _emit_case(stmt: CaseStmt, ctx: CodegenCtx) -> str:
    lhs = stmt.var.name if stmt.field is None else f"{stmt.var.name}.{stmt.field}"
    rhs = _emit_literal(stmt.value)
    cond = f"{lhs} {stmt.op} {rhs}"
    lines = [f"{ctx.pad()}if ({cond}) {{"]
    lines.append(to_cpp(stmt.if_true, ctx.child()))
    lines.append(f"{ctx.pad()}}}")
    if stmt.if_false is not None:
        lines[-1] = f"{ctx.pad()}}} else {{"
        lines.append(to_cpp(stmt.if_false, ctx.child()))
        lines.append(f"{ctx.pad()}}}")
    return "\n".join(lines)


def _emit_expr(expr, ctx: CodegenCtx) -> str:
    if isinstance(expr, HwVar):
        return expr.name
    if isinstance(expr, Ref):
        return expr.var.name
    if isinstance(expr, FieldRef):
        return f"{expr.var.name}.{expr.field}"
    return _emit_literal(expr)


def _emit_literal(value) -> str:
    """Emit a Python value as a C++ literal."""
    from enum import IntEnum
    if isinstance(value, IntEnum):
        return f"{type(value).__name__}::{value.name}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f'"{value}"'
    return repr(value)
