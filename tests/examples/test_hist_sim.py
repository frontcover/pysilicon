"""Phase 1: HistAccel HwComponent SimPy parity with the numpy golden.

Runs the synthesizable HistAccel over a SimPy DirectMMIF + MemComponent and
asserts it reproduces HistogramAccel's histogram counts, plus the three
validation → status paths.
"""
from __future__ import annotations

import numpy as np
import pytest

from examples.shared_mem.hist import run_sim
from examples.shared_mem.hist_demo import MAX_NDATA, MAX_NBINS, HistError, HistTest


@pytest.mark.parametrize(
    "seed,ndata,nbins",
    [(7, 37, 6), (3, 100, 12), (5, 256, 32), (11, 1, 4)],
)
def test_histaccel_matches_numpy_golden(seed, ndata, nbins):
    """HistAccel (SimPy) reproduces HistogramAccel's counts over the same data."""
    # HistTest.simulate() runs the numpy HistogramAccel golden and records the
    # generated data/edges and its counts.
    ht = HistTest(seed=seed, ndata=ndata, nbins=nbins)
    ht.simulate()

    res = run_sim(ht.data, ht.bin_edges, nbins=nbins, tx_id=seed)

    assert res.status == HistError.NO_ERROR
    np.testing.assert_array_equal(res.counts, ht.counts)
    assert res.passed


def test_invalid_ndata_status():
    """ndata greater than max_ndata selects INVALID_NDATA before any memory op."""
    over = MAX_NDATA + 1
    data = np.zeros(over, dtype=np.float32)
    edges = np.sort(np.linspace(-1, 1, 5).astype(np.float32))
    res = run_sim(data, edges, nbins=6)
    assert res.status == HistError.INVALID_NDATA


def test_invalid_nbins_status():
    """nbins greater than max_nbins selects INVALID_NBINS."""
    over = MAX_NBINS + 1
    data, _ = np.random.default_rng(1).normal(size=20).astype(np.float32), None
    edges = np.sort(np.random.default_rng(1).uniform(-2, 2, size=over - 1).astype(np.float32))
    res = run_sim(data, edges, nbins=over)
    assert res.status == HistError.INVALID_NBINS


def test_address_error_status():
    """A misaligned data address selects ADDRESS_ERROR (mem_bw=32 → 4-byte words)."""
    data = np.random.default_rng(4).normal(size=30).astype(np.float32)
    edges = np.sort(np.random.default_rng(4).uniform(-2, 2, size=5).astype(np.float32))
    res = run_sim(data, edges, nbins=6, addr_misalign=1)   # 1 byte off a word boundary
    assert res.status == HistError.ADDRESS_ERROR
