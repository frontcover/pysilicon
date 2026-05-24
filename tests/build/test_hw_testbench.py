"""Tests for the ``HwTestbench`` class and testbench-mode extractor.

Phase 14 of the HwComponent codegen project introduces a separate codegen
source for testbench C++.  Phase 1 (this file) covers the wiring: the new
``HwTestbench`` class, its ``main()`` placeholder, and the
``extract_kernel`` routing that dispatches testbench subclasses through
``extract_testbench`` / the ``is_testbench=True`` extractor mode.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from pysilicon.build.hwcodegen import (
    HwStmtExtractor,
    extract_kernel,
    extract_testbench,
)
from pysilicon.hw.hw_testbench import HwTestbench
from pysilicon.hw.hwstmt import SeqStmt
from pysilicon.simulation.simulation import Simulation


pytestmark = pytest.mark.phase1


# ---------------------------------------------------------------------------
# Phase 1 — class + routing
# ---------------------------------------------------------------------------

def test_hw_testbench_is_a_hwcomponent():
    """``HwTestbench`` inherits from ``HwComponent`` so it picks up the
    ``HwParam`` / ``HwConst`` machinery and the simulation lifecycle."""
    from pysilicon.hw.hw_component import HwComponent
    assert issubclass(HwTestbench, HwComponent)


def test_hw_testbench_marker_is_set():
    """The codegen routing dispatches on the ``_is_testbench`` class
    marker.  Subclasses inherit ``True``; ``HwComponent`` proper does not
    have the marker set."""
    from pysilicon.hw.hw_component import HwComponent
    assert getattr(HwTestbench, '_is_testbench', False) is True
    assert getattr(HwComponent, '_is_testbench', False) is False


def test_base_main_raises_not_implemented():
    """The base-class ``main()`` is a placeholder that fails fast when a
    subclass forgets to override it."""
    tb = HwTestbench(name='unused', sim=Simulation())
    with pytest.raises(NotImplementedError, match='main'):
        tb.main()


@dataclass
class _EmptyTB(HwTestbench):
    """Trivial subclass — body is docstring-only, no real testbench logic
    yet.  Phase 3+ exercises real extraction; Phase 1 just confirms the
    routing through the extractor doesn't crash on a minimal body."""

    def main(self) -> None:
        """Phase 1 placeholder body."""


def test_extract_testbench_routes_through_main():
    """``extract_testbench`` reads ``comp.main`` (not ``run_proc``) and
    produces a tree without raising on the trivial body."""
    tb = _EmptyTB(name='tb', sim=Simulation())
    tree = extract_testbench(tb)
    assert isinstance(tree, SeqStmt)
    assert tree.stmts == []


def test_extract_kernel_dispatches_testbench_subclasses():
    """The legacy ``extract_kernel`` entry point auto-routes testbench
    subclasses through ``extract_testbench`` — callers don't need to
    branch on the marker."""
    tb = _EmptyTB(name='tb', sim=Simulation())
    tree = extract_kernel(tb)
    assert isinstance(tree, SeqStmt)


def test_extractor_carries_is_testbench_flag():
    """The mode flag is plumbed through; ``HwStmtExtractor`` stashes it
    so Phase 3/4 emitter logic can branch on the extractor's mode."""
    tb = _EmptyTB(name='tb', sim=Simulation())
    ext = HwStmtExtractor(tb, method_name='main', is_testbench=True)
    assert ext._is_testbench is True
    # Default is False — preserves backwards compat for kernel-mode callers.
    kernel_ext = HwStmtExtractor(tb, method_name='main')
    assert kernel_ext._is_testbench is False
