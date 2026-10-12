"""system.py — the :class:`System` base class: a design and its environment, as one object.

``plans/system_model.md`` S0.  Every system flow -- XSI simulation (``run_system_xsi``,
``add_system_steps``), the system top (``SystemTopSpec.from_system``), synthesis and the resource
estimate -- takes a system object.  This class states what such an object provides, so an author reads
the contract here instead of finding it in the consumers:

* ``sim`` -- the :class:`~waveflow.simulation.simulation.Simulation` holding every module and interface
  of the graph.  The subclass builds the graph (typically in ``__post_init__``) and sets it;
* :meth:`dut` -- the **default cut**: the modules synthesized into the DUT.  Everything else on the
  graph is the testbench.  A build may draw another cut (``inside=``); this is the one it takes
  otherwise.  DUT and testbench are roles a cut assigns, not kinds of module
  (``plans/design_cut.md``);
* :meth:`run` -- simulate the whole graph in pysim.

A system is not an :class:`~waveflow.hw.hw_module.HwModule`: it is never synthesized as a whole, and it
holds software (the host) and testbench models as well as hardware.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waveflow.simulation.simobj import SimObj
    from waveflow.simulation.simulation import Simulation


class System(ABC):
    """A design and its environment: the element graph, simulated by :meth:`run`, cut per build.

    A subclass builds the graph on ``self.sim`` and implements :meth:`dut` and :meth:`run`::

        @dataclass
        class MarkovSystem(System):
            jobs: list

            def __post_init__(self):
                self.sim = Simulation()
                ...                              # the kernels, memory, host, crossbar, wiring

            def dut(self):
                return [self.gen, self.chain, self.mem]

            def run(self):
                self.sim.run_sim(until=self.host.done)
                return self.host.results
    """

    #: The simulation holding every module and interface of the graph; set by the subclass.
    sim: "Simulation"

    @abstractmethod
    def dut(self) -> list:
        """The default cut: the modules synthesized into the DUT -- kernels and on-chip memories --
        in the order their sub-tops are rendered.  Each kernel brings its device (its views, adaptor
        and the bus writers acting for it) with it."""

    @abstractmethod
    def run(self) -> Any:
        """Simulate the whole graph in pysim and return what the system reports."""

    def elements(self) -> "list[SimObj]":
        """Every module and interface of the graph, in the order they were created."""
        return list(self.sim._sim_objs)


__all__ = ["System"]
