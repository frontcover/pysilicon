"""fir_block through the **system** flow (``plans/system_model.md`` S3e): the filter kernel as the DUT
of an ``HwSystem``, its driver, sink and memory answered by their own models.

Two gates: what the testbench recorded (the sink's completions, the memory's arena) is identical RTL
against pysim, and the kernel flow's golden (``test_fir_block_xsi.py``) -- every block bit-exact across
a tap reload and the carry, one completion per command -- holds on the RTL's recordings.  The DAG runs
with ``synth="check"``: a stale or missing csynth top fails the run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.fir_block.fir_block_system_build import HERE, TOP, build_dag, check_xsi, system
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"


def test_fir_block_system_pysim_is_the_golden():
    """The system's pysim run passes the kernel flow's golden check."""
    system().run()


@pytest.fixture(scope="module")
def fir_block_system_run():
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    res = build_dag(work_dir=WORK).run(BuildConfig(root_dir=HERE, params={"synth": "check"}),
                                       through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the fir_block system DAG failed: {bad}")
    return WORK / TOP


@pytest.mark.xsi
def test_fir_block_system_recordings_match_pysim(fir_block_system_run):
    assert json.loads((fir_block_system_run / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (fir_block_system_run / "traces").iterdir()) == ["out", "s_done"]


@pytest.mark.xsi
def test_fir_block_system_rtl_matches_golden(fir_block_system_run):
    """THE kernel gate, through the system flow."""
    check_xsi(fir_block_system_run)
