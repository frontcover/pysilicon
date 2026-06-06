"""Phase 3 (Vitis): a generated kernel using a FixedField compiles + runs under csim.

Mirrors test_arrayutils_vitis: generate the array read/write C++ for a FixedField
element (value_type = ap_fixed<W,I,Q,O>), then csim a round-trip through the
generated code.  This proves the FixedField codegen — the ap_fixed cpp_type, the
ap_fixed.h include, and the .range() bit-reinterpret helpers — actually compiles
under Vitis and preserves the W-bit payload exactly.

(The *quantization* bit-exactness vs Vitis ap_fixed is the Phase-4 conformance
milestone; here the values are already representable, so this is the serialization
round-trip.)
"""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from pysilicon.build.build import BuildConfig
from pysilicon.build.streamutils import StreamUtilsStep
from pysilicon.hw.arrayutils import gen_array_utils, read_array, write_array
from pysilicon.hw.fixpoint import FixedField
from pysilicon.utils.fixputils import AP_RND, AP_SAT, AP_TRN, AP_WRAP
from pysilicon.toolchain import toolchain

TEST_DIR = Path(__file__).parent
RESOURCE_DIR = TEST_DIR / "resources"
ROUNDTRIP_CPP_PATH = RESOURCE_DIR / "arrayutils_roundtrip_test.cpp"
ROUNDTRIP_TCL_PATH = RESOURCE_DIR / "arrayutils_roundtrip_run.tcl"


def _run_vitis_tcl(tcl_path: Path, work_dir: Path, failure_prefix: str) -> None:
    try:
        toolchain.run_vitis_hls(tcl_path, work_dir=work_dir)
    except RuntimeError as exc:
        pytest.skip(f"Vitis execution unavailable in current setup: {exc}")
    except subprocess.CalledProcessError as exc:
        pytest.fail(
            f"{failure_prefix}\nCommand: {exc.cmd}\nReturn code: {exc.returncode}\n"
            f"Stdout:\n{exc.stdout}\nStderr:\n{exc.stderr}")


def _representable(W, I, signed, count=12):  # noqa: E741
    lsb = 2.0 ** (-(W - I))
    lo = -(1 << (W - 1)) if signed else 0
    hi = (1 << (W - 1)) - 1 if signed else (1 << W) - 1
    codes = np.linspace(lo, hi, count).astype(np.int64)
    return (codes * lsb).astype(np.float64)


@pytest.mark.vitis
@pytest.mark.parametrize("W,I,signed,q,o", [
    (8, 4, True, AP_TRN, AP_WRAP),
    (8, 4, False, AP_TRN, AP_WRAP),
    (16, 8, True, AP_RND, AP_SAT),
])
def test_fixedfield_array_csim_roundtrip(tmp_path: Path, W, I, signed, q, o):  # noqa: E741
    if not toolchain.find_vitis_path():
        pytest.skip("Vitis installation not found; skipping FixedField csim test.")

    Q = FixedField.specialize(W, I, signed=signed, q_mode=q, o_mode=o, include_dir="include")
    word_bw = 32
    data = _representable(W, I, signed)
    length = len(data)
    in_words = write_array(data, elem_type=Q, word_bw=word_bw)

    in_words_path = tmp_path / "array_words.txt"
    out_words_path = tmp_path / "array_words_out.txt"
    np.savetxt(in_words_path, np.asarray(in_words).astype(np.uint32), fmt="%u")

    cfg = BuildConfig(root_dir=tmp_path)
    generated_header = gen_array_utils(Q, [word_bw], cfg=cfg, streamutils_dir="include")
    StreamUtilsStep(output_dir="include").run(cfg)

    cpp_src = (
        ROUNDTRIP_CPP_PATH.read_text(encoding="utf-8")
        .replace("__HEADER__", generated_header.relative_to(tmp_path).as_posix())
        .replace("__NAMESPACE__", generated_header.stem)
        .replace("__WORD_BW__", str(word_bw))
        .replace("__ARRAY_LEN__", str(length))
        .replace("__NWORDS__", str(np.asarray(in_words).shape[0]))
    )
    (tmp_path / "arrayutils_roundtrip_test.cpp").write_text(cpp_src, encoding="utf-8")
    shutil.copy(ROUNDTRIP_TCL_PATH, tmp_path / "arrayutils_roundtrip_run.tcl")

    _run_vitis_tcl(
        tmp_path / "arrayutils_roundtrip_run.tcl", work_dir=tmp_path,
        failure_prefix=f"Vitis csim failed for FixedField<{W},{I},signed={signed},{q},{o}>.")

    out_words = np.atleast_1d(np.loadtxt(out_words_path, dtype=np.uint32))
    got = np.asarray(read_array(out_words, elem_type=Q, word_bw=word_bw, shape=length))
    # the generated ap_fixed code preserves the W-bit payload exactly
    assert np.array_equal(out_words, np.asarray(in_words).astype(np.uint32))
    np.testing.assert_array_equal(got, data)
