"""state_toy through the **system** flow (``plans/system_model.md`` S3e): the accumulator as the DUT
of an ``HwSystem``, its driver and sink answered by their own models.

Two gates: the sink's recording is identical RTL against pysim, and the golden -- per-lane totals
climbing 1..5 across five firings, the kernel flow's own gate (``test_state_toy.py``) -- holds on the
RTL's recording.  The DAG runs with ``synth="check"``: a stale or missing csynth top fails the run.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from examples.state_toy.state_toy import expected_words
from examples.state_toy.state_toy_system_build import HERE, NVEC, TOP, build_dag, check_xsi, system
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"


def test_state_toy_system_pysim_is_the_golden():
    """The system's pysim run reproduces the kernel flow's golden."""
    np.testing.assert_array_equal(system().run(), expected_words(NVEC))


@pytest.fixture(scope="module")
def state_toy_system_run():
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    res = build_dag(work_dir=WORK).run(BuildConfig(root_dir=HERE, params={"synth": "check"}),
                                       through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the state_toy system DAG failed: {bad}")
    return WORK / TOP


@pytest.mark.xsi
def test_state_toy_system_recording_matches_pysim(state_toy_system_run):
    assert json.loads((state_toy_system_run / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (state_toy_system_run / "traces").iterdir()) == ["m_out"]


@pytest.mark.xsi
def test_state_toy_system_state_persists_in_rtl(state_toy_system_run):
    """THE kernel gate, through the system flow: 1..5 per lane, not 1,1,1,1,1."""
    check_xsi(state_toy_system_run)
