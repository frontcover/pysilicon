"""rf_blk_delay through the **system** flow (``plans/system_model.md`` S3e): the pattern-B loop as the
DUT (its wrapper: the kernel and both buffers' memories), the RFDC's models spanning the cut.

The kernel flow's gate (``test_rf_blk_delay_xsi.py``), claim for claim, on the system run's
recordings: its counters from the converter models' own recordings instead of a hand-written main.
The DAG runs with ``synth="check"``.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from examples.rf_blk_delay.rf_blk_delay import BLKSIZE, DELAY_BLOCKS, N_BLK, SRC_NBLK, ramp_samples
from examples.rf_blk_delay.rf_blk_delay_system_build import (HERE, TOP, WORD_BW, build_dag,
                                                             counters, measured_delay, played,
                                                             responses)
from tests.examples.test_rf_blk_delay_xsi import (RTL_GRID_SKEW, WANT_BLOCKS_RELAYED,
                                                  WANT_DAC_BLOCKS_OUT, WANT_RX_RESP_LAST_CYCLE,
                                                  WANT_TX_RESP_LAST_CYCLE)
from waveflow.build.build import BuildConfig
from waveflow.hw.rf_samp_buf import RF_SAMP_BUF_OK, RxResp
from waveflow.hw.rf_samp_buf_tx import TxResp
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
        pytest.fail(f"the rf_blk_delay system DAG failed: {bad}")
    work = WORK / TOP
    return work, counters(work)


@pytest.mark.xsi
def test_rf_blk_delay_system_records_everything_and_compares_nothing_it_cannot(run):
    work, _c = run
    assert json.loads((work / "compare.json").read_text())["trace_mismatches"] == []
    assert sorted(p.name for p in (work / "traces").iterdir()) == [
        "counters", "rf_out", "rxresp", "txresp"]


@pytest.mark.xsi
def test_rf_blk_delay_system_lost_nothing_and_the_converter_set_the_rate(run):
    _work, c = run
    assert c["ADC_WORDS_SENT"] == SRC_NBLK * BLKSIZE // 4 and c["ADC_DROPPED"] == 0
    assert c["DAC_UNDERRUN"] == 0 and c["DAC_BLOCKS_ZERO_FILLED"] == 0
    assert abs(c["DAC_WORDS_RECV"] - 15000) <= 2


@pytest.mark.xsi
def test_rf_blk_delay_system_is_bit_exact_at_the_asked_delay(run):
    work, _c = run
    p = played(work)
    shift = measured_delay(p)
    assert shift is not None and DELAY_BLOCKS * BLKSIZE - shift == RTL_GRID_SKEW
    n = min(N_BLK * BLKSIZE, p.size - shift)
    assert n >= (N_BLK - 1) * BLKSIZE
    assert np.array_equal(p[shift:shift + n], ramp_samples()[:n])


@pytest.mark.xsi
def test_rf_blk_delay_system_answered_every_command_at_the_recorded_cycles(run):
    work, c = run
    want = [(k + 1, RF_SAMP_BUF_OK, BLKSIZE) for k in range(WANT_BLOCKS_RELAYED)]
    assert responses(work, "rxresp", RxResp)[0] == want
    assert responses(work, "txresp", TxResp)[0] == want
    assert c["RX_RESP_WORDS"] == WANT_BLOCKS_RELAYED * RxResp.nwords_per_inst(WORD_BW)
    assert c["TX_RESP_WORDS"] == WANT_BLOCKS_RELAYED * TxResp.nwords_per_inst(WORD_BW)
    assert (c["RX_RESP_LAST_CYCLE"], c["TX_RESP_LAST_CYCLE"]) == (WANT_RX_RESP_LAST_CYCLE,
                                                                  WANT_TX_RESP_LAST_CYCLE)
    assert c["DAC_BLOCKS_OUT"] == WANT_DAC_BLOCKS_OUT
