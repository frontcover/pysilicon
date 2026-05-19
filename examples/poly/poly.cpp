#include "poly.hpp"


static float eval_poly_horner(const float coeff[4], float x) {
#pragma HLS INLINE
    float y = coeff[3];
    y = y * x + coeff[2];
    y = y * x + coeff[1];
    y = y * x + coeff[0];
    return y;
}

// Persistent kernel. The host configures `coeffs` over AXI-Lite, writes
// `ap_start`, and the kernel reads command headers from `in_stream` until
// it sees an END header (clean exit) or hits a framing/protocol error
// (halt-on-error: latch halted/error_code/tx_id_status and return).
//
// The host re-launches the kernel via platform reset (`ap_rst_n`) plus a
// fresh `ap_start` after observing `halted == 1`.
void poly(hls::stream<axis_word_t>& in_stream,
          hls::stream<axis_word_t>& out_stream,
          const float coeffs[4],
          ap_uint<1>& halted,
          ap_uint<8>& error_code,
          ap_uint<16>& tx_id_status) {
#pragma HLS INTERFACE axis port=in_stream
#pragma HLS INTERFACE axis port=out_stream
#pragma HLS INTERFACE s_axilite port=coeffs       bundle=control
#pragma HLS INTERFACE s_axilite port=halted       bundle=control
#pragma HLS INTERFACE s_axilite port=error_code   bundle=control
#pragma HLS INTERFACE s_axilite port=tx_id_status bundle=control
#pragma HLS INTERFACE s_axilite port=return       bundle=control

    ap_uint<1>  local_halted = 0;
    ap_uint<8>  local_error  = (ap_uint<8>)static_cast<unsigned int>(PolyError::NO_ERROR);
    ap_uint<16> local_tx_id  = 0;

    static const int pf = float32_array_utils::pf<WORD_BW>();
    float x_lane[pf];
    float y_lane[pf];
#pragma HLS ARRAY_PARTITION variable=x_lane complete dim=1
#pragma HLS ARRAY_PARTITION variable=y_lane complete dim=1

    while (true) {
        // ------------------------------------------------------------------
        // Read the next command header.
        // ------------------------------------------------------------------
        PolyCmdHdr cmd_hdr;
        streamutils::tlast_status cmd_hdr_tlast = streamutils::tlast_status::no_tlast;
        cmd_hdr.read_axi4_stream<WORD_BW>(in_stream, cmd_hdr_tlast);

        // END command: clean exit, no response emitted.
        if (cmd_hdr.cmd_type == PolyCmdType::END) {
            break;
        }

        // Validate framing of the command-header burst.
        if (cmd_hdr_tlast == streamutils::tlast_status::tlast_early) {
            local_halted = 1;
            local_error  = (ap_uint<8>)static_cast<unsigned int>(PolyError::TLAST_EARLY_CMD_HDR);
            local_tx_id  = cmd_hdr.tx_id;
            break;
        }
        if (cmd_hdr_tlast == streamutils::tlast_status::no_tlast) {
            local_halted = 1;
            local_error  = (ap_uint<8>)static_cast<unsigned int>(PolyError::NO_TLAST_CMD_HDR);
            local_tx_id  = cmd_hdr.tx_id;
            break;
        }

        // ------------------------------------------------------------------
        // Emit the response header.
        // ------------------------------------------------------------------
        PolyRespHdr resp_hdr;
        resp_hdr.tx_id = cmd_hdr.tx_id;
        resp_hdr.write_axi4_stream<WORD_BW>(out_stream, true);

        // ------------------------------------------------------------------
        // Process the sample burst lane-by-lane.
        // ------------------------------------------------------------------
        int nsamp_read = 0;
        streamutils::tlast_status samp_in_tlast = streamutils::tlast_status::no_tlast;
        bool read_done = false;
        for (int i = 0; i < cmd_hdr.nsamp && !read_done; i += pf) {
            const int nrem = cmd_hdr.nsamp - i;
            const int lane_count = (nrem < pf) ? nrem : pf;
            streamutils::tlast_status lane_tlast = streamutils::tlast_status::no_tlast;
            float32_array_utils::read_axi4_stream_elem<WORD_BW>(
                in_stream, x_lane, lane_tlast, nrem);

            for (int k = 0; k < pf; ++k) {
#pragma HLS UNROLL
                if (k < lane_count) {
                    y_lane[k] = eval_poly_horner(coeffs, x_lane[k]);
                }
            }

            const bool out_tlast = (nrem <= pf);
            float32_array_utils::write_axi4_stream_elem<WORD_BW>(
                out_stream, y_lane, out_tlast, nrem);

            nsamp_read += lane_count;
            if (lane_tlast == streamutils::tlast_status::tlast_at_end) {
                samp_in_tlast = out_tlast ? streamutils::tlast_status::tlast_at_end
                                          : streamutils::tlast_status::tlast_early;
                read_done = true;
            }
        }

        // ------------------------------------------------------------------
        // Validate framing of the sample burst and halt on error.
        // No flush on framing errors: the host issues ap_rst_n via the
        // platform reset controller before re-launching.
        // ------------------------------------------------------------------
        if (samp_in_tlast == streamutils::tlast_status::tlast_early) {
            local_halted = 1;
            local_error  = (ap_uint<8>)static_cast<unsigned int>(PolyError::TLAST_EARLY_SAMP_IN);
            local_tx_id  = cmd_hdr.tx_id;
            break;
        }
        if (samp_in_tlast == streamutils::tlast_status::no_tlast) {
            local_halted = 1;
            local_error  = (ap_uint<8>)static_cast<unsigned int>(PolyError::NO_TLAST_SAMP_IN);
            local_tx_id  = cmd_hdr.tx_id;
            break;
        }
        if (nsamp_read != cmd_hdr.nsamp) {
            local_halted = 1;
            local_error  = (ap_uint<8>)static_cast<unsigned int>(PolyError::WRONG_NSAMP);
            local_tx_id  = cmd_hdr.tx_id;
            break;
        }
    }

    halted       = local_halted;
    error_code   = local_error;
    tx_id_status = local_tx_id;
}
