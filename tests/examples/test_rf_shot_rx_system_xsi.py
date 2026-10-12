"""rf_shot_rx through the **system** flow (``plans/system_model.md`` S3e), both builds: the ping-pong
receiver as the DUT (its wrapper: the kernel and its memory), the RFDC's ADC model spanning the cut.

Per build: the window frames are identical RTL against pysim, and the kernel flow's gates
(``test_rf_shot_rx_xsi.py`` / ``test_rf_shot_rx_abs_xsi.py``) hold on the system run's recordings --
the ramp with no gap, alternating halves, CAP_OK headers, the converter's counters (from the ADC
model's own recording) and the last window's cycle; for the absolute build, the address is the phase.
Their waveform claims stay with the kernel gates (the system flow does not trace yet).  The DAG runs
with ``synth="check"``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.rf_shot_rx.rf_shot_rx import (CAP_OK, CODE_BASE, N_REGION, REGION_SAMPLES,
                                            REGION_WORDS, WORD, RfShotRxAbs, check_windows,
                                            expected_bases, windows_as_codes)
from examples.rf_shot_rx.rf_shot_rx_system_build import BUILDS, HERE, build_dag, counters, frames
from tests.examples.test_rf_shot_rx_xsi import (WANT_ADC_BLOCKS, WANT_ADC_DROPPED, WANT_ADC_WORDS,
                                                WANT_WIN_LAST_CYCLE, WANT_WIN_WORDS)
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"


@pytest.fixture(scope="module", params=sorted(BUILDS))
def run(request):
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    build = request.param
    res = build_dag(build, work_dir=WORK).run(BuildConfig(root_dir=HERE, params={"synth": "check"}),
                                              through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the rf_shot_rx ({build}) system DAG failed: {bad}")
    return build, WORK / BUILDS[build][1]


@pytest.mark.xsi
def test_rf_shot_rx_system_frames_match_pysim(run):
    _build, work = run
    assert json.loads((work / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (work / "traces").iterdir()) == ["counters", "win"]


@pytest.mark.xsi
def test_rf_shot_rx_system_captures_the_ramp_in_alternating_halves(run):
    _build, work = run
    fr = frames(work)
    flat = check_windows(fr, where="XSI system: ")
    assert flat.size == 4 * REGION_SAMPLES
    assert int(flat[0]) == CODE_BASE and int(flat[-1]) == CODE_BASE + flat.size - 1
    wins = windows_as_codes(fr)
    got = [int(h.base_addr) for h, _c in wins]
    assert got == expected_bases(len(wins))
    assert set(got) == {i * REGION_WORDS for i in range(N_REGION)}
    assert [int(h.status) for h, _c in wins] == [CAP_OK] * len(wins)
    assert [int(h.n_dropped) for h, _c in wins] == [0] * len(wins)


@pytest.mark.xsi
def test_rf_shot_rx_system_counters_are_the_kernel_gates(run):
    _build, work = run
    c = counters(work)
    assert (c["ADC_WORDS"], c["ADC_DROPPED"], c["ADC_BLOCKS_IN"]) == (
        WANT_ADC_WORDS, WANT_ADC_DROPPED, WANT_ADC_BLOCKS)
    assert (c["WIN_WORDS"], c["WIN_LAST_CYCLE"]) == (WANT_WIN_WORDS, WANT_WIN_LAST_CYCLE)


@pytest.mark.xsi
def test_rf_shot_rx_system_the_address_is_the_phase(run):
    """The absolute build's feature -- every window at the address its absolute index names -- and,
    on a run that loses nothing, the default build's too: the two builds are identical then (the
    kernel gate's ``test_the_two_builds_are_identical_when_nothing_is_lost``)."""
    _build, work = run
    from waveflow.simulation.simulation import Simulation

    dut = RfShotRxAbs.for_word(WORD, depth=REGION_WORDS * N_REGION, sim=Simulation(), name="chk")
    idx = dut.assert_windows_absolute(frames(work), where="RTL system: ")
    assert idx == [0, REGION_WORDS, 2 * REGION_WORDS, 3 * REGION_WORDS]
