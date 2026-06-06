"""Fixed-point conformance harness — Python ``FixedField`` vs Vitis ``ap_fixed``.

The milestone proof for :class:`pysilicon.hw.fixpoint.FixedField`: for each curated
``(W, I, signed, Q, O)`` config, assign a sweep of exactly-representable doubles to
``ap_fixed<W,I,Q,O>`` in Vitis C-sim (decision 6: ``double -> ap_fixed -> .range()``
bits) and assert the emitted bits equal the bits the Python ``fixputils`` model
produces — **bit-for-bit, zero LSB disagreement**. If they ever differ the Python
model is wrong, not Vitis: fix ``fixputils``, never loosen the comparison.

Built on the shared ``BuildDag`` + :func:`run_dag_cli` pattern, and deliberately
factored so the next bit-exact type (``ComplexField`` = complex-of-``FixedField``)
reuses the same gen -> csim -> compare-bits rig with a different kernel template.

CLI::

    python fixedpoint_build.py --through gen_conformance   # write kernels + vectors (no Vitis)
    python fixedpoint_build.py --through run_conformance    # the full csim conformance (Vitis)
    python fixedpoint_build.py --list-steps
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pysilicon.build.build import BuildConfig, BuildDag, BuildStep, SourceStep
from pysilicon.build.cli import run_dag_cli
from pysilicon.hw.fixpoint import FixedField
from pysilicon.toolchain import toolchain
from pysilicon.utils import fixputils
from pysilicon.utils.fixputils import AP_RND, AP_SAT, AP_TRN, AP_WRAP

_SOURCE_DIR = Path(__file__).resolve().parent
_KERNEL_TEMPLATE = _SOURCE_DIR / "quantize_tb.cpp.in"


# --- curated config set (decision 5: a few widths x the v1 modes) -------------
@dataclass(frozen=True)
class FixedConfig:
    name: str
    W: int
    int_bits: int          # the ap_fixed I
    signed: bool = True
    q_mode: str = AP_TRN
    o_mode: str = AP_WRAP

    @property
    def cpp_type(self) -> str:
        """The exact C++ type FixedField emits — what the kernel quantizes through."""
        return FixedField.specialize(
            self.W, self.int_bits, signed=self.signed,
            q_mode=self.q_mode, o_mode=self.o_mode).cpp_type


def _modes(name, W, I, signed):  # noqa: E741 — one entry per (Q, O) for a width
    return [
        FixedConfig(f"{name}_trn_wrap", W, I, signed, AP_TRN, AP_WRAP),
        FixedConfig(f"{name}_rnd_wrap", W, I, signed, AP_RND, AP_WRAP),
        FixedConfig(f"{name}_trn_sat", W, I, signed, AP_TRN, AP_SAT),
        FixedConfig(f"{name}_rnd_sat", W, I, signed, AP_RND, AP_SAT),
    ]


CURATED_CONFIGS: list[FixedConfig] = [
    *_modes("s4_2", 4, 2, True),     # tiny signed, F=2
    *_modes("s8_4", 8, 4, True),     # mid signed, F=4
    *_modes("u8_4", 8, 4, False),    # unsigned, F=4 (negative-input edge)
    *_modes("s16_8", 16, 8, True),   # wider, F=8
    *_modes("s8_8", 8, 8, True),     # F=0 integer (overflow only)
    *_modes("s8_0", 8, 0, True),     # pure fractional [-0.5, 0.5)
]


def conformance_values(cfg: FixedConfig) -> list[float]:
    """Edge-value sweep: exact-representable, rounding midpoints, min/max overflow,
    negatives, unsigned-negative inputs — all exactly-representable doubles so
    quantization is the only lossy step (decision 6)."""
    W, I, signed = cfg.W, cfg.int_bits, cfg.signed  # noqa: E741
    lsb = 2.0 ** (-(W - I))
    if signed:
        max_repr = ((1 << (W - 1)) - 1) * lsb
        min_repr = -(1 << (W - 1)) * lsb
    else:
        max_repr = ((1 << W) - 1) * lsb
        min_repr = 0.0
    vals = [
        0.0, lsb, -lsb, 2 * lsb, -2 * lsb,
        0.25 * lsb, -0.25 * lsb, 0.5 * lsb, -0.5 * lsb, 0.75 * lsb, -0.75 * lsb,
        1.5 * lsb, -1.5 * lsb,
        max_repr, max_repr + 0.5 * lsb, max_repr + lsb, 8 * max_repr,
        min_repr, min_repr - 0.5 * lsb, min_repr - lsb, -8 * max_repr,
    ]
    if not signed:
        vals += [-0.5 * lsb, -lsb, -2.0]
    return vals


def expected_bits(cfg: FixedConfig, values: list[float]) -> list[int]:
    """The W-bit stored pattern the Python fixputils model produces (the golden)."""
    stored = fixputils.quantize(
        np.asarray(values, dtype=np.float64),
        cfg.W, cfg.int_bits, cfg.signed, cfg.q_mode, cfg.o_mode)
    bits = fixputils.to_bits(np.asarray(stored, dtype=np.int64), cfg.W)
    return [int(b) for b in np.atleast_1d(bits)]


def _render_kernel(cfg: FixedConfig) -> str:
    return (
        _KERNEL_TEMPLATE.read_text(encoding="utf-8")
        .replace("__FIXED_TYPE__", cfg.cpp_type)
        .replace("__W__", str(cfg.W))
    )


def gen_config_sources(cfg: FixedConfig, work_dir: Path) -> Path:
    """Write one config's kernel + input vector + Python expected bits + run.tcl."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    values = conformance_values(cfg)
    (work_dir / "quantize_tb.cpp").write_text(_render_kernel(cfg), encoding="utf-8")
    (work_dir / "in_values.txt").write_text(
        "\n".join(f"{v:.17g}" for v in values) + "\n", encoding="utf-8")
    (work_dir / "expected.json").write_text(json.dumps({
        "config": cfg.name, "cpp_type": cfg.cpp_type, "W": cfg.W,
        "signed": cfg.signed, "q_mode": cfg.q_mode, "o_mode": cfg.o_mode,
        "values": values, "expected_bits": expected_bits(cfg, values),
    }, indent=2), encoding="utf-8")
    shutil.copy(_SOURCE_DIR / "run.tcl", work_dir / "run.tcl")
    return work_dir


def csim_and_compare(cfg: FixedConfig, work_dir: Path, *, live_output: bool = False) -> dict:
    """Run Vitis csim for one config and compare the emitted bits to the golden.

    Returns a result dict; ``exact`` is True iff every value matched bit-for-bit."""
    work_dir = Path(work_dir)
    expected = json.loads((work_dir / "expected.json").read_text(encoding="utf-8"))
    toolchain.run_vitis_hls(work_dir / "run.tcl", work_dir=work_dir,
                            capture_output=not live_output)
    vitis_bits = [int(tok) for tok in
                  (work_dir / "out_bits.txt").read_text(encoding="utf-8").split()]
    exp_bits = expected["expected_bits"]
    values = expected["values"]
    mismatches = [
        {"value": v, "expected": e, "vitis": g}
        for v, e, g in zip(values, exp_bits, vitis_bits) if e != g
    ]
    count_ok = len(vitis_bits) == len(exp_bits)
    return {
        "config": cfg.name, "cpp_type": cfg.cpp_type, "n_values": len(values),
        "count_ok": count_ok, "mismatches": mismatches,
        "exact": count_ok and not mismatches,
    }


def conformance_for_config(cfg: FixedConfig, work_dir: Path, *, live_output: bool = False) -> dict:
    """Full single-config conformance: generate sources, csim, compare bits."""
    gen_config_sources(cfg, work_dir)
    return csim_and_compare(cfg, work_dir, live_output=live_output)


# --- BuildDag steps -----------------------------------------------------------
@dataclass(kw_only=True)
class GenConformanceSourcesStep(BuildStep):
    description = "Generate the per-config quantize kernel + value vectors + Python golden bits."
    consumes = ["fixedpoint_source", "run_tcl", "kernel_template"]
    produces = {"conformance_gen": Path("gen")}
    params = {}

    def run(self, config: BuildConfig, **_) -> dict:
        gen = config.root_dir / "gen"
        gen.mkdir(parents=True, exist_ok=True)
        for cfg in CURATED_CONFIGS:
            gen_config_sources(cfg, gen / cfg.name)
        return {"conformance_gen": gen}


@dataclass(kw_only=True)
class RunConformanceStep(BuildStep):
    description = "Per config: Vitis csim, assert Vitis bits == Python fixputils bits exactly."
    consumes = ["conformance_gen"]
    produces = {"conformance_report": Path("results/conformance_report.json")}
    params = {"live_output": False}

    def run(self, config: BuildConfig, live_output, **_) -> dict:
        gen = config.root_dir / "gen"
        results = [csim_and_compare(cfg, gen / cfg.name, live_output=live_output)
                   for cfg in CURATED_CONFIGS]
        report_path = config.root_dir / "results" / "conformance_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        n_exact = sum(r["exact"] for r in results)
        report = {
            "n_configs": len(results), "n_exact": n_exact,
            "all_exact": n_exact == len(results), "results": results,
        }
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        failed = [r for r in results if not r["exact"]]
        if failed:
            raise RuntimeError(
                "STOP — Vitis ap_fixed disagreed with the Python fixputils model "
                f"on {len(failed)}/{len(results)} configs. The Python model is wrong, "
                "not Vitis; fix fixputils, do not loosen the comparison. "
                f"First failure: {failed[0]}")
        return {"conformance_report": report_path}


def build_fixedpoint_dag() -> BuildDag:
    dag = BuildDag()
    dag.add(SourceStep(artifact="fixedpoint_source", path=_SOURCE_DIR / "fixedpoint_build.py"))
    dag.add(SourceStep(artifact="run_tcl", path=_SOURCE_DIR / "run.tcl"))
    dag.add(SourceStep(artifact="kernel_template", path=_KERNEL_TEMPLATE))
    dag.add(GenConformanceSourcesStep(name="gen_conformance"))
    dag.add(RunConformanceStep(name="run_conformance"))
    return dag


def main() -> None:
    run_dag_cli(
        build_fixedpoint_dag,
        description="Fixed-point (ap_fixed) Python-vs-Vitis bit-exact conformance harness.",
        default_through="gen_conformance",
        root_dir=_SOURCE_DIR,
        extra_args=[
            (("--live-output",), {"action": "store_true"}),
        ],
        params_from_args=lambda a: {"live_output": a.live_output},
    )


if __name__ == "__main__":
    main()
