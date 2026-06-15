"""Phase 1a lane-method conformance — Python golden vs Vitis csim, bit-exact.

Exercises the new regime-agnostic ``*_lane`` methods (``read_array_lane`` / ``write_array_lane``
+ the FIFO / AXI4-Stream variants) through the canonical lane loop, for **both** regimes:

- ``pf >= 1`` (vectorized): several word widths, including a **partial tail** (``n < pf``).
- ``pf == 0`` (wide element, the path nothing tested before): a 64-bit element on a 32-bit
  channel, and a wide complex element — each spanning ``ceil(elem/W)`` words/beats.

Each case lays the array with the Python golden (``arrayutils.write_array``), runs the lane loop
read→write round-trip in Vitis csim, and asserts the re-emitted words equal the golden words
bit-for-bit. The C++ testbench additionally re-checks the FIFO and AXI4-Stream lane paths
reproduce the same words in-sim (a divergence returns non-zero and fails csim).

Reuses the ``tests/hw/test_arrayutils_vitis.py`` harness pattern (template cpp + tcl + csim).
"""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from waveflow.build.build import BuildConfig
from waveflow.build.streamutils import StreamUtilsStep
from waveflow.hw.arrayutils import gen_array_utils, read_array, write_array
from waveflow.hw.complexfield import ComplexField
from waveflow.hw.dataschema import FloatField, IntField
from waveflow.toolchain import toolchain
from waveflow.utils import complexutils as cx

TEST_DIR = Path(__file__).parent
RESOURCE_DIR = TEST_DIR / "resources"
LANE_CPP_PATH = RESOURCE_DIR / "arrayutils_lane_roundtrip_test.cpp"
ROUNDTRIP_TCL_PATH = RESOURCE_DIR / "arrayutils_roundtrip_run.tcl"
WF_CINT_PATH = Path(__file__).resolve().parents[2] / "waveflow" / "build" / "wf_cint.h"


def _s(bw: int):
    return IntField.specialize(bitwidth=bw, signed=True, include_dir="include")


def _cint(bw: int):
    return ComplexField.specialize(_s(bw), include_dir="include")


def _f(bw: int):
    return FloatField.specialize(bitwidth=bw, include_dir="include")


def _int_data(bw: int, n: int) -> np.ndarray:
    # Evenly spaced integers in [lo, hi] via integer math (no float overflow at bw = 64),
    # with the signed extremes pinned at the ends.
    lo, hi = -(1 << (bw - 1)), (1 << (bw - 1)) - 1
    if n == 1:
        return np.array([hi], dtype=np.int64)
    step = (hi - lo) // (n - 1)
    vals = [lo + step * i for i in range(n)]
    vals[-1] = hi
    return np.array(vals, dtype=np.int64)


def _complex_data(bw: int, n: int) -> np.ndarray:
    re = _int_data(bw, n)
    im = np.roll(_int_data(bw, n), 1)
    return cx.make_complex(re, im, cx.int_format(bw, True))


# (id, elem_type, word_bw, length, data, regime-note). pf = word_bw // elem_bw (0 if wider).
def _cases():
    return [
        # --- pf >= 1 (vectorized), various word widths + partial tails ---
        ("s16_w32_full", _s(16), 32, 8, _int_data(16, 8)),       # pf=2, no tail
        ("s16_w32_tail", _s(16), 32, 7, _int_data(16, 7)),       # pf=2, tail n=1
        ("s16_w64_tail", _s(16), 64, 11, _int_data(16, 11)),     # pf=4, tail n=3
        ("s32_w32", _s(32), 32, 5, _int_data(32, 5)),            # pf=1
        ("f32_w32", _f(32), 32, 6, np.linspace(-2.5, 3.5, 6, dtype=np.float32)),   # pf=1
        ("f32_w64", _f(32), 64, 6, np.linspace(-9.0, 9.0, 6, dtype=np.float32)),   # pf=2
        # --- pf == 0 (wide element: one element spans ceil(elem/W) words) ---
        ("s64_w32", _s(64), 32, 4, _int_data(64, 4)),            # 64-bit elem on 32-bit channel
        ("s64_w32_odd", _s(64), 32, 3, _int_data(64, 3)),        # wide, odd count
        ("cint32_w32", _cint(32), 32, 4, _complex_data(32, 4)),  # wide complex (re|im = 64 bits)
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
def test_arrayutils_lane_roundtrip_vitis(tmp_path: Path, case):
    name, elem_type, word_bw, length, data = case

    vitis_path = toolchain.find_vitis_path()
    if not vitis_path:
        pytest.skip("Vitis installation not found; skipping arrayutils lane integration test.")

    in_words = np.asarray(write_array(data, elem_type=elem_type, word_bw=word_bw))
    save_dtype = np.uint32 if word_bw <= 32 else np.uint64

    in_words_path = tmp_path / "array_words.txt"
    out_words_path = tmp_path / "array_words_out.txt"
    np.savetxt(in_words_path, in_words.astype(save_dtype), fmt="%u")

    cfg = BuildConfig(root_dir=tmp_path)
    generated_header = gen_array_utils(elem_type, [word_bw], cfg=cfg, streamutils_dir="include")
    StreamUtilsStep(output_dir="include").run(cfg)

    # An integer-inner ComplexField element maps to wf_cint<W>; its array-utils header
    # #includes "wf_cint.h", so make it resolvable next to the generated header.
    if "wf_cint.h" in generated_header.read_text(encoding="utf-8"):
        shutil.copy(WF_CINT_PATH, generated_header.parent / "wf_cint.h")

    header_include = generated_header.relative_to(tmp_path).as_posix()
    namespace_name = generated_header.stem
    cpp_src = (
        LANE_CPP_PATH.read_text(encoding="utf-8")
        .replace("__HEADER__", header_include)
        .replace("__NAMESPACE__", namespace_name)
        .replace("__WORD_BW__", str(word_bw))
        .replace("__ARRAY_LEN__", str(length))
        .replace("__NWORDS__", str(in_words.shape[0]))
    )
    (tmp_path / "arrayutils_lane_roundtrip_test.cpp").write_text(cpp_src, encoding="utf-8")

    tcl_src = ROUNDTRIP_TCL_PATH.read_text(encoding="utf-8").replace(
        "arrayutils_roundtrip_test.cpp", "arrayutils_lane_roundtrip_test.cpp"
    )
    (tmp_path / "arrayutils_lane_roundtrip_run.tcl").write_text(tcl_src, encoding="utf-8")

    _run_vitis_tcl(
        tmp_path / "arrayutils_lane_roundtrip_run.tcl",
        work_dir=tmp_path,
        failure_prefix=f"Vitis execution failed for arrayutils lane roundtrip case {name!r}.",
    )

    out_words = np.atleast_1d(np.loadtxt(out_words_path, dtype=save_dtype))

    # The lane-loop round-trip must reproduce the Python golden words bit-for-bit.
    assert np.array_equal(out_words, in_words.astype(save_dtype)), (
        f"{name}: lane round-trip words differ from the Python golden."
    )

    # Numeric round-trip check for the simple element types (complex is covered by words-equal).
    if isinstance(elem_type, type) and issubclass(elem_type, (IntField, FloatField)):
        got = np.asarray(read_array(out_words, elem_type=elem_type, word_bw=word_bw, shape=length))
        ref = np.asarray(data)
        if issubclass(elem_type, FloatField):
            assert np.allclose(got, ref, rtol=1e-6, atol=1e-6)
        else:
            assert np.array_equal(got, ref.astype(got.dtype))
