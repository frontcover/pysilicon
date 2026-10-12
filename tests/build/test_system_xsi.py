"""``waveflow/build/system_xsi.py`` -- the fast half (plans/host_runtime.md S5).

The RTL half is the example gates (test_mm_fir_xsi.py, test_markov_xsi.py), which run the
examples' system DAGs; ``run_system_xsi`` is a thin wrapper over the same steps.  Here: discovery from a bare system object, and the trace comparison.
"""
from __future__ import annotations

import numpy as np
import pytest

from waveflow.build.hwcodegen import LoweringError
from waveflow.build.system_xsi import compare_traces, discover
from waveflow.utils.burst_io import write_burst_bundle


def test_discover_finds_the_crossbar_the_host_and_the_cut():
    from examples.markov.markov_build import system
    from examples.mm_fir.mm_fir_build import system as fir_system

    s = system()
    xbar, host, inside = discover(s)
    assert xbar is s.xbar and host is s.host
    assert inside == [s.gen, s.chain, s.mem]            # device kernels, then the memory, in slot order
    f = fir_system("one_front")
    xbar, host, inside = discover(f)
    assert xbar is f.xbar and host is f.host and inside == [f.fir]


def test_discover_needs_a_bus_system():
    from examples.markov.markov import MarkovSystem, default_jobs
    with pytest.raises(LoweringError, match="one crossbar"):
        discover(MarkovSystem(jobs=default_jobs(1, 16), link="direct"))


def test_compare_traces_names_what_differs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        write_burst_bundle([np.arange(3, dtype=np.uint64)], root / "qin")
    write_burst_bundle([np.arange(2, dtype=np.uint64)], a / "qout")
    write_burst_bundle([np.arange(2, dtype=np.uint64) + 1], b / "qout")
    write_burst_bundle([np.arange(1, dtype=np.uint64)], b / "extra")
    assert compare_traces(a, a) == []
    assert compare_traces(a, b) == ["extra/words.bin", "extra/bounds.bin", "extra/meta.json",
                                    "qout/words.bin"]


# ---------------------------------------------------------------------------------------------
# The System contract (plans/system_model.md S0)
# ---------------------------------------------------------------------------------------------

def test_a_system_declares_its_cut():
    """Both systems state their default cut through dut(); discover() reads it, no longer derives it
    from the crossbar's slaves -- and it is the cut the derivation gave, so the top is unchanged."""
    from examples.markov.markov_build import system
    from examples.mm_fir.mm_fir_build import system as fir_system
    from waveflow.simulation.system import System

    s, f = system(), fir_system("per_view")
    assert isinstance(s, System) and isinstance(f, System)
    assert s.dut() == [s.gen, s.chain, s.mem] and f.dut() == [f.fir]
    assert s.xbar in s.elements() and s.host in s.elements()


def test_a_class_that_is_not_a_system_is_refused_by_name():
    class Loose:
        sim = None

    with pytest.raises(LoweringError, match=r"not a waveflow System.*dut\(\).*run\(\)"):
        discover(Loose())


def test_a_system_without_its_cut_cannot_be_built():
    from waveflow.simulation.system import System

    class NoCut(System):
        def run(self):
            return None

    with pytest.raises(TypeError, match="dut"):
        NoCut()
