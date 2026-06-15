"""Phase 1b range-method conformance — Python golden vs Vitis csim, bit-exact.

Exercises the new element-indexed range methods (``read_array_slice`` / ``write_array_slice``,
built on the Phase 1a lane methods) through real Vitis csim. Per case the C++ testbench runs three
checks against the Python golden (``arrayutils.write_array``):

- **(A) static whole-array overloads** — read ``[0, N)`` then re-pack must reproduce the words.
- **(B) ranged read** ``[i0, i1)`` — read the sub-range, re-pack contiguously, compare to
  ``write_array(data[i0:i1])``.
- **(C) ranged write RMW** ``[i0, i1)`` — splice a replacement sub-array into the middle of a copy
  of the full golden and compare to ``write_array(data with [i0:i1] replaced)``. The load-bearing
  check: neighbor elements sharing the boundary words must be preserved (not clobbered).

Cases cover: whole array; aligned and unaligned ``[i0, i1)``; both regimes including ``pf == 0``
(wide int + wide complex); a non-power-of-two ``pf`` with intra-word padding; and the unaligned-end
RMW. Reuses the Phase 1a / existing arrayutils Vitis harness pattern (template cpp + tcl + csim).
"""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from waveflow.build.build import BuildConfig
from waveflow.build.streamutils import StreamUtilsStep
from waveflow.hw.arrayutils import gen_array_utils, write_array
from waveflow.hw.complexfield import ComplexField
from waveflow.hw.dataschema import IntField
from waveflow.toolchain import toolchain
from waveflow.utils import complexutils as cx

TEST_DIR = Path(__file__).parent
RESOURCE_DIR = TEST_DIR / "resources"
SLICE_CPP_PATH = RESOURCE_DIR / "arrayutils_slice_test.cpp"
SLICE_TCL_PATH = RESOURCE_DIR / "arrayutils_slice_run.tcl"
WF_CINT_PATH = Path(__file__).resolve().parents[2] / "waveflow" / "build" / "wf_cint.h"


def _sint(bw: int):
    return IntField.specialize(bitwidth=bw, signed=True, include_dir="include")


def _uint(bw: int):
    return IntField.specialize(bitwidth=bw, signed=False, include_dir="include")


def _cint(bw: int):
    return ComplexField.specialize(_sint(bw), include_dir="include")


def _spaced(lo: int, hi: int, n: int) -> np.ndarray:
    # Evenly spaced integers in [lo, hi] via integer math (no float overflow at bw = 64).
    if n == 1:
        return np.array([hi], dtype=np.int64)
    step = (hi - lo) // (n - 1)
    vals = [lo + step * i for i in range(n)]
    vals[0], vals[-1] = lo, hi
    return np.array(vals, dtype=np.int64)


def _data(kind: str, bw: int, n: int) -> np.ndarray:
    if kind == "sint":
        return _spaced(-(1 << (bw - 1)), (1 << (bw - 1)) - 1, n)
    if kind == "uint":
        return _spaced(0, (1 << bw) - 1, n)
    re = _spaced(-(1 << (bw - 1)), (1 << (bw - 1)) - 1, n)
    im = np.roll(re, 1)
    return cx.make_complex(re, im, cx.int_format(bw, True))


def _complement(seg: np.ndarray, kind: str, bw: int) -> np.ndarray:
    # A distinct, in-range replacement: bitwise complement maps the range onto itself bijectively
    # and never equals the original (so a missed write or a clobbered neighbor is detectable).
    if kind == "sint":
        return (-1 - np.asarray(seg)).astype(np.int64)
    if kind == "uint":
        return (((1 << bw) - 1) - np.asarray(seg)).astype(np.int64)
    re = -1 - cx.re_of(seg)
    im = -1 - cx.im_of(seg)
    return cx.make_complex(re, im, cx.int_format(bw, True))


# (id, kind, bw, word_bw, N, i0, i1)
def _cases():
    return [
        ("s16_w32_whole", "sint", 16, 32, 8, 0, 8),        # pf=2, whole array
        ("s16_w32_aligned", "sint", 16, 32, 10, 2, 8),     # pf=2, aligned [2,8)
        ("s16_w32_unaligned", "sint", 16, 32, 10, 3, 7),   # pf=2, both ends unaligned (RMW x2)
        ("s16_w64_unaligned", "sint", 16, 64, 13, 5, 11),  # pf=4, unaligned
        ("s32_w32_mid", "sint", 32, 32, 6, 2, 5),          # pf=1
        ("u10_w32_unaligned", "uint", 10, 32, 11, 2, 9),   # pf=3 (non-power-of-two), padding, RMW
        ("s64_w32_mid", "sint", 64, 32, 5, 1, 4),          # pf=0 wide int
        ("cint32_w32_mid", "complex", 32, 32, 5, 1, 4),    # pf=0 wide complex
    ]


def _run_vitis_tcl(tcl_path: Path, work_dir: Path, failure_prefix: str) -> None:
    try:
        toolchain.run_vitis_hls(tcl_path, work_dir=work_dir)
    except RuntimeError as exc:
        pytest.skip(f"Vitis execution unavailable in current setup: {exc}")
    except subprocess.CalledProcessError as exc:
        pytest.fail(
            f"{failure_prefix}\n"
            f"Command: {exc.cmd}\n"
            f"Return code: {exc.returncode}\n"
            f"Stdout:\n{exc.stdout}\n"
            f"Stderr:\n{exc.stderr}"
        )


@pytest.mark.vitis
@pytest.mark.parametrize("case", _cases(), ids=[c[0] for c in _cases()])
def test_arrayutils_slice_vitis(tmp_path: Path, case):
    name, kind, bw, word_bw, n, i0, i1 = case

    vitis_path = toolchain.find_vitis_path()
    if not vitis_path:
        pytest.skip("Vitis installation not found; skipping arrayutils slice integration test.")

    elem_type = {"sint": _sint, "uint": _uint, "complex": _cint}[kind](bw)
    data = _data(kind, bw, n)
    repl = _complement(data[i0:i1], kind, bw)
    expected_full = data.copy()
    expected_full[i0:i1] = repl

    save_dtype = np.uint32 if word_bw <= 32 else np.uint64
    in_full = np.asarray(write_array(data, elem_type=elem_type, word_bw=word_bw))
    in_repl = np.asarray(write_array(repl, elem_type=elem_type, word_bw=word_bw))
    expected_read = np.asarray(write_array(data[i0:i1], elem_type=elem_type, word_bw=word_bw))
    expected_write = np.asarray(write_array(expected_full, elem_type=elem_type, word_bw=word_bw))

    np.savetxt(tmp_path / "in_full.txt", in_full.astype(save_dtype), fmt="%u")
    np.savetxt(tmp_path / "in_repl.txt", in_repl.astype(save_dtype), fmt="%u")

    cfg = BuildConfig(root_dir=tmp_path)
    generated_header = gen_array_utils(elem_type, [word_bw], cfg=cfg, streamutils_dir="include")
    StreamUtilsStep(output_dir="include").run(cfg)
    if "wf_cint.h" in generated_header.read_text(encoding="utf-8"):
        shutil.copy(WF_CINT_PATH, generated_header.parent / "wf_cint.h")

    cpp_src = (
        SLICE_CPP_PATH.read_text(encoding="utf-8")
        .replace("__HEADER__", generated_header.relative_to(tmp_path).as_posix())
        .replace("__NAMESPACE__", generated_header.stem)
        .replace("__WORD_BW__", str(word_bw))
        .replace("__NWORDS_FULL__", str(in_full.shape[0]))
        .replace("__NWORDS_SUB__", str(in_repl.shape[0]))
        .replace("__N__", str(n))
        .replace("__M__", str(i1 - i0))
        .replace("__I0__", str(i0))
        .replace("__I1__", str(i1))
    )
    (tmp_path / "arrayutils_slice_test.cpp").write_text(cpp_src, encoding="utf-8")
    shutil.copy(SLICE_TCL_PATH, tmp_path / "arrayutils_slice_run.tcl")

    _run_vitis_tcl(
        tmp_path / "arrayutils_slice_run.tcl",
        work_dir=tmp_path,
        failure_prefix=f"Vitis execution failed for arrayutils slice case {name!r}.",
    )

    out_read = np.atleast_1d(np.loadtxt(tmp_path / "out_read.txt", dtype=save_dtype))
    out_write = np.atleast_1d(np.loadtxt(tmp_path / "out_write.txt", dtype=save_dtype))

    assert np.array_equal(out_read, expected_read.astype(save_dtype)), (
        f"{name}: read_array_slice[{i0},{i1}) words differ from the Python golden."
    )
    assert np.array_equal(out_write, expected_write.astype(save_dtype)), (
        f"{name}: write_array_slice[{i0},{i1}) RMW words differ from the Python golden "
        "(a neighbor in a boundary word may have been clobbered)."
    )
