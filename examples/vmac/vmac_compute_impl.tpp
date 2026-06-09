// vmac_compute_impl.tpp
// Included from gen/vmac.hpp. Types declared there (VmacCmd, the operand
// array-utils namespace) are in scope. Do not include this file directly
// except via the .hpp.
//
// Hand-written body for the templated `vmac_impl::vmac_compute` hook — the
// single VMAC datapath: the fused op
//
//     D = alpha * A * op(B) + beta * C   [, reduced over rows]
//
// over a row-major shared-memory image reached through the `m_axi` pointer.
// This is the C++ contract of `VmacAccel.vmac_compute` (whose Python body is
// the golden `VmacAccel.execute`); it is bit-identical to that golden by
// construction (full-precision intermediates in the wide accumulator, a single
// lossy requantize = the right-shift + round + saturate).
//
// Lanes via the existing packing infra (not reinvented), exactly as
// examples/stream_inband/poly_evaluate_impl.tpp does for streams — here the
// m_axi variant: `pf<MEM_BW>()` contiguous elements per memory word, lane
// arrays with `#pragma HLS ARRAY_PARTITION` + `UNROLL`.  The packed (inner)
// dimension is the **columns** (unit stride, row-major), so each m_axi word
// carries `pf` contiguous columns:
//
//   * real    element = DATA_BW bits        -> pf = MEM_BW / DATA_BW lanes
//   * complex element = 2*DATA_BW bits       -> pf = MEM_BW / (2*DATA_BW) lanes
//                                               (re in the low DATA_BW bits,
//                                                im in the high DATA_BW bits)
//
// so real gets 2x the lanes of complex (the Phase-4 throughput 2x).
//
// The ap_fixed types are the Phase-2 numeric contract, taken straight from the
// component's format methods (the compile-time template params below):
//
//   A_T   = ap_fixed<DATA_BW, INT_BITS>                  (VmacAccel._in_fmt)
//   ACC_T = ap_fixed<ACC_W, ACC_I>                       (accumulator_format)
//   OUT_T = ap_fixed<OUT_W, OUT_I, QMODE, OMODE>         (output_format)
//
// Complex uses the **explicit re/im formula** (ar*br - ai*bi, ar*bi + ai*br) on
// A_T/ACC_T values — *not* std::complex operator* (which would quantize the
// product back to A_T); conj(B) negates the imag component (its +1 integer bit
// of growth is already in ACC_T via conj_format).  reduce_rows is a per-column
// reduction (sum over rows -> one value per column): a per-column ACC_T
// accumulator, outer loop over rows, inner loop over the contiguous (packed)
// columns — the GEMM accumulation pattern, not a strided down-column read.

namespace vmac_impl {

// Reconstruct an A_T operand from DATA_BW packed bits at [lo, lo+DATA_BW).
template <typename A_T, int DATA_BW>
static inline A_T recon(const ap_uint<DATA_BW>& bits) {
#pragma HLS INLINE
    A_T v;
    v.range(DATA_BW - 1, 0) = bits;
    return v;
}

// One lane's fused term, full precision (no rounding): real path.
//   t = alpha * (b_one ? a : a*b) + (c_zero ? 0 : beta * c)
template <typename A_T, typename ACC_T, bool B_ONE, bool C_ZERO>
static inline ACC_T term_real(A_T a, A_T b, A_T c, A_T alpha, A_T beta) {
#pragma HLS INLINE
    ACC_T ab = B_ONE ? (ACC_T)a : (ACC_T)(a * b);
    ACC_T t = (ACC_T)(alpha * ab);
    if (!C_ZERO) t += (ACC_T)(beta * c);
    return t;
}

// One lane's fused term, full precision: complex path (explicit re/im).
// conj(B) negates the imag of B before the multiply (B_CONJ).
template <typename A_T, typename ACC_T, bool B_ONE, bool C_ZERO, bool B_CONJ>
static inline void term_complex(
    A_T are, A_T aim, A_T bre, A_T bim, A_T cre, A_T cim,
    A_T alre, A_T alim, A_T bere, A_T beim,
    ACC_T& tre, ACC_T& tim) {
#pragma HLS INLINE
    // op(B): identity, or conj(B) = (bre, -bim)
    A_T obim = B_CONJ ? (A_T)(-bim) : bim;
    // A * op(B)  (explicit re/im; full precision into ACC_T)
    ACC_T abre, abim;
    if (B_ONE) {
        abre = (ACC_T)are;
        abim = (ACC_T)aim;
    } else {
        abre = (ACC_T)(are * bre) - (ACC_T)(aim * obim);
        abim = (ACC_T)(are * obim) + (ACC_T)(aim * bre);
    }
    // alpha * (A*op(B))
    tre = (ACC_T)(alre * abre) - (ACC_T)(alim * abim);
    tim = (ACC_T)(alre * abim) + (ACC_T)(alim * abre);
    // + beta * C
    if (!C_ZERO) {
        tre += (ACC_T)(bere * cre) - (ACC_T)(beim * cim);
        tim += (ACC_T)(bere * cim) + (ACC_T)(beim * cre);
    }
}

// The compute hook.  Template params are the compile-time numeric contract +
// datapath flags (from VmacAccel's HwParams + accumulator_format /
// output_format + the cmd flags); the runtime VmacCmd carries the region
// addresses / pitches, the matrix shape, and the alpha/beta operands.  `mem` is
// the m_axi word pointer (pf operand elements per ap_uint<MEM_BW> word); region
// rows are word-aligned (addr and row_stride are multiples of pf), so the
// contiguous columns of a row are the lanes of consecutive words.
// The single lossy step is the requantize `OUT_T y = acc`, an ap_fixed assignment:
// OUT_T's binary point is F_out = F_acc - SHIFT (output_format), so aligning the
// ACC_T value to OUT_T drops SHIFT fractional bits with round (QMODE) + saturate
// (OMODE) — SHIFT is therefore *encoded in* OUT_I (= OUT_W - F_out) and needs no
// explicit `>> SHIFT` (that would double-count it).
template <
    int MEM_BW, int DATA_BW, int INT_BITS,
    int ACC_W, int ACC_I, int OUT_W, int OUT_I,
    int MAX_COLS,
    bool COMPLEX, bool B_ONE, bool C_ZERO, bool B_CONJ, bool REDUCE_ROWS,
    ap_q_mode QMODE, ap_o_mode OMODE>
void vmac_compute(VmacCmd cmd, ap_uint<MEM_BW>* mem) {
    typedef ap_fixed<DATA_BW, INT_BITS, AP_TRN, AP_WRAP> A_T;
    typedef ap_fixed<ACC_W, ACC_I, AP_TRN, AP_WRAP> ACC_T;     // full precision (no loss)
    typedef ap_fixed<OUT_W, OUT_I, QMODE, OMODE> OUT_T;        // the single lossy requantize

    // element bits / packing factor (the lane count) — real 1 component, complex 2.
    const int EB = COMPLEX ? (2 * DATA_BW) : DATA_BW;
    const int PF = MEM_BW / EB;

    const int n_rows = (int)cmd.n_rows;
    const int n_cols = (int)cmd.n_cols;
    const int a_addr = (int)cmd.a.addr,  a_rs = (int)cmd.a.row_stride;
    const int b_addr = (int)cmd.b.addr,  b_rs = (int)cmd.b.row_stride;
    const int c_addr = (int)cmd.c.addr,  c_rs = (int)cmd.c.row_stride;
    const int d_addr = (int)cmd.d.addr,  d_rs = (int)cmd.d.row_stride;

    // alpha / beta: direct immediates (re/im) or indirect per-column pointers.
    const bool al_direct = (bool)cmd.alpha.direct;
    const bool be_direct = (bool)cmd.beta.direct;
    A_T al_re_imm = recon<A_T, DATA_BW>((ap_uint<DATA_BW>)cmd.alpha.re);
    A_T al_im_imm = recon<A_T, DATA_BW>((ap_uint<DATA_BW>)cmd.alpha.im);
    A_T be_re_imm = recon<A_T, DATA_BW>((ap_uint<DATA_BW>)cmd.beta.re);
    A_T be_im_imm = recon<A_T, DATA_BW>((ap_uint<DATA_BW>)cmd.beta.im);

    // read pf contiguous lanes (columns col0..col0+pf-1 of row i) of one operand
    // word into a lane array; im[] is meaningful only in COMPLEX mode.
    // elem address = base + i*pitch + col0 ; word index = that / PF (row-aligned).
#define VMAC_READ_LANES(NAME, BASE, PITCH)                                      \
    A_T NAME##_re[PF];                                                          \
    A_T NAME##_im[PF];                                                          \
    _Pragma("HLS ARRAY_PARTITION variable=" #NAME "_re complete dim=1")         \
    _Pragma("HLS ARRAY_PARTITION variable=" #NAME "_im complete dim=1")         \
    {                                                                           \
        ap_uint<MEM_BW> w = mem[((BASE) + i * (PITCH) + col0) / PF];            \
        for (int k = 0; k < PF; ++k) {                                          \
            _Pragma("HLS UNROLL")                                               \
            int lo = k * EB;                                                    \
            NAME##_re[k] = recon<A_T, DATA_BW>(w.range(lo + DATA_BW - 1, lo));  \
            if (COMPLEX)                                                        \
                NAME##_im[k] = recon<A_T, DATA_BW>(                             \
                    w.range(lo + 2 * DATA_BW - 1, lo + DATA_BW));               \
        }                                                                       \
    }

    // per-column accumulators (REDUCE_ROWS): one ACC_T per column, summed over rows.
    ACC_T acc_re[MAX_COLS];
    ACC_T acc_im[MAX_COLS];
#pragma HLS ARRAY_PARTITION variable=acc_re cyclic factor=16 dim=1
#pragma HLS ARRAY_PARTITION variable=acc_im cyclic factor=16 dim=1
    if (REDUCE_ROWS) {
        for (int j = 0; j < n_cols; ++j) {
#pragma HLS PIPELINE II=1
            acc_re[j] = 0;
            acc_im[j] = 0;
        }
    }

    // outer loop over rows (strided by the pitch), inner over contiguous columns
    // packed PF/word — the GEMM accumulation pattern.
    for (int i = 0; i < n_rows; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=MAX_COLS
        for (int col0 = 0; col0 < n_cols; col0 += PF) {
#pragma HLS PIPELINE II=1
            VMAC_READ_LANES(a, a_addr, a_rs)
            VMAC_READ_LANES(b, b_addr, b_rs)
            VMAC_READ_LANES(c, c_addr, c_rs)

            ap_uint<MEM_BW> dw = 0;
            for (int k = 0; k < PF; ++k) {
#pragma HLS UNROLL
                const int j = col0 + k;
                if (j >= n_cols) continue;

                // per-column alpha/beta: indirect reads one lane per column.
                A_T alre = al_re_imm, alim = al_im_imm;
                A_T bere = be_re_imm, beim = be_im_imm;
                if (!al_direct) {
                    ap_uint<MEM_BW> aw = mem[((int)cmd.alpha.addr + j * (int)cmd.alpha.stride) / PF];
                    int la = (((int)cmd.alpha.addr + j * (int)cmd.alpha.stride) % PF) * EB;
                    alre = recon<A_T, DATA_BW>(aw.range(la + DATA_BW - 1, la));
                    if (COMPLEX) alim = recon<A_T, DATA_BW>(aw.range(la + 2 * DATA_BW - 1, la + DATA_BW));
                }
                if (!be_direct) {
                    ap_uint<MEM_BW> bw = mem[((int)cmd.beta.addr + j * (int)cmd.beta.stride) / PF];
                    int lb = (((int)cmd.beta.addr + j * (int)cmd.beta.stride) % PF) * EB;
                    bere = recon<A_T, DATA_BW>(bw.range(lb + DATA_BW - 1, lb));
                    if (COMPLEX) beim = recon<A_T, DATA_BW>(bw.range(lb + 2 * DATA_BW - 1, lb + DATA_BW));
                }

                ACC_T tre, tim = 0;
                if (COMPLEX) {
                    term_complex<A_T, ACC_T, B_ONE, C_ZERO, B_CONJ>(
                        a_re[k], a_im[k], b_re[k], b_im[k], c_re[k], c_im[k],
                        alre, alim, bere, beim, tre, tim);
                } else {
                    tre = term_real<A_T, ACC_T, B_ONE, C_ZERO>(
                        a_re[k], b_re[k], c_re[k], alre, bere);
                }

                if (REDUCE_ROWS) {
                    acc_re[j] += tre;
                    if (COMPLEX) acc_im[j] += tim;
                } else {
                    // requantize this lane (the single lossy step) and pack into dst word.
                    OUT_T yr = tre;
                    int lo = k * EB;
                    dw.range(lo + OUT_W - 1, lo) = yr.range(OUT_W - 1, 0);
                    if (COMPLEX) {
                        OUT_T yi = tim;
                        dw.range(lo + DATA_BW + OUT_W - 1, lo + DATA_BW) = yi.range(OUT_W - 1, 0);
                    }
                }
            }
            if (!REDUCE_ROWS)
                mem[(d_addr + i * d_rs + col0) / PF] = dw;
        }
    }

    // REDUCE_ROWS writeback: one row of n_cols requantized results at the dst.
    if (REDUCE_ROWS) {
        for (int col0 = 0; col0 < n_cols; col0 += PF) {
#pragma HLS PIPELINE II=1
            ap_uint<MEM_BW> dw = 0;
            for (int k = 0; k < PF; ++k) {
#pragma HLS UNROLL
                const int j = col0 + k;
                if (j >= n_cols) continue;
                int lo = k * EB;
                OUT_T yr = acc_re[j];
                dw.range(lo + OUT_W - 1, lo) = yr.range(OUT_W - 1, 0);
                if (COMPLEX) {
                    OUT_T yi = acc_im[j];
                    dw.range(lo + DATA_BW + OUT_W - 1, lo + DATA_BW) = yi.range(OUT_W - 1, 0);
                }
            }
            mem[(d_addr + col0) / PF] = dw;
        }
    }

#undef VMAC_READ_LANES
}

}  // namespace vmac_impl
