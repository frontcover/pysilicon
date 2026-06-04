"""
Unit tests for pysilicon/hw/aximm_queue.py.

Phase 1 coverage
----------------
AXIMMQueueLayout  — address math across mem_bw and elem_words, validation
MMMemory          — burst round-trip over a DirectMMIF
"""
from __future__ import annotations

import numpy as np
import numpy.testing as npt
import pytest

from pysilicon.hw.aximm_queue import AXIMMQueueLayout, MMMemory
from pysilicon.hw.clock import Clock
from pysilicon.hw.memif import DirectMMIF, MMIFMaster
from pysilicon.simulation.simulation import Simulation


# ---------------------------------------------------------------------------
# AXIMMQueueLayout
# ---------------------------------------------------------------------------

class TestAXIMMQueueLayout:
    def test_basic_math_32bit(self):
        lay = AXIMMQueueLayout(base_addr=0x1000, capacity=8, elem_words=1, mem_bw=32)
        assert lay.word_bytes == 4
        assert lay.control_bytes == 16          # 4 control words * 4 bytes
        assert lay.head_addr == 0x1000
        assert lay.tail_addr == 0x1004
        assert lay.capacity_addr == 0x1008
        assert lay.data_base == 0x1010
        assert lay.slot_addr(0) == 0x1010
        assert lay.slot_addr(7) == 0x1010 + 7 * 4
        # 16 control bytes + 8 slots * 1 word * 4 bytes
        assert lay.total_bytes == 16 + 8 * 4

    def test_mem_bw_64_doubles_control_and_stride(self):
        """Regression: a hard-coded 0x04 / CONTROL_BYTES=16 would fail this."""
        lay32 = AXIMMQueueLayout(base_addr=0, capacity=8, mem_bw=32)
        lay64 = AXIMMQueueLayout(base_addr=0, capacity=8, mem_bw=64)
        assert lay64.word_bytes == 8
        assert lay64.control_bytes == 32        # double the 32-bit case
        assert lay64.control_bytes == 2 * lay32.control_bytes
        # tail is one word past head — stride doubles with mem_bw
        assert lay32.tail_addr == 4
        assert lay64.tail_addr == 8
        # data base sits after the (larger) control region
        assert lay64.data_base == 32
        assert lay64.slot_addr(1) - lay64.slot_addr(0) == 8

    def test_elem_words_scales_slot_stride(self):
        lay = AXIMMQueueLayout(base_addr=0, capacity=4, elem_words=4, mem_bw=32)
        assert lay.slot_addr(0) == lay.data_base
        # each slot is 4 words * 4 bytes = 16 bytes
        assert lay.slot_addr(1) - lay.slot_addr(0) == 16
        assert lay.slot_addr(3) == lay.data_base + 3 * 16
        assert lay.total_bytes == lay.control_bytes + 4 * 4 * 4

    def test_elem_words_4_mem_bw_64(self):
        lay = AXIMMQueueLayout(base_addr=0x40, capacity=4, elem_words=4, mem_bw=64)
        assert lay.word_bytes == 8
        assert lay.control_bytes == 32
        assert lay.data_base == 0x40 + 32
        # slot stride = elem_words(4) * word_bytes(8) = 32
        assert lay.slot_addr(1) - lay.slot_addr(0) == 32
        assert lay.total_bytes == 32 + 4 * 4 * 8

    def test_validation(self):
        with pytest.raises(ValueError, match="capacity"):
            AXIMMQueueLayout(base_addr=0, capacity=1)
        with pytest.raises(ValueError, match="elem_words"):
            AXIMMQueueLayout(base_addr=0, capacity=4, elem_words=0)
        with pytest.raises(ValueError, match="mem_bw"):
            AXIMMQueueLayout(base_addr=0, capacity=4, mem_bw=128)


# ---------------------------------------------------------------------------
# MMMemory
# ---------------------------------------------------------------------------

class TestMMMemory:
    def _make(self, bitwidth=32):
        sim = Simulation()
        clk = Clock(freq=1.0)
        mem = MMMemory(sim=sim, bitwidth=bitwidth)
        master = MMIFMaster(sim=sim, bitwidth=bitwidth)
        direct = DirectMMIF(sim=sim, clk=clk)
        direct.bind("master", master)
        direct.bind("slave", mem.slave_ep)
        return sim, master, mem

    def test_burst_round_trip(self):
        sim, master, mem = self._make()
        data = np.arange(6, dtype=np.uint32) + 100
        result = []

        def proc():
            yield from master.write(data, 0x0)
            got = yield from master.read(6, 0x0)
            result.append(got)

        sim.env.process(proc())
        sim.env.run()
        npt.assert_array_equal(result[0], data)

    def test_byte_stride_keys(self):
        """Words are stored at byte addresses spaced by bitwidth // 8."""
        sim, master, mem = self._make(bitwidth=32)

        def proc():
            yield from master.write(np.array([7, 8, 9], dtype=np.uint32), 0x10)

        sim.env.process(proc())
        sim.env.run()
        assert mem._mem[0x10] == 7
        assert mem._mem[0x14] == 8
        assert mem._mem[0x18] == 9

    def test_unwritten_reads_zero(self):
        sim, master, mem = self._make()
        result = []

        def proc():
            got = yield from master.read(3, 0x200)
            result.append(got)

        sim.env.process(proc())
        sim.env.run()
        npt.assert_array_equal(result[0], [0, 0, 0])

    def test_64bit_stride(self):
        sim, master, mem = self._make(bitwidth=64)

        def proc():
            yield from master.write(np.array([1, 2], dtype=np.uint64), 0x0)

        sim.env.process(proc())
        sim.env.run()
        assert mem._mem[0x0] == 1
        assert mem._mem[0x8] == 2   # stride 8 bytes for 64-bit
