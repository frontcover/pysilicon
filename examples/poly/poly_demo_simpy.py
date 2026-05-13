from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import simpy

from pysilicon.hw.arrayutils import read_array, write_array
from pysilicon.hw.clock import Clock
from pysilicon.hw.component import Component
from pysilicon.hw.interface import StreamIF, StreamIFMaster, StreamIFSlave
from pysilicon.simulation.simulation import Simulation
from pysilicon.simulation.simobj import ProcessGen, SimObj

from poly_demo import (
    CoeffArray,
    Float32,
    PolyAccel,
    PolyCmdHdr,
    PolyRespFtr,
    PolyRespHdr,
)


# ---------------------------------------------------------------------------
# Accelerator
# ---------------------------------------------------------------------------

@dataclass
class PolyAccelComponent(Component):
    """
    SimPy model of the polynomial accelerator kernel.

    Matches the two-stream Vitis HLS interface exactly:
      in_stream  — burst 1: PolyCmdHdr words  |  burst 2: float32 samples
      out_stream — burst 1: PolyRespHdr words |  burst 2: float32 samples  |  burst 3: PolyRespFtr words
    """

    in_bw : int = 32
    """Bitwidth of the input stream."""

    out_bw: int = 32
    """Bitwidth of the output stream."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.s_in  = StreamIFSlave( name=f'{self.name}_s_in',  sim=self.sim, bitwidth=self.in_bw)
        self.m_out = StreamIFMaster(name=f'{self.name}_m_out', sim=self.sim, bitwidth=self.out_bw)
        self.add_endpoint(self.s_in)
        self.add_endpoint(self.m_out)
        self.status_reg: int = 0
        self._reset_event: simpy.Event = self.env.event()

    def reset(self) -> None:
        """Signal a hardware reset; unblocks run_proc if it is waiting on an error."""
        old, self._reset_event = self._reset_event, self.env.event()
        if not old.triggered:
            old.succeed()

    def run_proc(self) -> ProcessGen[None]:
        accel = PolyAccel()
        while True:
            cmd_nwords  = PolyCmdHdr.nwords_per_inst(self.in_bw)
            cmd_words   = yield from self.s_in.get(nwords_max=cmd_nwords)
            if cmd_words.shape[0] != cmd_nwords:
                self.status_reg = 0x1  # TLAST_EARLY on command burst
                yield self._reset_event
                self.status_reg = 0
                accel = PolyAccel()
                continue

            cmd_hdr     = PolyCmdHdr().deserialize(cmd_words, word_bw=self.in_bw)
            samp_nwords = int(cmd_hdr.nsamp) * Float32.nwords_per_inst(self.in_bw)
            samp_words  = yield from self.s_in.get(nwords_max=samp_nwords)
            if samp_words.shape[0] != samp_nwords:
                self.status_reg = 0x2  # TLAST_EARLY on sample burst
                yield self._reset_event
                self.status_reg = 0
                accel = PolyAccel()
                continue

            samp_in = read_array(samp_words, elem_type=Float32, word_bw=self.in_bw, shape=int(cmd_hdr.nsamp))
            resp_hdr, samp_out, resp_ftr = accel.evaluate(cmd_hdr, samp_in)

            yield from self.m_out.write(resp_hdr.serialize(word_bw=self.out_bw))
            yield from self.m_out.write(write_array(samp_out, elem_type=Float32, word_bw=self.out_bw))
            yield from self.m_out.write(resp_ftr.serialize(word_bw=self.out_bw))


# ---------------------------------------------------------------------------
# Testbench
# ---------------------------------------------------------------------------

@dataclass
class PolyTB(SimObj):
    """
    Drives one polynomial transaction and captures the response.
    Mirrors the two-stream interface of PolyAccelComponent.
    """

    cmd_hdr: PolyCmdHdr
    samp_in: npt.NDArray[np.float32]

    def __post_init__(self) -> None:
        super().__post_init__()
        self.m_in  = StreamIFMaster(name=f'{self.name}_m_in',  sim=self.sim, bitwidth=32)
        self.s_out = StreamIFSlave( name=f'{self.name}_s_out', sim=self.sim, bitwidth=32)
        self.resp_hdr: PolyRespHdr | None = None
        self.samp_out: npt.NDArray[np.float32] | None = None
        self.resp_ftr: PolyRespFtr | None = None

    def run_proc(self) -> ProcessGen[None]:
        yield from self.m_in.write(self.cmd_hdr.serialize(word_bw=32))
        yield from self.m_in.write(write_array(self.samp_in, elem_type=Float32, word_bw=32))

        resp_words = yield from self.s_out.get()
        samp_words = yield from self.s_out.get()
        ftr_words  = yield from self.s_out.get()

        self.resp_hdr = PolyRespHdr().deserialize(resp_words, word_bw=32)
        self.samp_out = read_array(samp_words, elem_type=Float32, word_bw=32, shape=int(self.cmd_hdr.nsamp))
        self.resp_ftr = PolyRespFtr().deserialize(ftr_words, word_bw=32)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def connect(sim: Simulation, tb: PolyTB, accel: PolyAccelComponent, clk: Clock) -> None:
    """Wire in_stream and out_stream between the testbench and the accelerator."""
    in_stream  = StreamIF(sim=sim, clk=clk)
    out_stream = StreamIF(sim=sim, clk=clk)

    in_stream.bind( "master", tb.m_in)
    in_stream.bind( "slave",  accel.s_in)
    out_stream.bind("master", accel.m_out)
    out_stream.bind("slave",  tb.s_out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    nsamp = 100

    coeffs = CoeffArray()
    coeffs.val = np.array([1.0, -2.0, -3.0, 4.0], dtype=np.float32)

    cmd_hdr = PolyCmdHdr()
    cmd_hdr.tx_id = 42
    cmd_hdr.coeffs = coeffs.val
    cmd_hdr.nsamp = nsamp

    samp_in = np.linspace(0.0, 1.0, nsamp, dtype=np.float32)

    sim = Simulation()
    clk = Clock(freq=1e9)

    accel = PolyAccelComponent(name='poly_accel', sim=sim)
    tb    = PolyTB(name='poly_tb', sim=sim, cmd_hdr=cmd_hdr, samp_in=samp_in)

    connect(sim, tb, accel, clk)
    sim.run_sim()

    print(f"tx_id={int(tb.resp_hdr.tx_id)}")
    print(f"nsamp_read={int(tb.resp_ftr.nsamp_read)}")
    print(f"error={tb.resp_ftr.error.name}")


if __name__ == "__main__":
    main()
