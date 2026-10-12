"""hw_system.py — the :class:`HwSystem` base class: a design and its environment, as one object.

``plans/system_model.md`` S0.  Every system flow -- XSI simulation (``run_system_xsi``,
``add_system_steps``), the system top (``SystemTopSpec.from_system``), synthesis and the resource
estimate -- takes a system object.  This class states what such an object provides, so an author reads
the contract here instead of finding it in the consumers:

* ``sim`` -- the :class:`~waveflow.simulation.simulation.Simulation` holding every module and interface
  of the graph.  The subclass builds the graph (typically in ``__post_init__``) and sets it;
* :meth:`dut` -- the **default DUT**: a :class:`DutTop`, the root of the tree of modules synthesized
  into the design -- each device (a kernel with its views and the bus writers acting for it), each
  on-chip memory -- with the interfaces between them (the crossbar) as its internal interfaces.
  Everything else on the graph is the testbench.  A build may draw another cut (``inside=``); this is
  the one it takes otherwise.  DUT and testbench are roles a cut assigns, not kinds of module
  (``plans/design_cut.md``);
* :meth:`run` -- simulate the whole graph in pysim.

A system is not an :class:`~waveflow.hw.hw_module.HwModule`: it is never synthesized as a whole, and it
holds software (the host) and testbench models as well as hardware.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from waveflow.hw.hw_module import HwModule

if TYPE_CHECKING:
    from waveflow.simulation.simobj import SimObj
    from waveflow.simulation.simulation import Simulation


@dataclass
class DutTop(HwModule):
    """The root of a system's DUT: a composite whose children are its devices and on-chip memories,
    and whose internal interfaces are the links between them (the crossbar, a routed stream).

    It is structure, not behaviour: its children simulate, and it adds no process.  Build it from the
    parts::

        DutTop.of("markov_dut", sim, comps=[gen_dev, chain_dev, mem], ifs=[xbar, u_link])
    """

    @classmethod
    def of(cls, name: str, sim, *, comps=(), ifs=()) -> "DutTop":
        top = cls(name=name, sim=sim)
        for c in comps:
            top.add_comp(c)
        for i in ifs:
            top.add_if(i)
        return top


def dut_members(root: HwModule) -> list:
    """The cut a DUT tree stands for, as the list the system top is walked from: each device's
    kernel, and each other child (an on-chip memory) itself, in the tree's order."""
    from waveflow.hw.mm_device import MmSlaveDevice

    return [c.kernel if isinstance(c, MmSlaveDevice) else c for c in root.sub_comps.values()]


class HwSystem(ABC):
    """A design and its environment: the element graph, simulated by :meth:`run`, cut per build.

    A subclass builds the graph on ``self.sim`` and implements :meth:`dut` and :meth:`run`::

        @dataclass
        class MarkovSystem(HwSystem):
            jobs: list

            def __post_init__(self):
                self.sim = Simulation()
                ...                              # the kernels, memory, host, crossbar, wiring

            def dut(self):
                return self.dut_top                  # DutTop.of("markov_dut", ...), built above

            def run(self):
                self.sim.run_sim(until=self.host.done)
                return self.host.results
    """

    #: The simulation holding every module and interface of the graph; set by the subclass.
    sim: "Simulation"

    @abstractmethod
    def dut(self) -> HwModule:
        """The default DUT: the root of the synthesized tree (:class:`DutTop`), its children the
        devices and on-chip memories in the order their sub-tops are rendered."""

    @abstractmethod
    def run(self) -> Any:
        """Simulate the whole graph in pysim and return what the system reports."""

    def cut(self) -> list:
        """The default cut as a list -- each device's kernel and each memory (:func:`dut_members`):
        what ``inside=`` takes, and what a build uses when it is not given one."""
        return dut_members(self.dut())

    def elements(self) -> "list[SimObj]":
        """Every module and interface of the graph, in the order they were created."""
        return list(self.sim._sim_objs)


__all__ = ["DutTop", "HwSystem", "dut_members"]
