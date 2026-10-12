"""The in-band interleaver through the **system** flow (``plans/system_model.md`` S3e): the kernel as
the DUT of an ``HwSystem``, its driver, sink and memory answered by their own models.

Two gates: what the testbench recorded (the sink's completions, the memory's arena) is identical RTL
against pysim, and the kernel flow's twelve-job gate (``test_interleaver_inband_xsi.py``) -- every Y
region bit-exact, one completion per job, the last at 1300 cycles -- holds on the RTL's recordings.
The DAG runs with ``synth="check"``: a stale or missing csynth top fails the run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.interleaver.interleaver_inband_system_build import (HERE, TOP, WANT_LAST_DONE_CYCLE,
                                                                  build_dag, check_xsi, system)
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"


def test_interleaver_inband_system_pysim_is_the_golden():
    """The system's pysim run passes the kernel flow's golden check."""
    system().run()


@pytest.fixture(scope="module")
def interleaver_inband_system_run():
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    res = build_dag(work_dir=WORK).run(BuildConfig(root_dir=HERE, params={"synth": "check"}),
                                       through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the interleaver_inband system DAG failed: {bad}")
    return WORK / TOP


@pytest.mark.xsi
def test_interleaver_inband_system_recordings_match_pysim(interleaver_inband_system_run):
    work = interleaver_inband_system_run
    assert json.loads((work / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (work / "traces").iterdir()) == ["out", "s_done"]


@pytest.mark.xsi
def test_interleaver_inband_system_twelve_jobs_at_the_kernel_gates_cycle(
        interleaver_inband_system_run):
    """THE kernel gate, through the system flow: twelve jobs, the last at 1300 cycles."""
    assert check_xsi(interleaver_inband_system_run) == WANT_LAST_DONE_CYCLE
