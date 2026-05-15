"""HwStmt — synthesizable statement IR.

Users never construct these nodes manually.  They are produced by
``HwStmtExtractor`` when ``HwComponent.build()`` parses ``run_proc``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pysilicon.hw.interface import InterfaceEndpoint
    from pysilicon.hw.hw_component import HwComponent


# ---------------------------------------------------------------------------
# Expression nodes
# ---------------------------------------------------------------------------

class HwExpr:
    """Base class for synthesizable expression nodes."""


@dataclass
class Ref(HwExpr):
    """A reference to a bound ``HwVar``."""
    var: HwVar


@dataclass
class FieldRef(HwExpr):
    """Field access on a bound ``HwVar`` (e.g. ``cmd_hdr.nsamp``)."""
    var: HwVar
    field: str


# ---------------------------------------------------------------------------
# Variable binding
# ---------------------------------------------------------------------------

@dataclass
class HwVar:
    """A symbolic variable produced by a synthesizable statement.

    Created by ``HwStmtExtractor`` for each binding in ``run_proc``.
    """
    name: str
    typ: object  # type[DataSchema] | SchemaArray | None (unresolved)
    producer: HwStmt | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Statement nodes
# ---------------------------------------------------------------------------

class HwStmt:
    """Base class for synthesizable statement IR nodes."""


@dataclass
class SeqStmt(HwStmt):
    """Sequential list of statements."""
    stmts: list[HwStmt]


@dataclass
class WhileStmt(HwStmt):
    """``while True:`` loop — maps to ``ap_ctrl_none`` in HLS."""
    body: SeqStmt


@dataclass
class CaseStmt(HwStmt):
    """Restricted ``if var.field == value:`` — maps to switch/if-else in C++."""
    var: HwVar
    field: str
    value: object  # enum value or literal
    if_true: SeqStmt
    if_false: SeqStmt | None = None


@dataclass
class ContinueStmt(HwStmt):
    """``continue`` inside ``while True``."""


@dataclass
class SynthCallStmt(HwStmt):
    """A call to a ``@synthesizable`` method with a ``synth_fn``.

    Base class for endpoint-owned statement types
    (``StreamGetStmt``, ``MMArrayReadStmt``, etc.).
    """
    method: object              # bound callable with _is_synthesizable=True
    inputs: list               # HwVar | InterfaceEndpoint | ast node
    outputs: list[HwVar]


@dataclass
class HookStmt(HwStmt):
    """A call to a ``@synthesizable`` user compute method (``_synth_fn=None``).

    At codegen time emits a call to a user-written function in ``_impl.cpp``.
    """
    method: object              # bound callable with _is_synthesizable=True
    inputs: list               # HwVar | ast node
    outputs: list[HwVar]
