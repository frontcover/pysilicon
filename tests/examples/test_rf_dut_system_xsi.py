"""The pass-through's DUT gate through the **system** flow (``plans/system_model.md`` S3e): the kernel
alone as the DUT of an ``HwSystem``, between the generic driver's and sink's own models.

Two gates: the sink's recording is identical RTL against pysim, and the kernel flow's golden
(``test_xsi_bfm.py``'s ``rf_pass_through`` row) -- every word relayed bit-exact, the last at cycle
1066 -- holds on the RTL's recording.  The DAG runs with ``synth="check"``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.rf_loopback.rf_dut_system_build import HERE, TOP, build_dag, check_xsi, system
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"


def test_rf_dut_system_pysim_relays_every_word():
    system().run()


@pytest.fixture(scope="module")
def rf_dut_system_run():
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    res = build_dag(work_dir=WORK).run(BuildConfig(root_dir=HERE, params={"synth": "check"}),
                                       through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the rf_pass_through system DAG failed: {bad}")
    return WORK / TOP


@pytest.mark.xsi
def test_rf_dut_system_recording_matches_pysim(rf_dut_system_run):
    work = rf_dut_system_run
    assert json.loads((work / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (work / "traces").iterdir()) == ["s_out"]


@pytest.mark.xsi
def test_rf_dut_system_rtl_matches_the_kernel_gate(rf_dut_system_run):
    """THE kernel gate, through the system flow: bit-exact relay, the last word at cycle 1066."""
    check_xsi(rf_dut_system_run)
