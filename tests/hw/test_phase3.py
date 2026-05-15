"""Phase 3 integration tests.

- HwStmtExtractor on PolyAccelComponent.run_proc produces the expected IR tree.
- End-to-end poly simulation still produces correct results.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import numpy.testing as npt
import pytest

POLY_DIR = Path(__file__).resolve().parents[2] / "examples" / "poly"
if str(POLY_DIR) not in sys.path:
    sys.path.insert(0, str(POLY_DIR))

from poly import CoeffArray, Float32, PolyAccelComponent, PolyCmdHdr, PolyTB, connect

from pysilicon.build.hwcodegen import HwStmtExtractor, SynthesisError
from pysilicon.hw.clock import Clock
from pysilicon.hw.hw_component import HwComponent, HwParam, SynthContext
from pysilicon.hw.hwstmt import HookStmt, SeqStmt, WhileStmt
from pysilicon.hw.interface import (
    StreamDrainStmt,
    StreamGetStmt,
)
from pysilicon.simulation.simulation import Simulation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_accel() -> PolyAccelComponent:
    sim = Simulation()
    return PolyAccelComponent(name='test_accel', sim=sim)


# ---------------------------------------------------------------------------
# HwStmtExtractor on PolyAccelComponent.run_proc
# ---------------------------------------------------------------------------

def test_extractor_root_is_while():
    comp = _make_accel()
    tree = HwStmtExtractor(comp).extract()
    assert isinstance(tree, WhileStmt)


def test_extractor_body_has_two_stmts():
    comp = _make_accel()
    tree = HwStmtExtractor(comp).extract()
    assert isinstance(tree.body, SeqStmt)
    assert len(tree.body.stmts) == 2


def test_extractor_first_stmt_is_stream_get():
    comp = _make_accel()
    stmts = HwStmtExtractor(comp).extract().body.stmts
    assert isinstance(stmts[0], StreamGetStmt)


def test_extractor_second_stmt_is_hook():
    comp = _make_accel()
    stmts = HwStmtExtractor(comp).extract().body.stmts
    assert isinstance(stmts[1], HookStmt)


def test_extractor_get_output_name():
    comp = _make_accel()
    stmts = HwStmtExtractor(comp).extract().body.stmts
    assert stmts[0].outputs[0].name == 'cmd_hdr'


def test_extractor_hook_has_no_outputs():
    comp = _make_accel()
    stmts = HwStmtExtractor(comp).extract().body.stmts
    assert stmts[1].outputs == []


# ---------------------------------------------------------------------------
# HwParam detection on PolyAccelComponent
# ---------------------------------------------------------------------------

def test_poly_accel_hwparam_fields():
    sim = Simulation()
    comp = PolyAccelComponent(name='p', sim=sim)
    ctx = SynthContext.from_component(comp)
    assert 'in_bw' in ctx.params
    assert 'out_bw' in ctx.params
    assert ctx.params['in_bw'] == 'IN_BW'
    assert ctx.params['out_bw'] == 'OUT_BW'


# ---------------------------------------------------------------------------
# End-to-end simulation correctness
# ---------------------------------------------------------------------------

def _run_sim(nsamp: int = 100):
    coeffs = CoeffArray()
    coeffs.val = np.array([1.0, -2.0, -3.0, 4.0], dtype=np.float32)

    cmd_hdr = PolyCmdHdr()
    cmd_hdr.tx_id = 42
    cmd_hdr.coeffs = coeffs.val
    cmd_hdr.nsamp = nsamp

    samp_in = np.linspace(0.0, 1.0, nsamp, dtype=np.float32)

    sim = Simulation()
    clk = Clock(freq=1e9)

    accel = PolyAccelComponent(name='poly_accel', sim=sim)
    tb    = PolyTB(name='poly_tb', sim=sim, cmd_hdr=cmd_hdr, samp_in=samp_in)

    connect(sim, tb, accel, clk)
    sim.run_sim()
    return tb, cmd_hdr, samp_in, sim


def test_sim_tx_id_echoed():
    tb, _, _, _ = _run_sim()
    assert int(tb.resp_hdr.tx_id) == 42


def test_sim_nsamp_read_correct():
    tb, _, _, _ = _run_sim(nsamp=50)
    assert int(tb.resp_ftr.nsamp_read) == 50


def test_sim_no_error():
    tb, _, _, _ = _run_sim()
    assert tb.resp_ftr.error.name == 'NO_ERROR'


def _poly_reference(cmd_hdr, samp_in: np.ndarray) -> np.ndarray:
    y = np.zeros_like(samp_in)
    power = np.ones_like(samp_in)
    for c in cmd_hdr.coeffs:
        y += c * power
        power *= samp_in
    return y


def test_sim_output_matches_reference():
    tb, cmd_hdr, samp_in, _ = _run_sim(nsamp=100)
    expected_samp = _poly_reference(cmd_hdr, samp_in)
    npt.assert_allclose(np.asarray(tb.samp_out, dtype=np.float32), expected_samp, rtol=1e-5)


def test_sim_different_nsamp():
    tb, cmd_hdr, samp_in, _ = _run_sim(nsamp=256)
    assert int(tb.resp_ftr.nsamp_read) == 256
    assert tb.resp_ftr.error.name == 'NO_ERROR'
    expected_samp = _poly_reference(cmd_hdr, samp_in)
    npt.assert_allclose(np.asarray(tb.samp_out, dtype=np.float32), expected_samp, rtol=1e-5)


def test_sim_timing_is_nonzero():
    """Verify that proc_latency delays the output (sim time advances during run)."""
    _, _, _, sim = _run_sim()
    assert sim.env.now > 0


# ---------------------------------------------------------------------------
# StreamDrainStmt is importable and is a SynthCallStmt
# ---------------------------------------------------------------------------

def test_stream_drain_stmt_is_synth_call_stmt():
    from pysilicon.hw.hwstmt import SynthCallStmt
    assert issubclass(StreamDrainStmt, SynthCallStmt)


# ---------------------------------------------------------------------------
# @synthesizable on stream methods
# ---------------------------------------------------------------------------

def test_stream_get_is_synthesizable():
    sim = Simulation()
    ep = PolyAccelComponent(name='x', sim=sim).s_in
    assert getattr(ep.get, '_is_synthesizable', False) is True


def test_stream_write_is_synthesizable():
    sim = Simulation()
    ep = PolyAccelComponent(name='y', sim=sim).m_out
    assert getattr(ep.write, '_is_synthesizable', False) is True


def test_stream_drain_is_synthesizable():
    sim = Simulation()
    ep = PolyAccelComponent(name='z', sim=sim).s_in
    assert getattr(ep.drain, '_is_synthesizable', False) is True
