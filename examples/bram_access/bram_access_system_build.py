"""bram_access through the **system** flow: the kernel and its hand-written memory as one DUT.

``plans/system_model.md`` S3e.  The same design the kernel flow builds (``bram_access_build.py``),
taken through ``add_system_steps``: ``csynth`` -> ``system_rtl`` -> ``scenario`` / ``pysim`` ->
``system_xsi`` -> ``compare``.  The memory is ``rtl_module()`` RTL beside the kernel, so the system
top instantiates the DUT as its wrapper (``wrapper_gen``), rendered into the top's file, with
``bram_t2p.v`` on its compile list.  The three sinks' recordings are compared RTL against pysim; the
golden -- every response and read word, the last at cycle **568** -- is the kernel flow's own check
(``check_xsi_outputs``), run by :func:`check_xsi`.

    python -m examples.bram_access.bram_access_system_build --synth check     # the whole flow
"""
from __future__ import annotations

from pathlib import Path

from examples.bram_access.bram_access import BramAccessSystem, check_xsi_outputs, scenario_zero
from waveflow.build.build import BuildDag
from waveflow.build.system_dag import add_system_steps

HERE = Path(__file__).resolve().parent
TOP = "bram_access_system"
WORK_DIR = "xsi_work"
#: The kernel flow's gate (``test_bram_access_xsi.py``): the cycle the last read word lands.
WANT_CYCLES = 568


def system() -> BramAccessSystem:
    """Scenario zero, the kernel gate's."""
    return BramAccessSystem()


def build_dag(work_dir=WORK_DIR) -> BuildDag:
    """The framework's system steps for :func:`system`.  The kernel's csynth project is the kernel
    flow's (``bram_access.tcl`` at the root)."""
    dag = BuildDag()
    add_system_steps(dag, system(), work_dir=work_dir, top=TOP,
                     tcls={"bram_access": Path("bram_access.tcl")})
    return dag


def check_xsi(work: Path, want_cycles: int | None = WANT_CYCLES) -> None:
    """The kernel flow's golden check, on the RTL run's recordings."""
    check_xsi_outputs(Path(work) / "xsi_run", scenario_zero(), want_cycles)


if __name__ == "__main__":
    from waveflow.build.cli import run_dag_cli

    run_dag_cli(lambda a: build_dag(), description=__doc__.splitlines()[0],
                default_through="compare", root_dir=HERE,
                extra_args=[(("--synth",), dict(choices=("build", "check"), default="build",
                             help="build: csynth a stale top; check: fail on one instead"))],
                params_from_args=lambda a: {"synth": a.synth})
