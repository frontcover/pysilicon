"""Unified BuildDag for the shared_mem (histogram) example.

Replaces the imperative ``HistTest`` stage-runner (in ``hist_demo.py``) and the
``shared_mem_build.py`` grab-bag with one declarative :class:`BuildDag` of
dataclass :class:`BuildStep`s, driven by the shared regmap-style introspection
CLI (:func:`pysilicon.build.cli.run_dag_cli`).

This file is grown in phases (see plans/shared_mem_build_refactor.md). It
currently assembles the **non-Vitis front** — codegen → input vectors → Python
golden; the Vitis (csim/csynth/cosim/burst/timing) and figure stages land in
later phases. The codegen step *wraps* the proven ``generate_vitis_sources``
generation rather than reimplementing it, so the generated kernel — and the cosim
result it must reproduce — is unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from pysilicon.build.build import BuildConfig, BuildDag, BuildStep, SourceStep
from pysilicon.build.cli import run_dag_cli

try:
    from examples.shared_mem.hist import run_sim
    from examples.shared_mem.shared_mem_build import (
        HistCase, generate_vitis_sources,
    )
except ModuleNotFoundError:  # direct execution from the example dir
    from hist import run_sim  # type: ignore[no-redef]
    from shared_mem_build import (  # type: ignore[no-redef]
        HistCase, generate_vitis_sources,
    )

_SOURCE_DIR = Path(__file__).resolve().parent

# The default reference vector (the cosim/burst vector). The 4-case csim coverage
# sweep lives in CSIM_CASES and is driven by the csim step (added in a later phase).
DEFAULT_NDATA = 37
DEFAULT_NBINS = 6
DEFAULT_SEED = 3


@dataclass(kw_only=True)
class GenSourcesStep(BuildStep):
    """Generate the Vitis HLS support headers + the m_axi kernel and testbench.

    Wraps the proven ``generate_vitis_sources`` (``HistTest.gen_vitis_code`` for
    the schema/array-utils/stream/memmgr headers, then ``kernel_to_cpp`` /
    ``header_to_cpp`` / ``tb_files_to_str`` for ``gen/``) so the generated kernel
    is byte-for-byte what the cosim safety net validates."""

    description = "Generate include/ headers + gen/hist.{cpp,hpp} + gen/hist_tb.cpp."
    consumes = ["hist_source"]
    produces = {
        "include_dir": Path("include"),
        "kernel_cpp": Path("gen/hist.cpp"),
        "kernel_hpp": Path("gen/hist.hpp"),
        "tb_cpp": Path("gen/hist_tb.cpp"),
    }

    def run(self, config: BuildConfig, **_) -> dict[str, Any]:
        generate_vitis_sources(config.root_dir)
        gen = config.root_dir / "gen"
        return {
            "include_dir": config.root_dir / "include",
            "kernel_cpp": gen / "hist.cpp",
            "kernel_hpp": gen / "hist.hpp",
            "tb_cpp": gen / "hist_tb.cpp",
        }


@dataclass(kw_only=True)
class BuildInputsStep(BuildStep):
    """Write the C-sim input vectors for the reference case (cmd.bin + the data
    and edges buffers). The 4-case coverage sweep (CSIM_CASES) is driven by the
    csim step; this writes the reference vector the cosim/burst stages use."""

    description = "Write the reference-case C-sim inputs (cmd.bin + data/edges)."
    consumes = ["hist_source"]
    produces = {"data_dir": Path("data")}
    params = {"ndata": DEFAULT_NDATA, "nbins": DEFAULT_NBINS, "seed": DEFAULT_SEED}

    def run(self, config: BuildConfig, ndata, nbins, seed, **_) -> dict[str, Any]:
        data_dir = config.root_dir / "data"
        HistCase(ndata=ndata, nbins=nbins, seed=seed).write_inputs(data_dir)
        return {"data_dir": data_dir}


@dataclass(kw_only=True)
class PySimStep(BuildStep):
    """Run the SimPy model for the reference case and record golden parity.

    Drives ``run_sim`` (HistAccel + HistController + MemComponent) against the
    numpy golden and writes a summary — the functional reference the C-sim and
    cosim stages are checked against."""

    description = "Run the SimPy histogram model and record golden parity."
    consumes = ["hist_source"]
    produces = {"sim_summary": Path("results/sim_summary.json")}
    params = {"ndata": DEFAULT_NDATA, "nbins": DEFAULT_NBINS, "seed": DEFAULT_SEED}

    def run(self, config: BuildConfig, ndata, nbins, seed, **_) -> dict[str, Any]:
        data, edges = HistCase(ndata=ndata, nbins=nbins, seed=seed).gen_data()
        res = run_sim(data, edges, nbins=nbins, tx_id=seed)
        out = config.root_dir / "results" / "sim_summary.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "ndata": int(ndata), "nbins": int(nbins), "seed": int(seed),
            "status": res.status.name,
            "counts": np.asarray(res.counts).tolist(),
            "expected": np.asarray(res.expected).tolist(),
            "passed": bool(res.passed),
        }, indent=2), encoding="utf-8")
        if not res.passed:
            raise RuntimeError(
                f"SimPy golden parity failed: status={res.status.name}, "
                f"counts={res.counts.tolist()} != expected={res.expected.tolist()}"
            )
        return {"sim_summary": out}


def build_hist_dag() -> BuildDag:
    """Assemble the histogram BuildDag (non-Vitis front, for now)."""
    dag = BuildDag()
    dag.add(SourceStep(artifact="hist_source", path=_SOURCE_DIR / "hist.py"))
    dag.add(SourceStep(artifact="run_tcl", path=_SOURCE_DIR / "run.tcl"))
    dag.add(GenSourcesStep(name="gen_sources"))
    dag.add(BuildInputsStep(name="build_inputs"))
    dag.add(PySimStep(name="py_sim"))
    return dag


def main() -> None:
    run_dag_cli(
        build_hist_dag,
        description="Run the histogram (shared_mem) example.",
        default_through="py_sim",
        root_dir=_SOURCE_DIR,
        extra_args=[
            (("--ndata",), {"type": int, "default": DEFAULT_NDATA,
                            "help": "Number of data samples for the reference case."}),
            (("--nbins",), {"type": int, "default": DEFAULT_NBINS,
                            "help": "Number of histogram bins for the reference case."}),
            (("--seed",), {"type": int, "default": DEFAULT_SEED,
                           "help": "RNG seed / transaction id for the reference case."}),
            (("--trace-level",), {"default": "none", "choices": ["none", "port", "all"],
                                  "help": "RTL cosim VCD trace level (Vitis stages)."}),
            (("--live-output",), {"action": "store_true"}),
        ],
        params_from_args=lambda a: {
            "ndata": a.ndata, "nbins": a.nbins, "seed": a.seed,
            "trace_level": a.trace_level, "live_output": a.live_output,
        },
    )


if __name__ == "__main__":
    main()
