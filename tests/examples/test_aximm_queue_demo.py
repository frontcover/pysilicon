"""Test that the aximm_queue_demo harness runs and self-checks."""
from __future__ import annotations

import numpy as np

from examples.interface.aximm_queue_demo import AXIMMQueueDemo, run_and_check


def test_aximm_queue_demo_passes():
    demo = AXIMMQueueDemo()
    received = demo.run_and_check()
    # The consumer received the producer's exact sequence, in FIFO order.
    np.testing.assert_array_equal(received, np.arange(demo.N, dtype=np.uint32))


def test_run_and_check_entry_point():
    received = run_and_check()
    assert len(received) > 0
