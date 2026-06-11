// vmac_compute_impl.tpp
// Included from gen/vmac.hpp.  The generated header brings into scope, *before* this file:
//
//   * VmacCmd                  — the command struct (DataSchemaStep).
//   * vmac_in_au / vmac_out_au — namespace aliases for the **generated ComplexField
//                                serialization** of the operand / output element types
//                                (ArrayUtilsStep over ComplexField.specialize(FixedField…)).
//     `vmac_in_au::value_type`  == std::complex<ap_fixed<DATA_BW, INT_BITS, …>>  (operand)
//     `vmac_out_au::value_type` == std::complex<ap_fixed<OUT_BW,  OUT_INT,  …>>  (writeback)
//
// Hand-written body for the templated `vmac_impl::vmac_compute` hook — the single VMAC
// datapath: the **complex** fused op
//
//     D = alpha * A * op(B) + beta * C   [, reduced over rows]
//
// over a row-major shared-memory image reached through the `m_axi` pointer.  This is the C++
// contract of `VmacAccel.vmac_compute` (whose Python body is the golden `VmacAccel.execute`);
// it is bit-identical to that golden by construction (full-precision integer intermediates in
// the wide accumulator, a single lossy requantize).
//
// ONE fixed-format kernel, configured at *run time* by the command's op flags:
//
//   * Template params are **widths only** (MEM_BW / DATA_BW / INT_BITS / ACC_BW / OUT_BW /
//     Q_RND / O_SAT) -> the ap_int/ap_fixed types are compile-time (no dynamic types).
//   * The op flags (b_one / c_zero / b_conj / reduce_rows) are **runtime** if/mux —
//     loop-invariant, so no II hit.
//   * The requantize shift is **derived** from the flags + the structural format
//     (F_acc = (b_one?2:3)*F_in, F_out = F_in) -> SHIFT = F_acc - F_out: a variable barrel
//     shift on the fixed-width accumulator (vmac_requantize), not a dynamic type.
//
// VMAC is complex-only: every element is one packed ComplexField (interleaved re/im), so the
// element bitwidth is 2*DATA_BW and the kernel processes PF = MEM_BW / (2*DATA_BW) complex
// columns per memory word.  The complex multiply uses the **explicit re/im formula**
// (ar*br - ai*bi, ar*bi + ai*br) over the operands' integer codes (full precision, no
// quantization until the final requantize); op(B) = conj(B) negates B's imag.
//
// Lane I/O reuses the **generated** ComplexField serialization (NOT a hand-rolled component
// interleave): `vmac_in_au::read_array_elem<MEM_BW>` fills a lane buffer of complex elements
// from one memory word, and `vmac_out_au::write_array_elem<MEM_BW>` packs the requantized
// results back, exactly as examples/schemas/complex's migrated kernel does.  Per-row operand
// regions are word-aligned (addr / row_stride are multiples of PF), so the PF contiguous
// columns of a row live in the single word at element index / PF.

#include "vmac_utils.h"

namespace vmac_impl {

// Operand code <-> packed ComplexField component, via the generated serialization's
// fixed<->bits helpers (ap_fixed's raw .V is not directly convertible to ap_int).  fx_code
// reinterprets the stored bits as the signed integer code; fx_from_code is its inverse.
template <typename FX>
static inline ap_int<FX::width> fx_code(const FX& x) {
#pragma HLS INLINE
    return (ap_int<FX::width>)streamutils::fixed_to_bits<FX>(x);
}
template <typename FX>
static inline FX fx_from_code(ap_int<FX::width> code) {
#pragma HLS INLINE
    return streamutils::bits_to_fixed<FX>((ap_uint<FX::width>)code);
}

// The datapath, taking the command as **plain scalars** (not the VmacCmd struct).  The
// synthesizable top calls this directly: a nested struct passed by value mis-decomposes
// through HLS's Array/Struct optimization at csynth (loop bounds fold to 0 -> the kernel is
// DCE'd), whereas scalar s_axilite args lower cleanly.  vmac_compute(cmd, mem) below is the
// thin struct-taking wrapper the csim conformance harness drives.
template <int MEM_BW, int DATA_BW, int INT_BITS, int ACC_BW, int OUT_BW,
          bool Q_RND, bool O_SAT, int MAX_COLS>
void vmac_compute_core(
    ap_uint<MEM_BW>* mem,
    int n_rows, int n_cols, bool b_one, bool c_zero, bool b_conj, bool reduce_rows,
    int a_addr, int a_rs, int b_addr, int b_rs, int c_addr, int c_rs, int d_addr, int d_rs,
    bool al_direct, int al_re, int al_im, int al_addr, int al_stride,
    bool be_direct, int be_re, int be_im, int be_addr, int be_stride) {
#pragma HLS INLINE
    // Inline into the synthesizable top so the m_axi reads/writes belong to the top's gmem
    // port (kept as a separate module, the top would have "no outputs" and gmem would dangle).
    typedef ap_int<DATA_BW> A_T;              // operand (re/im) integer code
    typedef ap_int<ACC_BW> ACC_T;             // full-precision integer accumulator
    typedef typename vmac_in_au::value_type CX;    // std::complex<ap_fixed<DATA_BW,…>>
    typedef typename vmac_out_au::value_type CXO;  // std::complex<ap_fixed<OUT_BW,…>>
    typedef typename CXO::value_type OUT_FX;        // inner ap_fixed<OUT_BW, OUT_INT, …>
    static const int F_IN = DATA_BW - INT_BITS;
    static const int PF = MEM_BW / (2 * DATA_BW);  // complex columns / word

    // Derived requantize shift: F_acc = (b_one?2:3)*F_in, F_out = F_in -> SHIFT = F_acc - F_in.
    const int shift = (b_one ? 1 : 2) * F_IN;
    // beta*C lands at scale 2*F_in; the alpha*A*op(B) term sits at scale (b_one?2:3)*F_in, so
    // when !b_one the addend must be aligned up by F_in to the accumulator's fractional depth.
    const int c_align = b_one ? 0 : F_IN;

    // alpha / beta immediates (direct); per-column (indirect) reads are loaded per column.
    const A_T al_re_imm = (A_T)(ap_int<DATA_BW>)al_re;
    const A_T al_im_imm = (A_T)(ap_int<DATA_BW>)al_im;
    const A_T be_re_imm = (A_T)(ap_int<DATA_BW>)be_re;
    const A_T be_im_imm = (A_T)(ap_int<DATA_BW>)be_im;

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
            const int cols = (n_cols - col0 < PF) ? (n_cols - col0) : PF;
            CX a_lane[PF], b_lane[PF], c_lane[PF];
#pragma HLS ARRAY_PARTITION variable=a_lane complete dim=1
#pragma HLS ARRAY_PARTITION variable=b_lane complete dim=1
#pragma HLS ARRAY_PARTITION variable=c_lane complete dim=1
            vmac_in_au::read_array_elem<MEM_BW>(
                mem + (a_addr + i * a_rs + col0) / PF, a_lane, cols);
            if (!b_one)
                vmac_in_au::read_array_elem<MEM_BW>(
                    mem + (b_addr + i * b_rs + col0) / PF, b_lane, cols);
            if (!c_zero)
                vmac_in_au::read_array_elem<MEM_BW>(
                    mem + (c_addr + i * c_rs + col0) / PF, c_lane, cols);

            CXO y_lane[PF];
#pragma HLS ARRAY_PARTITION variable=y_lane complete dim=1
            for (int k = 0; k < PF; ++k) {
#pragma HLS UNROLL
                const int j = col0 + k;
                if (j >= n_cols) continue;

                const A_T are = fx_code(a_lane[k].real()), aim = fx_code(a_lane[k].imag());

                // per-column alpha/beta (indirect): one complex element per column, read from
                // the word containing it (arbitrary alignment -> pick the lane).
                A_T alre = al_re_imm, alim = al_im_imm, bere = be_re_imm, beim = be_im_imm;
                if (!al_direct) {
                    const int e = al_addr + j * al_stride;
                    CX sb[PF];
                    vmac_in_au::read_array_elem<MEM_BW>(mem + e / PF, sb, PF);
                    alre = fx_code(sb[e % PF].real()); alim = fx_code(sb[e % PF].imag());
                }
                if (!be_direct && !c_zero) {
                    const int e = be_addr + j * be_stride;
                    CX sb[PF];
                    vmac_in_au::read_array_elem<MEM_BW>(mem + e / PF, sb, PF);
                    bere = fx_code(sb[e % PF].real()); beim = fx_code(sb[e % PF].imag());
                }

                // A * op(B)  (explicit re/im, full precision).  op(B): identity or conj.
                ACC_T abre, abim;
                if (b_one) {
                    abre = (ACC_T)are; abim = (ACC_T)aim;
                } else {
                    const A_T bre = fx_code(b_lane[k].real()), bim = fx_code(b_lane[k].imag());
                    const ACC_T obre = bre;
                    const ACC_T obim = b_conj ? (ACC_T)(-bim) : (ACC_T)bim;
                    abre = (ACC_T)are * obre - (ACC_T)aim * obim;
                    abim = (ACC_T)are * obim + (ACC_T)aim * obre;
                }
                // alpha * (A*op(B))
                ACC_T tre = (ACC_T)alre * abre - (ACC_T)alim * abim;
                ACC_T tim = (ACC_T)alre * abim + (ACC_T)alim * abre;
                // + beta * C  (aligned up to the accumulator fractional depth when !b_one)
                if (!c_zero) {
                    const A_T cre = fx_code(c_lane[k].real()), cim = fx_code(c_lane[k].imag());
                    ACC_T bcre = (ACC_T)bere * (ACC_T)cre - (ACC_T)beim * (ACC_T)cim;
                    ACC_T bcim = (ACC_T)bere * (ACC_T)cim + (ACC_T)beim * (ACC_T)cre;
                    tre += bcre << c_align;
                    tim += bcim << c_align;
                }

                if (reduce_rows) {
                    acc_re[j] += tre;
                    acc_im[j] += tim;
                } else {
                    OUT_FX yr = fx_from_code<OUT_FX>(vmac_requantize<OUT_BW, Q_RND, O_SAT>(tre, shift));
                    OUT_FX yi = fx_from_code<OUT_FX>(vmac_requantize<OUT_BW, Q_RND, O_SAT>(tim, shift));
                    y_lane[k] = CXO(yr, yi);
                }
            }
            if (!reduce_rows)
                vmac_out_au::write_array_elem<MEM_BW>(
                    y_lane, mem + (d_addr + i * d_rs + col0) / PF, cols);
        }
    }

    // reduce_rows writeback: one row of n_cols requantized results at the dst.
    if (reduce_rows) {
        for (int col0 = 0; col0 < n_cols; col0 += PF) {
#pragma HLS PIPELINE II=1
            const int cols = (n_cols - col0 < PF) ? (n_cols - col0) : PF;
            CXO y_lane[PF];
#pragma HLS ARRAY_PARTITION variable=y_lane complete dim=1
            for (int k = 0; k < PF; ++k) {
#pragma HLS UNROLL
                const int j = col0 + k;
                if (j >= n_cols) continue;
                OUT_FX yr = fx_from_code<OUT_FX>(vmac_requantize<OUT_BW, Q_RND, O_SAT>(acc_re[j], shift));
                OUT_FX yi = fx_from_code<OUT_FX>(vmac_requantize<OUT_BW, Q_RND, O_SAT>(acc_im[j], shift));
                y_lane[k] = CXO(yr, yi);
            }
            vmac_out_au::write_array_elem<MEM_BW>(
                y_lane, mem + (d_addr + col0) / PF, cols);
        }
    }
}

// Struct-taking wrapper — extracts the command's scalar fields and calls the core.  Used by
// the csim conformance harness (where the by-value struct is fine); the synthesizable top
// calls vmac_compute_core directly to avoid the nested-struct csynth pitfall above.
template <int MEM_BW, int DATA_BW, int INT_BITS, int ACC_BW, int OUT_BW,
          bool Q_RND, bool O_SAT, int MAX_COLS>
void vmac_compute(VmacCmd cmd, ap_uint<MEM_BW>* mem) {
    vmac_compute_core<MEM_BW, DATA_BW, INT_BITS, ACC_BW, OUT_BW, Q_RND, O_SAT, MAX_COLS>(
        mem,
        (int)cmd.n_rows, (int)cmd.n_cols, (bool)cmd.b_one, (bool)cmd.c_zero,
        (bool)cmd.b_conj, (bool)cmd.reduce_rows,
        (int)cmd.a.addr, (int)cmd.a.row_stride, (int)cmd.b.addr, (int)cmd.b.row_stride,
        (int)cmd.c.addr, (int)cmd.c.row_stride, (int)cmd.d.addr, (int)cmd.d.row_stride,
        (bool)cmd.alpha.direct, (int)cmd.alpha.re, (int)cmd.alpha.im,
        (int)cmd.alpha.addr, (int)cmd.alpha.stride,
        (bool)cmd.beta.direct, (int)cmd.beta.re, (int)cmd.beta.im,
        (int)cmd.beta.addr, (int)cmd.beta.stride);
}

}  // namespace vmac_impl
