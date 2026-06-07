"""Complex conformance harness -- Python ``ComplexField`` vs Vitis complex, bit-exact.

The Phase-4 milestone: for each curated case, run the complex op in Vitis C-sim (the
:mod:`kernels` complex kernels) and assert the emitted stored bits equal the bits the
Python ``DataArray[ComplexField]`` model produces -- **bit-for-bit, zero LSB
disagreement** -- per inner:

- **fixed** -> ``std::complex<ap_fixed>``  (the headline; signed cmult/cadd/csub/conj,
  unsigned cadd; round-trip quantization).
- **int**   -> the Waveflow ``wf_cint`` struct (s8 / s16: cmult/cadd/csub/conj + round-trip).
- **float** -> ``std::complex<float>`` / ``std::complex<double>``  (round-trip + the
  complex-multiply **edge** confirmed empirically + cadd/csub/conj).

If Python and Vitis ever differ the Python model is wrong, not Vitis: fix it, never loosen.
Built on the shared ``BuildDag`` + :func:`run_dag_cli` rig (reused from the FixedField
harness), so this is the same gen -> csim -> compare-bits flow.

CLI::

    python complex_build.py --through gen_conformance   # write kernels + vectors (no Vitis)
    python complex_build.py --through run_conformance    # the full csim conformance (Vitis)
    python complex_build.py --list-steps
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from waveflow.build.build import BuildConfig, BuildDag, BuildStep, SourceStep
from waveflow.build.cli import run_dag_cli
from waveflow.hw.complexfield import ComplexField, cadd, cmult, conj, csub
from waveflow.hw.dataschema import DataArray, FloatField, IntField
from waveflow.hw.fixpoint import FixedField
from waveflow.toolchain import toolchain
from waveflow.utils import complexutils as cx
from waveflow.utils.fixputils import OMode, QMode, quantize_real, to_bits

try:
    from examples.schemas.complex.kernels import (
        render_caddsub, render_cmult, render_conj, render_load_real,
    )
except ModuleNotFoundError:  # direct execution from the example dir
    from kernels import (  # type: ignore[no-redef]
        render_caddsub, render_cmult, render_conj, render_load_real,
    )

_SOURCE_DIR = Path(__file__).resolve().parent


# --- config -------------------------------------------------------------------
@dataclass(frozen=True)
class ComplexConfig:
    name: str
    kind: str                       # "fixed" | "int" | "float"
    W: int = 0
    int_bits: int = 0
    signed: bool = True
    q_mode: QMode = QMode.AP_TRN
    o_mode: OMode = OMode.AP_WRAP
    fbw: int = 0                    # float bitwidth (32/64)

    @property
    def inner_cls(self):
        if self.kind == "fixed":
            return FixedField.specialize(self.W, self.int_bits, self.signed, self.q_mode, self.o_mode)
        if self.kind == "int":
            return IntField.specialize(self.W, self.signed)
        return FloatField.specialize(self.fbw)

    @property
    def complex_cls(self):
        return ComplexField.specialize(self.inner_cls)

    @property
    def inner_W(self) -> int:
        return self.fbw if self.kind == "float" else self.W


# curated signed fixed configs (the FixedField set; 2W+1 <= 64 for all -> cmult ok)
FIXED_SIGNED = [
    ComplexConfig("s4_2", "fixed", 4, 2), ComplexConfig("s8_4", "fixed", 8, 4),
    ComplexConfig("s16_8", "fixed", 16, 8), ComplexConfig("s8_8", "fixed", 8, 8),
    ComplexConfig("s8_0", "fixed", 8, 0),
]
FIXED_UNSIGNED = ComplexConfig("u8_4", "fixed", 8, 4, signed=False)
INTS = [ComplexConfig("i8", "int", 8), ComplexConfig("i16", "int", 16)]
FLOATS = [ComplexConfig("f32", "float", fbw=32), ComplexConfig("f64", "float", fbw=64)]


# --- value helpers ------------------------------------------------------------
def _stored_codes(W: int, signed: bool, n: int = 6) -> np.ndarray:
    lo, hi = (-(1 << (W - 1)), (1 << (W - 1)) - 1) if signed else (0, (1 << W) - 1)
    return np.linspace(lo, hi, n).astype(np.int64)


def _da(cf, val) -> DataArray:
    arr = np.asarray(val)
    return DataArray.specialize(cf, max_shape=(arr.shape[0],))(arr)


def _fixed_int_operand(cfg: ComplexConfig, seed: int) -> DataArray:
    fmt = cfg.inner_cls.get_format() if cfg.kind == "fixed" else cx.int_format(cfg.W, cfg.signed)
    re = _stored_codes(cfg.W, cfg.signed)
    im = np.roll(_stored_codes(cfg.W, cfg.signed), seed)
    return _da(cfg.complex_cls, cx.make_complex(re, im, fmt))


def _float_operand(cfg: ComplexConfig, vals) -> DataArray:
    dt = np.complex128 if cfg.fbw == 64 else np.complex64
    return _da(cfg.complex_cls, np.asarray(vals, dtype=dt))


def _interleave_stored_bits(struct, W: int) -> list[int]:
    re = np.atleast_1d(to_bits(np.asarray(struct["re"]), W))
    im = np.atleast_1d(to_bits(np.asarray(struct["im"]), W))
    return [int(x) for pair in zip(re, im) for x in pair]


def _interleave_float_bits(arr, bw: int) -> list[int]:
    dt = np.uint32 if bw == 32 else np.uint64
    ft = np.float32 if bw == 32 else np.float64
    out: list[int] = []
    for z in np.atleast_1d(arr):
        out.append(int(np.asarray(ft(z.real)).view(dt)))
        out.append(int(np.asarray(ft(z.imag)).view(dt)))
    return out


def _text(ints) -> str:
    return "\n".join(str(int(x)) for x in ints) + "\n"


def _reals_text(pairs) -> str:
    """Interleaved re, im doubles (one per line)."""
    out: list[str] = []
    for re, im in pairs:
        out.append(f"{float(re):.17g}")
        out.append(f"{float(im):.17g}")
    return "\n".join(out) + "\n"


def _case(name, kernel, in_a, in_b, expected) -> dict:
    return {"name": name, "kernel": kernel, "in_a": in_a, "in_b": in_b, "expected": expected}


# --- per-op golden + kernel ---------------------------------------------------
def _arith_inputs(cfg, a, b):
    if cfg.kind == "float":
        return _text(_interleave_float_bits(a.val, cfg.fbw)), _text(_interleave_float_bits(b.val, cfg.fbw))
    return _text(_interleave_stored_bits(a.val, cfg.W)), _text(_interleave_stored_bits(b.val, cfg.W))


def _arith_expected(cfg, out):
    ri = out.element_type.inner_type
    if cfg.kind == "float":
        return _interleave_float_bits(out.val, ri.get_bitwidth()), ri
    return _interleave_stored_bits(out.val, ri.get_bitwidth()), ri


def _add_binary_case(cases, cfg, a, b, op_name, fn, render_fn):
    out = fn(a, b)
    exp, ri = _arith_expected(cfg, out)
    in_a, in_b = _arith_inputs(cfg, a, b)
    kernel = render_fn(cfg.inner_cls.cpp_type, cfg.inner_W, ri.cpp_type, ri.get_bitwidth())
    cases.append(_case(f"{op_name}_{cfg.name}", kernel, in_a, in_b, exp))


def build_cases() -> list[dict]:  # noqa: C901 — flat enumeration of curated cases
    cases: list[dict] = []
    ctype_of = lambda cfg: cfg.inner_cls.cpp_type  # noqa: E731

    # ---- fixed (signed): round-trip + cmult/cadd/csub/conj ----
    for cfg in FIXED_SIGNED:
        a = _fixed_int_operand(cfg, 2)
        b = _fixed_int_operand(cfg, 4)
        # round-trip: quantize reals (exact + rounding-midpoint + overflow sweep)
        pairs = _roundtrip_pairs(cfg)
        gold = []
        for re, im in pairs:
            gold.append(int(to_bits(quantize_real(np.array([re]), cfg.inner_cls.get_format()), cfg.W)[0]))
            gold.append(int(to_bits(quantize_real(np.array([im]), cfg.inner_cls.get_format()), cfg.W)[0]))
        cases.append(_case(f"roundtrip_{cfg.name}",
                           render_load_real("fixed", ctype_of(cfg), cfg.W),
                           _reals_text(pairs), "", gold))
        _add_binary_case(cases, cfg, a, b, "cmult", cmult,
                         lambda ct, w, rt, rw, c=cfg: render_cmult("fixed", c.inner_cls.cpp_type, c.W, rt, rw))
        _add_binary_case(cases, cfg, a, b, "cadd", cadd,
                         lambda ct, w, rt, rw, c=cfg: render_caddsub("+", "fixed", c.inner_cls.cpp_type, c.W, rt, rw))
        _add_binary_case(cases, cfg, a, b, "csub", csub,
                         lambda ct, w, rt, rw, c=cfg: render_caddsub("-", "fixed", c.inner_cls.cpp_type, c.W, rt, rw))
        oc = conj(a)
        ri = oc.element_type.inner_type
        cases.append(_case(f"conj_{cfg.name}",
                           render_conj("fixed", ctype_of(cfg), cfg.W, ri.cpp_type, ri.get_bitwidth()),
                           _text(_interleave_stored_bits(a.val, cfg.W)), "",
                           _interleave_stored_bits(oc.val, ri.get_bitwidth())))

    # ---- fixed (unsigned): round-trip + cadd only (cmult/conj are signed-only) ----
    cfg = FIXED_UNSIGNED
    a = _fixed_int_operand(cfg, 2)
    b = _fixed_int_operand(cfg, 4)
    pairs = _roundtrip_pairs(cfg)
    gold = []
    for re, im in pairs:
        gold.append(int(to_bits(quantize_real(np.array([re]), cfg.inner_cls.get_format()), cfg.W)[0]))
        gold.append(int(to_bits(quantize_real(np.array([im]), cfg.inner_cls.get_format()), cfg.W)[0]))
    cases.append(_case(f"roundtrip_{cfg.name}",
                       render_load_real("fixed", ctype_of(cfg), cfg.W), _reals_text(pairs), "", gold))
    _add_binary_case(cases, cfg, a, b, "cadd", cadd,
                     lambda ct, w, rt, rw, c=cfg: render_caddsub("+", "fixed", c.inner_cls.cpp_type, c.W, rt, rw))

    # ---- int (signed s8 / s16): round-trip + cmult/cadd/csub/conj ----
    for cfg in INTS:
        a = _fixed_int_operand(cfg, 2)
        b = _fixed_int_operand(cfg, 4)
        # round-trip is an identity over stored ints (validates the wf_cint layout)
        rt_bits = _interleave_stored_bits(a.val, cfg.W)
        cases.append(_case(f"roundtrip_{cfg.name}",
                           render_load_real("int", f"ap_int<{cfg.W}>", cfg.W),
                           _text(rt_bits), "", rt_bits))
        _add_binary_case(cases, cfg, a, b, "cmult", cmult,
                         lambda ct, w, rt, rw, c=cfg: render_cmult("int", f"ap_int<{c.W}>", c.W, rt, rw))
        _add_binary_case(cases, cfg, a, b, "cadd", cadd,
                         lambda ct, w, rt, rw, c=cfg: render_caddsub("+", "int", f"ap_int<{c.W}>", c.W, rt, rw))
        _add_binary_case(cases, cfg, a, b, "csub", csub,
                         lambda ct, w, rt, rw, c=cfg: render_caddsub("-", "int", f"ap_int<{c.W}>", c.W, rt, rw))
        oc = conj(a)
        ri = oc.element_type.inner_type
        cases.append(_case(f"conj_{cfg.name}",
                           render_conj("int", f"ap_int<{cfg.W}>", cfg.W,
                                       f"ap_int<{ri.get_bitwidth()}>", ri.get_bitwidth()),
                           _text(_interleave_stored_bits(a.val, cfg.W)), "",
                           _interleave_stored_bits(oc.val, ri.get_bitwidth())))

    # ---- float (f32 / f64): round-trip + cmult (the edge) + cadd/csub/conj ----
    fa_vals = [1 + 2j, -3 + 0.5j, 0.25 - 1j, 7.5 + 2.5j, -0.125 + 6j]
    fb_vals = [0.5 - 1j, 4 + 2j, -2 + 3j, 1 + 1j, 3.25 - 0.75j]
    for cfg in FLOATS:
        a = _float_operand(cfg, fa_vals)
        b = _float_operand(cfg, fb_vals)
        # round-trip: doubles -> float type (double->float quantization for f32; exact for f64)
        pairs = [(z.real, z.imag) for z in [complex(1.1, -2.2), complex(3.3, 0.4),
                                            complex(-5.5, 6.6), complex(0.0, -1.0)]]
        gold = _interleave_float_bits(
            np.asarray([complex(re, im) for re, im in pairs],
                       dtype=np.complex128 if cfg.fbw == 64 else np.complex64), cfg.fbw)
        cases.append(_case(f"roundtrip_{cfg.name}",
                           render_load_real("float", ctype_of(cfg), cfg.fbw), _reals_text(pairs), "", gold))
        _add_binary_case(cases, cfg, a, b, "cmult", cmult,
                         lambda ct, w, rt, rw, c=cfg: render_cmult("float", c.inner_cls.cpp_type, c.fbw, c.inner_cls.cpp_type, c.fbw))
        _add_binary_case(cases, cfg, a, b, "cadd", cadd,
                         lambda ct, w, rt, rw, c=cfg: render_caddsub("+", "float", c.inner_cls.cpp_type, c.fbw, c.inner_cls.cpp_type, c.fbw))
        _add_binary_case(cases, cfg, a, b, "csub", csub,
                         lambda ct, w, rt, rw, c=cfg: render_caddsub("-", "float", c.inner_cls.cpp_type, c.fbw, c.inner_cls.cpp_type, c.fbw))
        oc = conj(a)
        cases.append(_case(f"conj_{cfg.name}",
                           render_conj("float", ctype_of(cfg), cfg.fbw, ctype_of(cfg), cfg.fbw),
                           _text(_interleave_float_bits(a.val, cfg.fbw)), "",
                           _interleave_float_bits(oc.val, cfg.fbw)))

    return cases


def _roundtrip_pairs(cfg: ComplexConfig):
    """(re, im) real pairs for the quantization round-trip: exact, rounding midpoints, overflow."""
    lsb = 2.0 ** (-(cfg.W - cfg.int_bits))
    if cfg.signed:
        hi, lo = ((1 << (cfg.W - 1)) - 1) * lsb, -(1 << (cfg.W - 1)) * lsb
    else:
        hi, lo = ((1 << cfg.W) - 1) * lsb, 0.0
    vals = [0.0, lsb, -lsb, 0.5 * lsb, -0.5 * lsb, 1.5 * lsb, hi, hi + 0.5 * lsb, lo, lo - lsb]
    return [(vals[i], vals[-1 - i]) for i in range(len(vals))]


# --- single-case driver (reused by the step loop AND the conformance test) ----
def gen_case_sources(case: dict, work_dir: Path) -> Path:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "kernel.cpp").write_text(case["kernel"], encoding="utf-8")
    (work_dir / "in_a.txt").write_text(case["in_a"], encoding="utf-8")
    (work_dir / "in_b.txt").write_text(case["in_b"], encoding="utf-8")
    (work_dir / "expected.json").write_text(
        json.dumps({"name": case["name"], "expected": case["expected"]}, indent=2),
        encoding="utf-8")
    shutil.copy(_SOURCE_DIR / "run.tcl", work_dir / "run.tcl")
    return work_dir


def csim_and_compare(work_dir: Path, *, live_output: bool = False) -> dict:
    work_dir = Path(work_dir)
    expected = json.loads((work_dir / "expected.json").read_text(encoding="utf-8"))
    toolchain.run_vitis_hls(work_dir / "run.tcl", work_dir=work_dir, capture_output=not live_output)
    vitis = [int(tok) for tok in (work_dir / "out_bits.txt").read_text(encoding="utf-8").split()]
    exp = expected["expected"]
    mism = [{"i": i, "expected": e, "vitis": g}
            for i, (e, g) in enumerate(zip(exp, vitis)) if e != g]
    return {"name": expected["name"], "n": len(exp),
            "count_ok": len(vitis) == len(exp), "mismatches": mism,
            "exact": len(vitis) == len(exp) and not mism}


def conformance_for_case(case: dict, work_dir: Path, *, live_output: bool = False) -> dict:
    gen_case_sources(case, work_dir)
    return csim_and_compare(work_dir, live_output=live_output)


# --- BuildDag steps -----------------------------------------------------------
@dataclass(kw_only=True)
class GenConformanceStep(BuildStep):
    description = "Generate the per-case complex kernels + interleaved-I/Q vectors + Python golden bits."
    consumes = ["complex_source", "run_tcl", "kernels_source"]
    produces = {"conformance_gen": Path("gen")}
    params = {}

    def run(self, config: BuildConfig, **_) -> dict:
        gen = config.root_dir / "gen"
        gen.mkdir(parents=True, exist_ok=True)
        for case in build_cases():
            gen_case_sources(case, gen / case["name"])
        return {"conformance_gen": gen}


@dataclass(kw_only=True)
class RunConformanceStep(BuildStep):
    description = "Per case: Vitis csim, assert Vitis bits == Python bits exactly (round-trip + arithmetic)."
    consumes = ["conformance_gen"]
    produces = {"conformance_report": Path("results/conformance_report.json")}
    params = {"live_output": False}

    def run(self, config: BuildConfig, live_output, **_) -> dict:
        gen = config.root_dir / "gen"
        results = [csim_and_compare(gen / case["name"], live_output=live_output)
                   for case in build_cases()]
        report_path = config.root_dir / "results" / "conformance_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        n_exact = sum(r["exact"] for r in results)
        report_path.write_text(json.dumps(
            {"n_cases": len(results), "n_exact": n_exact,
             "all_exact": n_exact == len(results), "results": results}, indent=2),
            encoding="utf-8")
        failed = [r for r in results if not r["exact"]]
        if failed:
            raise RuntimeError(
                f"STOP — Vitis disagreed with the Python model on {len(failed)}/{len(results)} "
                "cases. The Python model is wrong, not Vitis; fix it, do not loosen the "
                f"comparison. First failure: {failed[0]}")
        return {"conformance_report": report_path}


def build_complex_dag() -> BuildDag:
    dag = BuildDag()
    dag.add(SourceStep(artifact="complex_source", path=_SOURCE_DIR / "complex_build.py"))
    dag.add(SourceStep(artifact="kernels_source", path=_SOURCE_DIR / "kernels.py"))
    dag.add(SourceStep(artifact="run_tcl", path=_SOURCE_DIR / "run.tcl"))
    dag.add(GenConformanceStep(name="gen_conformance"))
    dag.add(RunConformanceStep(name="run_conformance"))
    return dag


def main() -> None:
    run_dag_cli(
        build_complex_dag,
        description="Complex (ComplexField) Python-vs-Vitis bit-exact conformance (round-trip + arithmetic).",
        default_through="gen_conformance",
        root_dir=_SOURCE_DIR,
        extra_args=[(("--live-output",), {"action": "store_true"})],
        params_from_args=lambda a: {"live_output": a.live_output},
    )


if __name__ == "__main__":
    main()
