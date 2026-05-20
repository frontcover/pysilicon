"""End-to-end extractor tests targeting the PolyAccelComponent kernel."""
from __future__ import annotations

import pytest

from pysilicon.build.hwcodegen import HwStmtExtractor
from pysilicon.hw.hw_component import HwComponent
from pysilicon.hw.hwstmt import (
    CaseStmt,
    ReturnStmt,
    SeqStmt,
    WhileStmt,
)
from pysilicon.hw.synth import synthesizable
from pysilicon.simulation.simulation import Simulation


# ---------------------------------------------------------------------------
# Minimal synthesizable endpoint for testing
# ---------------------------------------------------------------------------

class _MockEndpoint:
    """Stand-in for a stream endpoint with synthesizable get/write."""

    @synthesizable(synth_fn=lambda ctx, i, o: "")
    def get(self):
        pass

    @synthesizable(synth_fn=lambda ctx, i, o: "")
    def write(self, data):
        pass


def _make_comp(comp_cls):
    sim = Simulation()
    comp = comp_cls(sim=sim)
    comp.ep = _MockEndpoint()
    return comp


# ---------------------------------------------------------------------------
# Phase 1: ReturnStmt
# ---------------------------------------------------------------------------

class _ReturnInIfComp(HwComponent):
    def run_proc(self):
        while True:
            x = yield from self.ep.get()
            if x.f == 1:
                return


def test_return_inside_if_body():
    comp = _make_comp(_ReturnInIfComp)
    tree = HwStmtExtractor(comp).extract()
    assert isinstance(tree, WhileStmt)
    case_stmt = tree.body.stmts[1]
    assert isinstance(case_stmt, CaseStmt)
    first_in_branch = case_stmt.if_true.stmts[0]
    assert isinstance(first_in_branch, ReturnStmt)
    assert first_in_branch.value is None


class _ReturnAtTopComp(HwComponent):
    def run_proc(self):
        while True:
            x = yield from self.ep.get()
            return


def test_return_at_top_of_while():
    comp = _make_comp(_ReturnAtTopComp)
    tree = HwStmtExtractor(comp).extract()
    assert isinstance(tree, WhileStmt)
    assert isinstance(tree.body, SeqStmt)
    assert any(isinstance(s, ReturnStmt) for s in tree.body.stmts)
