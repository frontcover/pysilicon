"""rf_relayout through the **system** flow (``plans/system_model.md`` S3e): the two conversions as the
DUT of an ``HwSystem``, its driver and sink answered by their own models.

Two gates: the sink's recording is identical RTL against pysim, and the kernel flow's golden
(``test_rf_relayout_xsi.py``) -- every converter word back unchanged through the dense layout, the
64th at cycle 68 -- holds on the RTL's recording.  The DAG runs with ``synth="check"``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.rf_relayout.rf_relayout_system_build import HERE, TOP, build_dag, check_xsi, system
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"


def test_rf_relayout_system_pysim_is_the_golden():
    """The system's pysim run passes the loopback identity."""
    system().run()


@pytest.fixture(scope="module")
def rf_relayout_system_run():
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    res = build_dag(work_dir=WORK).run(BuildConfig(root_dir=HERE, params={"synth": "check"}),
                                       through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the rf_relayout system DAG failed: {bad}")
    return WORK / TOP


@pytest.mark.xsi
def test_rf_relayout_system_recording_matches_pysim(rf_relayout_system_run):
    work = rf_relayout_system_run
    assert json.loads((work / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (work / "traces").iterdir()) == ["out"]


@pytest.mark.xsi
def test_rf_relayout_system_rtl_matches_the_kernel_gate(rf_relayout_system_run):
    """THE kernel gate, through the system flow: the identity, the last word at cycle 68."""
    check_xsi(rf_relayout_system_run)
