"""complex_utils.hpp — standalone host-compile sanity check (no Vitis synthesis).

Phase 2 of plans/complex_serialization.md adds ``waveflow/build/complex_utils.hpp``: the Vitis
complex arithmetic (``cmult`` / ``cadd`` / ``csub`` / ``conj``) over the element ``cpp_type``,
the **explicit re/im formula at full precision** (NOT ``std::complex`` ``operator*``), mirroring
``waveflow/utils/complexutils.py``'s result growth.

This test compiles a small program that ``#include``s the header against the **real Vitis
``ap_int`` / ``ap_fixed`` headers** with a host C++ compiler (g++), then runs it.  It does NOT
invoke Vitis HLS synthesis (that bit-exact conformance is Phase 3).  ``static_assert``s pin the
result component widths to the Python ``*_format`` growth; runtime checks pin a few values.

Skipped when a host C++ compiler or the Vitis include dir is not found.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from waveflow.toolchain import toolchain

_HEADER_DIR = Path(__file__).resolve().parents[2] / "waveflow" / "build"


def _vitis_include_dir() -> Path | None:
    """Locate the Vitis include dir (holding ap_int.h / ap_fixed.h) from the toolchain path."""
    vitis = toolchain.find_vitis_path()
    if not vitis:
        return None
    # find_vitis_path -> .../Vitis/bin/vitis-run(.bat); the headers live in .../Vitis/include
    for parent in Path(vitis).resolve().parents:
        cand = parent / "include" / "ap_int.h"
        if cand.exists():
            return cand.parent
    return None


_CXX = next((c for c in ("g++", "c++") if shutil.which(c)), None)
_VITIS_INC = _vitis_include_dir()

_PROGRAM = r"""
#include "complex_utils.hpp"
#include <type_traits>
#include <cstdio>

template <typename A, typename B> struct same { static const bool ok = false; };
template <typename A> struct same<A, A> { static const bool ok = true; };
template <typename Z> struct comp {
    typedef typename std::decay<decltype(std::declval<Z>().real())>::type type;
};

int main() {
    // fixed inner ap_fixed<8,4>: cmult -> (17,9); cadd/csub/conj -> (9,5)
    typedef ap_fixed<8,4,AP_TRN,AP_WRAP> F;
    std::complex<F> a(F(1.5), F(-2.25)), b(F(0.75), F(3.0));
    static_assert(comp<decltype(complex_utils::cmult(a,b))>::type::width  == 17, "cmult fixed W");
    static_assert(comp<decltype(complex_utils::cmult(a,b))>::type::iwidth ==  9, "cmult fixed I");
    static_assert(comp<decltype(complex_utils::cadd(a,b))>::type::width   ==  9, "cadd fixed W");
    static_assert(comp<decltype(complex_utils::csub(a,b))>::type::width   ==  9, "csub fixed W");
    static_assert(comp<decltype(complex_utils::conj(a))>::type::width     ==  9, "conj fixed W");
    static_assert(comp<decltype(complex_utils::conj(a))>::type::iwidth    ==  5, "conj fixed I");

    // unsigned cadd stays unsigned, grows to (9,5)
    typedef ap_ufixed<8,4,AP_TRN,AP_WRAP> UF;
    std::complex<UF> ua(UF(1.5),UF(2.25)), ub(UF(0.75),UF(3.0));
    static_assert(comp<decltype(complex_utils::cadd(ua,ub))>::type::width == 9, "cadd ufixed W");

    // int inner wf_cint<8>: cmult -> wf_cint<17>; cadd/csub/conj -> wf_cint<9>
    wf_cint<8> ia(ap_int<8>(3), ap_int<8>(-4)), ib(ap_int<8>(2), ap_int<8>(5));
    static_assert(same<decltype(complex_utils::cmult(ia,ib)), wf_cint<17> >::ok, "cmult int");
    static_assert(same<decltype(complex_utils::cadd(ia,ib)),  wf_cint<9>  >::ok, "cadd int");
    static_assert(same<decltype(complex_utils::csub(ia,ib)),  wf_cint<9>  >::ok, "csub int");
    static_assert(same<decltype(complex_utils::conj(ia)),     wf_cint<9>  >::ok, "conj int");

    // float inner: no growth (std::complex<float>); explicit naive formula
    std::complex<float> fa(1.0f, 2.0f), fb(3.0f, -4.0f);
    static_assert(same<decltype(complex_utils::cmult(fa,fb)), std::complex<float> >::ok, "cmult f32");

    int rc = 0;
    auto fm = complex_utils::cmult(fa, fb);  // (1+2i)(3-4i) = 11 + 2i
    if (!(fm.real() == 11.0f && fm.imag() == 2.0f)) { printf("FAIL f cmult\n"); rc = 1; }
    auto fc = complex_utils::conj(fa);       // conj(1+2i) = 1 - 2i
    if (!(fc.real() == 1.0f && fc.imag() == -2.0f)) { printf("FAIL f conj\n"); rc = 1; }
    auto im = complex_utils::cmult(ia, ib);  // (3-4i)(2+5i) = 26 + 7i
    if (!((int)im.re == 26 && (int)im.im == 7)) { printf("FAIL i cmult\n"); rc = 1; }
    auto ic = complex_utils::conj(ia);       // conj(3-4i) = 3 + 4i
    if (!((int)ic.re == 3 && (int)ic.im == 4)) { printf("FAIL i conj\n"); rc = 1; }
    if (rc == 0) printf("OK\n");
    return rc;
}
"""


@pytest.mark.skipif(_CXX is None, reason="no host C++ compiler (g++) found")
@pytest.mark.skipif(_VITIS_INC is None, reason="Vitis include dir (ap_int.h) not found")
def test_complex_utils_hpp_compiles_and_runs(tmp_path):
    src = tmp_path / "cu_check.cpp"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp_path / "cu_check.exe"
    compile_cmd = [
        _CXX, "-std=c++14",
        "-I", str(_HEADER_DIR),
        "-I", str(_VITIS_INC),
        str(src), "-o", str(exe),
    ]
    cp = subprocess.run(compile_cmd, capture_output=True, text=True)
    assert cp.returncode == 0, (
        "complex_utils.hpp failed to compile (static_asserts pin the result widths to the "
        f"Python *_format growth):\n{cp.stderr}")
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.returncode == 0 and "OK" in run.stdout, (
        f"complex_utils.hpp value checks failed:\n{run.stdout}\n{run.stderr}")
