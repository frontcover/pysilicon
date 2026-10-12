"""rf_samp_buf_tx through the **system** flow (``plans/system_model.md`` S3e): the playout buffer as
the DUT (its wrapper: the kernel and its memory), the RFDC's DAC model spanning the cut.

Two gates: the responses are identical RTL against pysim, and the kernel flow's gate
(``test_rf_samp_buf_tx_xsi.py``), claim for claim, holds on the system run's recordings -- its
counters from the DAC model's own recording instead of a hand-written main.  The DAG runs with
``synth="check"``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.rf_samp_buf_tx.rf_samp_buf_tx import (GATE_COMMANDS, PRIMED_AT, SAMP_BW, TxResp,
                                                    expected_responses, find_loaded_run)
from examples.rf_samp_buf_tx.rf_samp_buf_tx_system_build import (HERE, TOP, build_dag, counters,
                                                                 played, recorded)
from tests.examples.test_rf_samp_buf_tx_xsi import (WANT_DAC_BLOCKS_OUT, WANT_MIN_LOADED_RUN,
                                                    WANT_RESP_LAST_CYCLE)
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
        pytest.fail(f"the rf_samp_buf_tx system DAG failed: {bad}")
    return WORK / TOP


@pytest.mark.xsi
def test_rf_samp_buf_tx_system_responses_match_pysim(run):
    assert json.loads((run / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (run / "traces").iterdir()) == ["counters", "resp", "rf_out"]


@pytest.mark.xsi
def test_rf_samp_buf_tx_system_plays_the_loaded_ramp_and_answers_in_order(run):
    got = played(run)
    assert got.size > PRIMED_AT and find_loaded_run(got) >= WANT_MIN_LOADED_RUN
    flat = recorded(run, "resp")
    n = TxResp.nwords_per_inst(SAMP_BW)
    assert [tuple(int(v) for v in flat[i:i + n]) for i in range(0, flat.size, n)] == \
        expected_responses(1)


@pytest.mark.xsi
def test_rf_samp_buf_tx_system_counters_are_the_kernel_gates(run):
    c = counters(run)
    assert c["DAC_WORDS_RECV"] > 0 and c["DAC_BLOCKS_OUT"] == WANT_DAC_BLOCKS_OUT
    assert c["RESP_WORDS"] == len(GATE_COMMANDS) * TxResp.nwords_per_inst(SAMP_BW)
    assert c["RESP_LAST_CYCLE"] == WANT_RESP_LAST_CYCLE
