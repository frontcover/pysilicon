"""bram_access through the **system** flow (``plans/system_model.md`` S3e): a kernel with hand-written
RTL beside it as the DUT of an ``HwSystem``, instantiated as its wrapper.

Two gates: the three sinks' recordings are identical RTL against pysim, and the kernel flow's golden
(``test_bram_access_xsi.py``) -- every response and read word, the last at cycle 568 -- holds on the
RTL's recordings.  The DAG runs with ``synth="check"``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.bram_access.bram_access_system_build import HERE, TOP, build_dag, check_xsi, system
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"


def test_bram_access_system_pysim_is_the_golden():
    system().run()


def test_bram_access_system_top_instantiates_the_wrapper():
    """The DUT is the wrapper, rendered into the top's file; the csynth'd kernel and the memory
    are what it holds, the memory's source on the compile list."""
    from waveflow.build.system_top import SystemTopSpec

    spec = SystemTopSpec.from_system(system(), top=TOP)
    (k,) = spec.kernels
    assert (k.module, k.csynth_module) == ("bram_access_top", "bram_access")
    assert spec.modules == ("bram_access",)
    v = spec.render_verilog()
    assert "module bram_access_top (" in v and "bram_access_top u_xsi_tb_dut (" in v
    insts = {i.path: (i.module, i.kind) for i in spec.instances()}
    assert ("bram_access", "kernel") in insts.values() and ("bram_t2p", "leaf") in insts.values()
    assert any(p.name == "bram_t2p.v" for p in spec.leaf_sources())


@pytest.fixture(scope="module")
def bram_access_system_run():
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    res = build_dag(work_dir=WORK).run(BuildConfig(root_dir=HERE, params={"synth": "check"}),
                                       through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the bram_access system DAG failed: {bad}")
    return WORK / TOP


@pytest.mark.xsi
def test_bram_access_system_recordings_match_pysim(bram_access_system_run):
    work = bram_access_system_run
    assert json.loads((work / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (work / "traces").iterdir()) == ["data_r", "resp_r", "resp_w"]


@pytest.mark.xsi
def test_bram_access_system_rtl_matches_the_kernel_gate(bram_access_system_run):
    """THE kernel gate, through the system flow: the golden, the last read word at cycle 568."""
    check_xsi(bram_access_system_run)
