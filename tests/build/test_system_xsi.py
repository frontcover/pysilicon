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
# The HwSystem contract (plans/system_model.md S0)
# ---------------------------------------------------------------------------------------------

def test_a_system_declares_its_cut():
    """Both systems state their DUT through dut() -- a tree whose children are the devices and the
    memory (system_model S1) -- and cut() flattens it to the kernels and memories discover() hands
    on: the cut the old derivation from the crossbar's slaves gave, so the top is unchanged."""
    from examples.markov.markov_build import system
    from examples.mm_fir.mm_fir_build import system as fir_system
    from waveflow.hw.hw_system import DutTop, HwSystem

    s, f = system(), fir_system("per_view")
    assert isinstance(s, HwSystem) and isinstance(f, HwSystem)
    assert isinstance(s.dut(), DutTop)
    assert list(s.dut().sub_comps.values()) == [s.gen_dev, s.chain_dev, s.mem]
    assert s.xbar in s.dut().interfaces.values()
    assert s.cut() == [s.gen, s.chain, s.mem] and f.cut() == [f.fir]
    assert s.xbar in s.elements() and s.host in s.elements()


def test_a_class_that_is_not_a_system_is_refused_by_name():
    class Loose:
        sim = None

    with pytest.raises(LoweringError, match=r"not a waveflow HwSystem.*dut\(\).*run\(\)"):
        discover(Loose())


def test_a_system_without_its_cut_cannot_be_built():
    from waveflow.hw.hw_system import HwSystem

    class NoCut(HwSystem):
        def run(self):
            return None

    with pytest.raises(TypeError, match="dut"):
        NoCut()


def test_the_host_declares_its_generated_header_and_the_harness_names_no_participant_type():
    """system_model S4: a SwHost's endpoints header is part of what its bfm_model() declares, so
    system_tb_spec treats every participant alike -- it imports no participant type."""
    import inspect

    from examples.markov.markov_build import system
    from waveflow.build import system_top

    s = system()
    (bm,) = [s.host.bfm_model()]
    ((fname, render),) = bm.generated_files
    assert fname == "MarkovHost_endpoints.h"
    assert "MarkovHost_endpoints" in render()                  # rendered on demand, from the wired host
    src = inspect.getsource(system_top.system_tb_spec)
    assert "SwHost" not in src and "isinstance(p" not in src


def test_the_bus_and_the_host_are_found_by_role():
    """system_model D1: the bus is the DUT tree's link that realizes as bus IP; the host is the
    bus master outside the DUT.  discover() names neither type."""
    import inspect

    from examples.markov.markov_build import system

    s = system()
    xbar, host, _cut = discover(s)
    assert xbar is s.xbar and host is s.host
    src = inspect.getsource(discover)
    assert "AXIMMCrossBarIF" not in src and "SwHost" not in src
    # Take the crossbar out of the DUT: the system no longer has a bus to build a top around.
    del s.dut().interfaces[s.xbar.name]
    with pytest.raises(LoweringError, match="one crossbar"):
        discover(s)
