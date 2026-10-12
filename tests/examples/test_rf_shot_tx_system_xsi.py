"""rf_shot_tx through the **system** flow (``plans/system_model.md`` S3e): the shot transmitter as the
DUT (its wrapper: the kernel and its memory), the RFDC's DAC model spanning the cut -- both builds,
both frame streams, four runs.

Per run: the responses are identical RTL against pysim, and the kernel flow's gates
(``test_rf_shot_tx_xsi.py`` / ``test_rf_shot_tx_abs_xsi.py``) hold on the system run's recordings --
the playout (its passes, its block shape, its startup transient; for the absolute build the address
is the phase and a playout starts on a boundary), every header answered in order, and the DAC's
counters (from the model's own recording) and the last response's cycle.  Their waveform claims stay
with the kernel gates (the system flow does not trace yet).  The DAG runs with ``synth="check"``.
"""
from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import pytest

import tests.examples.test_rf_shot_tx_abs_xsi as abs_gate
import tests.examples.test_rf_shot_tx_xsi as default_gate
from examples.rf_shot_tx.rf_shot_tx import (check_address_is_the_phase, check_finite_playout,
                                            check_loop_playout, check_phase, check_responses,
                                            check_starts_on_a_boundary, segments, transients,
                                            BLKSIZE)
from examples.rf_shot_tx.rf_shot_tx_system_build import (BUILDS, HERE, SCENARIO_NAMES, build_dag,
                                                         counters, played, responses, system, top)
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"
GATES = {"default": default_gate, "abs": abs_gate}


@pytest.fixture(scope="module", params=[f"{b}-{s}" for b, s in product(sorted(BUILDS), SCENARIO_NAMES)])
def run(request):
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    build, scenario = request.param.split("-")
    res = build_dag(build, scenario, work_dir=WORK).run(
        BuildConfig(root_dir=HERE, params={"synth": "check"}), through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the rf_shot_tx ({build}, {scenario}) system DAG failed: {bad}")
    work = WORK / top(build, scenario)
    return build, scenario, work, played(work)


@pytest.mark.xsi
def test_rf_shot_tx_system_responses_match_pysim(run):
    _b, _s, work, _p = run
    assert json.loads((work / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (work / "traces").iterdir()) == ["counters", "resp", "rf_out"]


@pytest.mark.xsi
def test_rf_shot_tx_system_plays_as_the_kernel_gate_recorded(run):
    build, scenario, _work, p = run
    gate = GATES[build]
    where = f"XSI system {build} {scenario}: "
    if build == "default":
        (check_finite_playout if scenario == "cmd" else check_loop_playout)(p, where=where)
        check_phase(p, where=where)
        want_startup = gate.WANT_STARTUP[("rtl", scenario)]
    else:
        if scenario == "cmd":
            check_address_is_the_phase(p, where=where)
            check_starts_on_a_boundary(p, where=where)
            check_finite_playout(p, where=where)
        want_startup = gate.WANT_STARTUP[scenario]
    assert [(bool(f), int(s.size) // BLKSIZE) for f, s in segments(p)] == \
        gate.WANT_SEGMENT_BLOCKS[scenario]
    assert transients(p)[0] == want_startup


@pytest.mark.xsi
def test_rf_shot_tx_system_answers_every_header_at_the_recorded_cycle(run):
    build, scenario, work, _p = run
    gate = GATES[build]
    check_responses(responses(work), system(build, scenario).frames,
                    where=f"XSI system {build} {scenario}: ")
    c = counters(work)
    assert c["RESP_LAST_CYCLE"] == gate.WANT_RESP_LAST_CYCLE[scenario]


@pytest.mark.xsi
def test_rf_shot_tx_system_the_dac_is_never_starved(run):
    build, _scenario, work, _p = run
    gate = GATES[build]
    c = counters(work)
    assert c["DAC_BLOCKS_ZERO_FILLED"] == gate.WANT_ZERO_FILLED
    assert c["DAC_WORDS_RECV"] == gate.WANT_DAC_WORDS
    assert (c["DAC_UNDERRUN"], c["DAC_LAST_UNDERRUN_CYCLE"]) == (gate.WANT_DAC_UNDERRUN,
                                                                 gate.WANT_LAST_UNDERRUN_CYCLE)
