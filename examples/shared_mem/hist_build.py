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

from pysilicon.toolchain import toolchain

try:
    from examples.shared_mem.hist import run_sim
    from examples.shared_mem.hist_demo import HistTest
    from examples.shared_mem.shared_mem_build import (
        CSIM_CASES, HistCase, generate_vitis_sources,
    )
    from examples.shared_mem.shared_mem_figures import (
        GenerateBurstDiagramStep, GenerateTimingDiagramStep, SyncDocsFiguresStep,
    )
except ModuleNotFoundError:  # direct execution from the example dir
    from hist import run_sim  # type: ignore[no-redef]
    from hist_demo import HistTest  # type: ignore[no-redef]
    from shared_mem_build import (  # type: ignore[no-redef]
        CSIM_CASES, HistCase, generate_vitis_sources,
    )
    from shared_mem_figures import (  # type: ignore[no-redef]
        GenerateBurstDiagramStep, GenerateTimingDiagramStep, SyncDocsFiguresStep,
    )

_SOURCE_DIR = Path(__file__).resolve().parent


def _run_tcl(config: BuildConfig, *, start_at: str, through: str,
             trace_level: str, live_output: bool) -> None:
    """Drive run.tcl over a stage range, matching HistTest.test_vitis's env."""
    toolchain.run_vitis_hls(
        config.root_dir / "run.tcl", work_dir=config.root_dir,
        capture_output=not live_output,
        env={
            "PYSILICON_HIST_START_AT": start_at,
            "PYSILICON_HIST_THROUGH": through,
            "PYSILICON_HIST_TRACE_LEVEL": trace_level,
        },
    )


def _vcd_trace(trace_level: str) -> str:
    return trace_level if trace_level in ("port", "all") else "*"

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


@dataclass(kw_only=True)
class CsimStep(BuildStep):
    """Vitis C-simulation across the 4-case coverage set, each checked against the
    numpy golden (nbins==1, two normal cases, and a validation-failure case — see
    CSIM_CASES). Restores the reference vector afterwards for the cosim stage."""

    description = "Vitis C-sim across the CSIM_CASES coverage set (vs the numpy golden)."
    consumes = ["kernel_cpp", "tb_cpp", "include_dir", "run_tcl"]
    produces = {"csim_verdict": Path("results/csim_verdict.json")}
    params = {"ndata": DEFAULT_NDATA, "nbins": DEFAULT_NBINS, "seed": DEFAULT_SEED,
              "live_output": False}

    def run(self, config: BuildConfig, ndata, nbins, seed, live_output, **_) -> dict[str, Any]:
        data_dir = config.root_dir / "data"
        cases = []
        for case in CSIM_CASES:
            data, edges = case.write_inputs(data_dir)
            _run_tcl(config, start_at="csim", through="csim",
                     trace_level="none", live_output=live_output)
            ok, detail = case.check_outputs(data_dir, data, edges)
            cases.append({"ndata": case.ndata, "nbins": case.nbins,
                          "passed": ok, "detail": detail})
            if not ok:
                raise RuntimeError(
                    f"C-sim mismatch ndata={case.ndata} nbins={case.nbins}: {detail}")
        # Leave the reference vector in data/ for the cosim stage.
        HistCase(ndata=ndata, nbins=nbins, seed=seed).write_inputs(data_dir)
        out = config.root_dir / "results" / "csim_verdict.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"cases": cases, "passed": True}, indent=2),
                       encoding="utf-8")
        return {"csim_verdict": out}


@dataclass(kw_only=True)
class CosimStep(BuildStep):
    """C-synth + RTL co-simulation of the reference vector (one run.tcl invocation,
    START_AT=csim THROUGH=cosim — the proven test_hist_cosim flow), checked against
    the golden. Gated on csim passing."""

    description = "Vitis C-synth + RTL co-simulation of the reference vector."
    consumes = ["kernel_cpp", "tb_cpp", "include_dir", "run_tcl", "csim_verdict"]
    produces = {"cosim_dir": Path("pysilicon_hist_proj")}
    params = {"ndata": DEFAULT_NDATA, "nbins": DEFAULT_NBINS, "seed": DEFAULT_SEED,
              "trace_level": "port", "live_output": False}

    def run(self, config: BuildConfig, ndata, nbins, seed, trace_level,
            live_output, **_) -> dict[str, Any]:
        data_dir = config.root_dir / "data"
        case = HistCase(ndata=ndata, nbins=nbins, seed=seed)
        data, edges = case.write_inputs(data_dir)
        _run_tcl(config, start_at="csim", through="cosim",
                 trace_level=_vcd_trace(trace_level), live_output=live_output)
        ok, detail = case.check_outputs(data_dir, data, edges)
        if not ok:
            raise RuntimeError(f"Cosim output mismatch (reference vector): {detail}")
        return {"cosim_dir": config.root_dir / "pysilicon_hist_proj"}


@dataclass(kw_only=True)
class GenerateVcdStep(BuildStep):
    """Re-run the synthesized RTL to write the port-level VCD (Vivado/xsim)."""

    description = "Re-run the RTL sim to write the port-level VCD."
    consumes = ["cosim_dir"]
    produces = {"vcd": Path("vcd/dump.vcd")}
    params = {"ndata": DEFAULT_NDATA, "nbins": DEFAULT_NBINS, "trace_level": "port"}

    def run(self, config: BuildConfig, ndata, nbins, trace_level, **_) -> dict[str, Any]:
        ht = HistTest(example_dir=config.root_dir, ndata=ndata, nbins=nbins)
        vcd = ht.generate_vcd(trace_level=_vcd_trace(trace_level))
        return {"vcd": Path(vcd)}


@dataclass(kw_only=True)
class ExtractBurstsStep(BuildStep):
    """Extract the multi-buffer AXI-MM burst report from the VCD and validate the
    layout against the expected allocation (data + bin_edges reads, counts write)."""

    description = "Extract + validate the multi-buffer AXI-MM burst report."
    consumes = ["vcd"]
    produces = {"burst_info": Path("vcd/burst_info.json")}
    params = {"ndata": DEFAULT_NDATA, "nbins": DEFAULT_NBINS}

    def run(self, config: BuildConfig, ndata, nbins, vcd, **_) -> dict[str, Any]:
        ht = HistTest(example_dir=config.root_dir, ndata=ndata, nbins=nbins)
        ht.simulate()
        report = ht.extract_bursts(vcd_path=vcd)
        if not report.get("validated"):
            raise RuntimeError("AXI-MM burst layout did not validate against the golden.")
        return {"burst_info": config.root_dir / "vcd" / "burst_info.json"}


@dataclass(kw_only=True)
class ExtractCosimTimingStep(BuildStep):
    """Extract the measured per-transaction cycle latency from the cosim report."""

    description = "Extract the measured cosim transaction latency."
    consumes = ["cosim_dir"]
    produces = {"cosim_timing": Path("results/cosim_timing.json")}

    def run(self, config: BuildConfig, **_) -> dict[str, Any]:
        from pysilicon.utils.cosimparse import CosimReportParser
        sol = config.root_dir / "pysilicon_hist_proj" / "solution1"
        cycles = CosimReportParser(sol_path=sol, top="hist").get_transaction_cycles()
        out = config.root_dir / "results" / "cosim_timing.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"transaction_cycles": cycles}, indent=2),
                       encoding="utf-8")
        return {"cosim_timing": out}


def build_hist_dag() -> BuildDag:
    """Assemble the unified histogram BuildDag."""
    dag = BuildDag()
    dag.add(SourceStep(artifact="hist_source", path=_SOURCE_DIR / "hist.py"))
    dag.add(SourceStep(artifact="run_tcl", path=_SOURCE_DIR / "run.tcl"))
    dag.add(GenSourcesStep(name="gen_sources"))
    dag.add(BuildInputsStep(name="build_inputs"))
    dag.add(PySimStep(name="py_sim"))
    dag.add(CsimStep(name="csim"))
    dag.add(CosimStep(name="cosim"))
    dag.add(GenerateVcdStep(name="generate_vcd"))
    dag.add(ExtractBurstsStep(name="extract_bursts"))
    dag.add(ExtractCosimTimingStep(name="extract_cosim_timing"))
    # Figure steps — an independent branch: they render from vcd/burst_info.json,
    # regenerated from the committed vcd/dump.vcd (ensure_burst_info), so a docs
    # refresh (`--through sync_docs_figures`) needs no Vitis even though the full
    # Vitis pipeline lives in the same DAG.
    dag.add(GenerateBurstDiagramStep(name="generate_burst_diagram"))
    dag.add(GenerateTimingDiagramStep(name="generate_timing_diagram"))
    dag.add(SyncDocsFiguresStep(name="sync_docs_figures"))
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
