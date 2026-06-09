// vmac_compute_impl.tpp
// Included from gen/vmac.hpp. Types declared there (VmacCmd, the generated component
// array-utils namespace) are in scope. Do not include this file directly except via the .hpp.
//
// Hand-written body for the templated `vmac_impl::vmac_compute` hook — the single VMAC
// datapath: the **complex** fused op
//
//     D = alpha * A * op(B) + beta * C   [, reduced over rows]
//
// over a row-major shared-memory image reached through the `m_axi` pointer.  This is the C++
// contract of `VmacAccel.vmac_compute` (whose Python body is the golden `VmacAccel.execute`);
// it is bit-identical to that golden by construction (full-precision intermediates in the
// wide accumulator, a single lossy requantize).
//
// ONE fixed-format kernel, configured at *run time* by the command's op flags:
//
//   * Template params are **widths only** (MEM_BW / DATA_BW / INT_BITS / ACC_BW / OUT_BW /
//     Q_RND / O_SAT) -> the ap_int/ap_fixed types are compile-time (no dynamic types).
//   * The op flags (b_one / c_zero / b_conj / reduce_rows) are **runtime** if/mux —
//     loop-invariant, so no II hit.
//   * The requantize shift is **derived** from the flags + the structural format
//     (F_acc = (b_one?2:3)*F_in, F_out = F_in) -> SHIFT = F_acc - F_in: a variable barrel
//     shift on the fixed-width accumulator (vmac_requantize), not a dynamic type.
//
// VMAC is complex-only: every element is an interleaved (re, im) pair of DATA_BW-bit stored
// ints, so the component packing factor is CPF = MEM_BW / DATA_BW components/word and the
// kernel processes PF = CPF / 2 complex columns per word.  The complex multiply uses the
// **explicit re/im formula** (ar*br - ai*bi, ar*bi + ai*br); op(B) = conj(B) negates B's imag.
//
// Lanes reuse the generated per-type packing (NOT reinvented), exactly as
// poly_evaluate_impl.tpp uses float32_array_utils: `vmac_comp_array_utils::pf<MEM_BW>()` +
// `read_array_elem` / `write_array_elem` over a component lane array (the stored-int
// components, ap_int<DATA_BW>), with `#pragma HLS ARRAY_PARTITION` + `UNROLL`.

#include "vmac_utils.h"

namespace vmac_impl {

// Read PF complex columns (re/im stored-int components) of one operand row, starting at the
// complex element index `elem0` (== addr + i*row_stride + col0).  Rows are word-aligned
// (addr / row_stride are multiples of PF), so the contiguous columns are the components of
// one m_axi word: component index 2*elem0, word index 2*elem0 / CPF == elem0 / PF.
template <int MEM_BW, int DATA_BW, int PF>
static inline void vmac_read_lanes(const ap_uint<MEM_BW>* mem, int elem0,
                                   ap_int<DATA_BW> re[PF], ap_int<DATA_BW> im[PF]) {
#pragma HLS INLINE
    const int CPF = 2 * PF;
    ap_int<DATA_BW> comp[2 * PF];
#pragma HLS ARRAY_PARTITION variable=comp complete dim=1
    vmac_comp_array_utils::template read_array_elem<MEM_BW>(mem + (2 * elem0) / CPF, comp);
    for (int k = 0; k < PF; ++k) {
#pragma HLS UNROLL
        re[k] = comp[2 * k];
        im[k] = comp[2 * k + 1];
    }
}

// Write PF complex columns (re/im stored-int components) of one dst row at element `elem0`.
template <int MEM_BW, int DATA_BW, int PF>
static inline void vmac_write_lanes(ap_uint<MEM_BW>* mem, int elem0,
                                    const ap_int<DATA_BW> re[PF], const ap_int<DATA_BW> im[PF],
                                    int lane_count) {
#pragma HLS INLINE
    const int CPF = 2 * PF;
    ap_int<DATA_BW> comp[2 * PF];
#pragma HLS ARRAY_PARTITION variable=comp complete dim=1
    for (int k = 0; k < PF; ++k) {
#pragma HLS UNROLL
        comp[2 * k] = re[k];
        comp[2 * k + 1] = im[k];
    }
    vmac_comp_array_utils::template write_array_elem<MEM_BW>(comp, mem + (2 * elem0) / CPF,
                                                             2 * lane_count);
}

template <int MEM_BW, int DATA_BW, int INT_BITS, int ACC_BW, int OUT_BW,
          bool Q_RND, bool O_SAT, int MAX_COLS>
void vmac_compute(VmacCmd cmd, ap_uint<MEM_BW>* mem) {
    typedef ap_int<DATA_BW> A_T;       // stored-int operand component
    typedef ap_int<ACC_BW> ACC_T;      // full-precision integer accumulator
    static const int F_IN = DATA_BW - INT_BITS;
    static const int CPF = MEM_BW / DATA_BW;       // components / word
    static const int PF = CPF / 2;                 // complex columns / word

    const int n_rows = (int)cmd.n_rows;
    const int n_cols = (int)cmd.n_cols;
    const bool b_one = (bool)cmd.b_one;
    const bool c_zero = (bool)cmd.c_zero;
    const bool b_conj = (bool)cmd.b_conj;
    const bool reduce_rows = (bool)cmd.reduce_rows;

    // Derived requantize shift: F_acc = (b_one?2:3)*F_in, F_out = F_in -> SHIFT = F_acc - F_in.
    const int shift = (b_one ? 1 : 2) * F_IN;

    const int a_addr = (int)cmd.a.addr, a_rs = (int)cmd.a.row_stride;
    const int b_addr = (int)cmd.b.addr, b_rs = (int)cmd.b.row_stride;
    const int c_addr = (int)cmd.c.addr, c_rs = (int)cmd.c.row_stride;
    const int d_addr = (int)cmd.d.addr, d_rs = (int)cmd.d.row_stride;

    // alpha / beta immediates (direct); per-column (indirect) reads are loaded per column.
    const bool al_direct = (bool)cmd.alpha.direct, be_direct = (bool)cmd.beta.direct;
    const A_T al_re_imm = (A_T)(ap_int<DATA_BW>)cmd.alpha.re;
    const A_T al_im_imm = (A_T)(ap_int<DATA_BW>)cmd.alpha.im;
    const A_T be_re_imm = (A_T)(ap_int<DATA_BW>)cmd.beta.re;
    const A_T be_im_imm = (A_T)(ap_int<DATA_BW>)cmd.beta.im;

    // per-column accumulators (reduce_rows): one complex ACC_T per column, summed over rows.
    ACC_T acc_re[MAX_COLS], acc_im[MAX_COLS];
#pragma HLS ARRAY_PARTITION variable=acc_re cyclic factor=16 dim=1
#pragma HLS ARRAY_PARTITION variable=acc_im cyclic factor=16 dim=1
    if (reduce_rows) {
        for (int j = 0; j < n_cols; ++j) {
#pragma HLS PIPELINE II=1
            acc_re[j] = 0;
            acc_im[j] = 0;
        }
    }

    // outer loop over rows (strided by the pitch), inner over contiguous columns packed
    // PF/word (the GEMM accumulation pattern).
    for (int i = 0; i < n_rows; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=MAX_COLS
        for (int col0 = 0; col0 < n_cols; col0 += PF) {
#pragma HLS PIPELINE II=1
            A_T are[PF], aim[PF], bre[PF], bim[PF], cre[PF], cim[PF];
#pragma HLS ARRAY_PARTITION variable=are complete dim=1
#pragma HLS ARRAY_PARTITION variable=aim complete dim=1
#pragma HLS ARRAY_PARTITION variable=bre complete dim=1
#pragma HLS ARRAY_PARTITION variable=bim complete dim=1
#pragma HLS ARRAY_PARTITION variable=cre complete dim=1
#pragma HLS ARRAY_PARTITION variable=cim complete dim=1
            vmac_read_lanes<MEM_BW, DATA_BW, PF>(mem, a_addr + i * a_rs + col0, are, aim);
            if (!b_one)
                vmac_read_lanes<MEM_BW, DATA_BW, PF>(mem, b_addr + i * b_rs + col0, bre, bim);
            if (!c_zero)
                vmac_read_lanes<MEM_BW, DATA_BW, PF>(mem, c_addr + i * c_rs + col0, cre, cim);

            ap_int<DATA_BW> yr[PF], yi[PF];
#pragma HLS ARRAY_PARTITION variable=yr complete dim=1
#pragma HLS ARRAY_PARTITION variable=yi complete dim=1
            int lane_count = 0;
            for (int k = 0; k < PF; ++k) {
#pragma HLS UNROLL
                const int j = col0 + k;
                if (j >= n_cols) continue;
                lane_count = k + 1;

                // per-column alpha/beta (indirect): one complex element per column.
                A_T alre = al_re_imm, alim = al_im_imm, bere = be_re_imm, beim = be_im_imm;
                if (!al_direct) {
                    A_T tre[PF], tim[PF];
                    vmac_read_lanes<MEM_BW, DATA_BW, PF>(
                        mem, (int)cmd.alpha.addr + j * (int)cmd.alpha.stride, tre, tim);
                    alre = tre[0]; alim = tim[0];
                }
                if (!be_direct) {
                    A_T tre[PF], tim[PF];
                    vmac_read_lanes<MEM_BW, DATA_BW, PF>(
                        mem, (int)cmd.beta.addr + j * (int)cmd.beta.stride, tre, tim);
                    bere = tre[0]; beim = tim[0];
                }

                // op(B): identity, or conj(B) = (bre, -bim)
                ACC_T obre = bre[k], obim = b_conj ? (ACC_T)(-bim[k]) : (ACC_T)bim[k];
                // A * op(B)  (explicit re/im, full precision)
                ACC_T abre, abim;
                if (b_one) {
                    abre = (ACC_T)are[k]; abim = (ACC_T)aim[k];
                } else {
                    abre = (ACC_T)are[k] * obre - (ACC_T)aim[k] * obim;
                    abim = (ACC_T)are[k] * obim + (ACC_T)aim[k] * obre;
                }
                // alpha * (A*op(B))
                ACC_T tre = (ACC_T)alre * abre - (ACC_T)alim * abim;
                ACC_T tim = (ACC_T)alre * abim + (ACC_T)alim * abre;
                // + beta * C
                if (!c_zero) {
                    tre += (ACC_T)bere * (ACC_T)cre[k] - (ACC_T)beim * (ACC_T)cim[k];
                    tim += (ACC_T)bere * (ACC_T)cim[k] + (ACC_T)beim * (ACC_T)cre[k];
                }

                if (reduce_rows) {
                    acc_re[j] += tre;
                    acc_im[j] += tim;
                } else {
                    yr[k] = vmac_requantize<OUT_BW, Q_RND, O_SAT>(tre, shift);
                    yi[k] = vmac_requantize<OUT_BW, Q_RND, O_SAT>(tim, shift);
                }
            }
            if (!reduce_rows)
                vmac_write_lanes<MEM_BW, DATA_BW, PF>(mem, d_addr + i * d_rs + col0, yr, yi,
                                                      lane_count);
        }
    }

    // reduce_rows writeback: one row of n_cols requantized results at the dst.
    if (reduce_rows) {
        for (int col0 = 0; col0 < n_cols; col0 += PF) {
#pragma HLS PIPELINE II=1
            ap_int<DATA_BW> yr[PF], yi[PF];
#pragma HLS ARRAY_PARTITION variable=yr complete dim=1
#pragma HLS ARRAY_PARTITION variable=yi complete dim=1
            int lane_count = 0;
            for (int k = 0; k < PF; ++k) {
#pragma HLS UNROLL
                const int j = col0 + k;
                if (j >= n_cols) continue;
                lane_count = k + 1;
                yr[k] = vmac_requantize<OUT_BW, Q_RND, O_SAT>(acc_re[j], shift);
                yi[k] = vmac_requantize<OUT_BW, Q_RND, O_SAT>(acc_im[j], shift);
            }
            vmac_write_lanes<MEM_BW, DATA_BW, PF>(mem, d_addr + col0, yr, yi, lane_count);
        }
    }
}

}  // namespace vmac_impl
