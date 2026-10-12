"""rf_loopback through the **system** flow: the RFDC in the testbench, the pass-through as the DUT.

``plans/system_model.md`` S3d.  The same design the single-kernel flow gates
(``tests/examples/test_rf_loopback_xsi.py``), taken through ``add_system_steps``: the DUT is the
pass-through kernel alone, and the converter is a testbench participant whose two models each span
the cut -- a stream port of the top on the fabric side, an RF behavioral edge to the data source or
sink on the other.  The system harness is the kernel flow's, model for model.

The sink's recording is checked by the example (:func:`check_xsi`), not compared against pysim:
the two backends legitimately disagree about sub-block loss.

    python -m examples.rf_loopback.rf_loopback_system_build --synth check
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from examples.rf_loopback.rf_loopback import RfLoopbackSystem
from examples.rf_loopback.rf_loopback_xsi import XSI_BLKSIZE, XSI_N_CYCLES, XSI_NBLK
from waveflow.build.build import BuildDag
from waveflow.build.system_dag import add_system_steps

HERE = Path(__file__).resolve().parent
TOP = "rf_loopback_system"
WORK_DIR = "xsi_work"
#: The single-kernel gate's numbers (``test_rf_loopback_xsi.py``): grid periods that carry data, and
#: blocks the sink collects in the run's cycle budget.
WANT_DATA_BLOCKS_OUT = 7
WANT_SINK_BLOCKS = 63


def system() -> RfLoopbackSystem:
    """The gate scenario: eight 256-sample blocks, the kernel flow's cycle budget."""
    return RfLoopbackSystem(n_src_blk=XSI_NBLK, blksize=XSI_BLKSIZE, n_cycles=XSI_N_CYCLES)


def build_dag(work_dir=WORK_DIR) -> BuildDag:
    """The framework's system steps; the kernel's csynth project is ``rf_pass_through.tcl``'s."""
    dag = BuildDag()
    add_system_steps(dag, system(), work_dir=work_dir, top=TOP,
                     tcls={"rf_pass_through": Path("rf_pass_through.tcl")})
    return dag


#: Grid periods of zero-fill before the first data block at RTL (``test_rf_loopback_xsi.py``).
RTL_STARTUP_BLOCKS = 1


def check_xsi(work: Path) -> dict:
    """The example's check on the RTL run's sink recording -- the bundle half of the kernel flow's
    gate, claim for claim: the startup transient is zero-fill, the **first** data block is bit-exact,
    and the counts of data-carrying periods and of collected blocks are the kernel gate's.  Later
    blocks are not compared whole: this read-then-write relay drops ADC words structurally (the kernel
    gate's ``WANT_ADC_DROPPED_IS_STRUCTURAL``), so they land with gaps.  Returns the counts."""
    from waveflow.simulation.rf_tb import read_rf_bundle

    sysm = system()
    sysm.write_scenario(Path(work) / "_golden")              # the sent blocks, from the same writer
    got = read_rf_bundle(Path(work) / "xsi_run" / "vectors" / "rf_out", 1, XSI_BLKSIZE)
    data = [b for b in got if np.any(b)]
    sent = sysm.runner.sent
    assert len(got) == WANT_SINK_BLOCKS, f"the sink collected {len(got)} blocks"
    for k in range(RTL_STARTUP_BLOCKS):
        assert not np.any(got[k]), f"block {k} is inside the startup transient: zero-fill"
    assert len(data) == WANT_DATA_BLOCKS_OUT, f"{len(data)} grid periods carried data"
    assert np.array_equal(data[0], sent[0]), "the first data block is not the first sent block"
    return {"sink_blocks": len(got), "data_blocks": len(data)}


if __name__ == "__main__":
    from waveflow.build.cli import run_dag_cli

    run_dag_cli(lambda a: build_dag(), description=__doc__.splitlines()[0],
                default_through="compare", root_dir=HERE,
                extra_args=[(("--synth",), dict(choices=("build", "check"), default="build",
                             help="build: csynth a stale top; check: fail on one instead"))],
                params_from_args=lambda a: {"synth": a.synth})
