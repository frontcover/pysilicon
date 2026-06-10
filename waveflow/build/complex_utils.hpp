#ifndef WAVEFLOW_COMPLEX_UTILS_HPP
#define WAVEFLOW_COMPLEX_UTILS_HPP

// Self-contained Vitis HLS complex arithmetic for Waveflow ComplexField elements.
//
// Mirrors waveflow/utils/complexutils.py exactly: the **explicit re/im formula at FULL
// PRECISION** (ar*br - ai*bi, ar*bi + ai*br) -- NOT std::complex operator*, which
// FMA-contracts (float) / quantizes the product back to the input format (ap_fixed).  Works
// for both the std::complex<T> element (float / ap_fixed inner) and the wf_cint<W> element
// (ap_int inner; std::complex<ap_int> is non-standard).
//
// The result component type is computed by the native ap_int / ap_fixed / float operators,
// which widen exactly as the Python *_format rules do, so the result grows identically:
//
//   cmult -> (2W+1, 2I+1, signed)     conj -> (W+1, I+1, signed)
//   cadd  -> add_format (int bits +1)  csub -> sub_format (always signed)
//
// The result type is always wide enough to hold the full-precision value, so no rounding /
// overflow occurs -- the Q/O modes are irrelevant to the produced bits, hence bit-exact with
// the composed Python model.

#include <ap_fixed.h>
#include <ap_int.h>
#include <complex>

#include "wf_cint.h"

// Keep the float explicit formula as three IEEE-rounded ops (ar*br, ai*bi, then subtract) --
// no fused-multiply-add contraction -- so it is bit-exact with cmult_float's naive formula.
#pragma STDC FP_CONTRACT OFF

namespace complex_utils {

// --- element component accessors: std::complex<T> .real()/.imag() vs wf_cint<W> .re/.im ---
template <typename T>
static inline T cu_re(const std::complex<T>& z) { return z.real(); }
template <typename T>
static inline T cu_im(const std::complex<T>& z) { return z.imag(); }
template <int W>
static inline ap_int<W> cu_re(const wf_cint<W>& z) { return z.re; }
template <int W>
static inline ap_int<W> cu_im(const wf_cint<W>& z) { return z.im; }

// --- result element type: same family as the input C, with component type Comp ---
template <typename C, typename Comp>
struct cu_result;
template <typename T, typename Comp>
struct cu_result<std::complex<T>, Comp> { typedef std::complex<Comp> type; };
template <int W, typename Comp>
struct cu_result<wf_cint<W>, Comp> { typedef wf_cint<Comp::width> type; };

template <typename C, typename Comp>
static inline typename cu_result<C, Comp>::type cu_make(const Comp& re, const Comp& im) {
    return typename cu_result<C, Comp>::type(re, im);
}

// The return type of each op is deduced (C++14 `auto`) from its body, so it follows the
// native ap_int / ap_fixed / float operator widening exactly -- the same growth the Python
// *_format rules apply.  (A trailing `decltype(...)` return type is both redundant and, for
// ap_fixed subtraction, mis-deduced -- so the body is the single source of truth.)

// --- cmult: (ar*br - ai*bi) + j(ar*bi + ai*br), full precision ------------------
// Named products (matching cmult's p_rr / p_ii / p_ri / p_ir) keep the op order explicit.
template <typename C>
static inline auto cmult(const C& a, const C& b) {
#pragma HLS INLINE
    auto p_rr = cu_re(a) * cu_re(b);
    auto p_ii = cu_im(a) * cu_im(b);
    auto p_ri = cu_re(a) * cu_im(b);
    auto p_ir = cu_im(a) * cu_re(b);
    auto re = p_rr - p_ii;   // sub_format(P, P) -> (2W+1, 2I+1, signed)
    auto im = p_ri + p_ir;   // add_format(P, P) -> same format
    return cu_make<C>(re, im);
}

// --- cadd: (ar+br) + j(ai+bi) (add_format: int bits +1; inner signedness rule) --
template <typename C>
static inline auto cadd(const C& a, const C& b) {
#pragma HLS INLINE
    auto re = cu_re(a) + cu_re(b);
    auto im = cu_im(a) + cu_im(b);
    return cu_make<C>(re, im);
}

// --- csub: (ar-br) + j(ai-bi) (sub_format: always signed) -----------------------
template <typename C>
static inline auto csub(const C& a, const C& b) {
#pragma HLS INLINE
    auto re = cu_re(a) - cu_re(b);
    auto im = cu_im(a) - cu_im(b);
    return cu_make<C>(re, im);
}

// --- conj: ar - j(ai) (result (W+1, I+1, signed); im = 0 - ai, re widened to match) --
template <typename C>
static inline auto conj(const C& a) {
#pragma HLS INLINE
    auto im_in = cu_im(a);
    decltype(im_in) zero = 0;
    auto im = zero - im_in;        // 0 - ai (sub widens to (W+1, I+1, signed)) == cx.conj
    decltype(im) re = cu_re(a);    // widen re losslessly into the same format
    return cu_make<C>(re, im);
}

}  // namespace complex_utils

#endif  // WAVEFLOW_COMPLEX_UTILS_HPP
