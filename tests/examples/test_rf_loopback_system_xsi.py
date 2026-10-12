"""rf_loopback through the **system** flow (``plans/system_model.md`` S3d): the RFDC in the testbench.

The DUT is the pass-through kernel alone; the converter is a testbench participant whose two models
each span the cut -- a stream port of the top on the fabric side, an RF behavioral edge on the other.
The sink's recording is checked by the example, not against pysim (the two backends legitimately
disagree about sub-block loss): the bundle half of the kernel flow's gate, claim for claim.  The
loss *counters* that gate also prints come from a hand-written main and are not reproduced here.

The DAG runs with ``synth="check"``: a stale or missing csynth top fails the run, naming it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.rf_loopback.rf_loopback_system_build import (HERE, TOP, WANT_DATA_BLOCKS_OUT,
                                                          WANT_SINK_BLOCKS, build_dag, check_xsi)
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"


@pytest.fixture(scope="module")
def rf_system_run():
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    res = build_dag(work_dir=WORK).run(BuildConfig(root_dir=HERE, params={"synth": "check"}),
                                       through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the rf_loopback system DAG failed: {bad}")
    return WORK / TOP


@pytest.mark.xsi
def test_rf_system_records_the_sink_and_compares_nothing_it_cannot(rf_system_run):
    """The sink's recording is gathered from both runs, and -- marked for the example's check --
    not compared exactly."""
    work = rf_system_run
    assert json.loads((work / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (work / "traces").iterdir()) == ["rf_out"]


@pytest.mark.xsi
def test_rf_system_rtl_matches_the_kernel_gate(rf_system_run):
    """Zero-fill startup, the first data block bit-exact, and the kernel gate's counts."""
    assert check_xsi(rf_system_run) == {"sink_blocks": WANT_SINK_BLOCKS,
                                        "data_blocks": WANT_DATA_BLOCKS_OUT}
