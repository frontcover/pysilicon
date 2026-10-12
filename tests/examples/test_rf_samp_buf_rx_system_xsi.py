"""rf_samp_buf_rx through the **system** flow (``plans/system_model.md`` S3e): the capture buffer as
the DUT (its wrapper: the kernel and its memory), the RFDC's ADC model spanning the cut.

Two gates: the capture and the responses are identical RTL against pysim, and the kernel flow's gate
(``test_rf_samp_buf_rx_xsi.py``), claim for claim, holds on the system run's recordings -- its
counters from the ADC model's own recording instead of a hand-written main.  The DAG runs with
``synth="check"``.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from examples.rf_samp_buf_rx.rf_samp_buf_rx import WORD_BW, RxResp, expected_capture
from examples.rf_samp_buf_rx.rf_samp_buf_rx_system_build import HERE, TOP, build_dag, counters, recorded
from tests.examples.test_rf_samp_buf_rx_xsi import WANT_ADC_WORDS, WANT_LAST_CYCLE
from waveflow.build.build import BuildConfig
from waveflow.toolchain.toolchain import find_vivado_path

WORK = Path(__file__).resolve().parents[2] / "tests" / "build" / "_xsi_work"


@pytest.fixture(scope="module")
def run():
    if not find_vivado_path():
        pytest.skip("XSI gate prerequisite missing: Vivado (xsim)")
    res = build_dag(work_dir=WORK).run(BuildConfig(root_dir=HERE, params={"synth": "check"}),
                                       through="compare")
    bad = {n: r.message for n, r in res.items() if not r.success}
    if bad:
        pytest.fail(f"the rf_samp_buf_rx system DAG failed: {bad}")
    return WORK / TOP


@pytest.mark.xsi
def test_rf_samp_buf_rx_system_recordings_match_pysim(run):
    assert json.loads((run / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (run / "traces").iterdir()) == ["counters", "out", "resp"]


@pytest.mark.xsi
def test_rf_samp_buf_rx_system_captures_and_answers_as_predicted(run):
    want, want_resp = expected_capture()
    assert np.array_equal(recorded(run, "out"), want)
    flat = recorded(run, "resp")
    n = RxResp.nwords_per_inst(WORD_BW)
    assert [tuple(int(v) for v in flat[i:i + n]) for i in range(0, flat.size, n)] == want_resp


@pytest.mark.xsi
def test_rf_samp_buf_rx_system_counters_are_the_kernel_gates(run):
    c = counters(run)
    assert (c["ADC_WORDS_SENT"], c["ADC_DROPPED"]) == (WANT_ADC_WORDS, 0)
    assert c["OUT_LAST_CYCLE"] == WANT_LAST_CYCLE
    assert c["RESP_WORDS"] == c["CMD_TOTAL"]
