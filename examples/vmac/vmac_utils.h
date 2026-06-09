#ifndef WAVEFLOW_VMAC_UTILS_H
#define WAVEFLOW_VMAC_UTILS_H

// Small generic helpers for the VMAC compute hook, so vmac_compute_impl.tpp reads as a
// plain complex datapath.  Element packing itself is NOT reinvented here — the hook uses
// the generated per-type array-utils (pf<>() + read_array_elem / write_array_elem), exactly
// as examples/stream_inband/poly_evaluate_impl.tpp uses float32_array_utils.

#include <ap_int.h>

namespace vmac_impl {

// Integer-domain requantize — the single lossy step.  Arithmetic right-shift of the
// fixed-width accumulator by a *runtime* `shift` (a barrel shift; the shift is derived from
// the op flags + the structural format, not a dynamic type), with **compile-time** round
// (Q_RND -> AP_RND, round half up) and saturate (O_SAT -> AP_SAT) into OUT_BW signed bits.
// Bit-exact with VmacAccel._requantize (fixputils.quantize / an ap_fixed assignment).
template <int OUT_BW, bool Q_RND, bool O_SAT, typename ACC_T>
ap_int<OUT_BW> vmac_requantize(ACC_T acc, int shift) {
#pragma HLS INLINE
    ACC_T r = acc;
    if (shift > 0) {
        if (Q_RND) r += (ACC_T(1) << (shift - 1));   // round half up (toward +inf)
        r >>= shift;                                  // arithmetic shift (toward -inf)
    }
    if (O_SAT) {                                      // saturate to the OUT_BW signed range
        const ACC_T hi = (ACC_T(1) << (OUT_BW - 1)) - 1;
        const ACC_T lo = -(ACC_T(1) << (OUT_BW - 1));
        if (r > hi) r = hi;
        else if (r < lo) r = lo;
    }
    return (ap_int<OUT_BW>)r;                          // O_WRAP = the low OUT_BW bits
}

}  // namespace vmac_impl

#endif  // WAVEFLOW_VMAC_UTILS_H
