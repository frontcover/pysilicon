"""rf_repeat_play through the **system** flow (``plans/system_model.md`` S3e): the transmitter as the
DUT of an ``HwSystem``, the RFDC's DAC model spanning the cut in the testbench.

The RF sink's recording is checked by the example, not against pysim (pysim runs to a time horizon,
the RTL to a cycle bound): the kernel flow's gate (``test_rf_circ_play_xsi.py``), claim for claim, on
the RTL's capture.  The DAG runs with ``synth="check"``.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from examples.rf_repeat_play.rf_repeat_play import NSAMP, waveform
from examples.rf_repeat_play.rf_repeat_play_system_build import HERE, TOP, build_dag
from tests.examples.test_rf_circ_play_xsi import (WANT_HOLE, WANT_LEAD_IN, WANT_TOTAL_SAMPLES,
                                                  WANT_TRAIN_LEN, WANT_TRAIN_START,
                                                  WANT_ZERO_RUNS, _played, _zero_runs)
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"


@pytest.fixture(scope="module")
def rf_repeat_play_system_run():
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    res = build_dag(work_dir=WORK).run(BuildConfig(root_dir=HERE, params={"synth": "check"}),
                                       through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the rf_repeat_play system DAG failed: {bad}")
    return WORK / TOP


@pytest.mark.xsi
def test_rf_repeat_play_system_records_the_sink_and_compares_nothing_it_cannot(
        rf_repeat_play_system_run):
    work = rf_repeat_play_system_run
    assert json.loads((work / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (work / "traces").iterdir()) == ["counters", "rf_out"]


@pytest.mark.xsi
def test_rf_repeat_play_system_rtl_matches_the_kernel_gate(rf_repeat_play_system_run):
    """The kernel gate's claims on the system run's capture: the run length, the start_now window
    bit-exact after the lead-in, two zero runs (the lead-in, one PERIOD hole), then no gap."""
    played = _played(rf_repeat_play_system_run / "xsi_run")
    assert played.size == WANT_TOTAL_SAMPLES
    lead_in = int(np.argmax(played != 0))
    assert lead_in == WANT_LEAD_IN
    assert np.array_equal(played[lead_in:lead_in + NSAMP], waveform())
    runs = _zero_runs(played)
    assert len(runs) == WANT_ZERO_RUNS
    assert runs[0] == (0, WANT_LEAD_IN) and runs[1][1] == WANT_HOLE
    last = runs[1][0] + runs[1][1]
    assert last == WANT_TRAIN_START
    assert played[last:].size == WANT_TRAIN_LEN and played[last:].all()
