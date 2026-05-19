#include <fstream>
#include <cstdint>
#include <string>
#include <stdexcept>

#include "poly.hpp"
#include "include/float32_array_utils_tb.h"
#include "include/streamutils_tb.h"

int main(int argc, char** argv) {
    const std::string data_dir = (argc > 1) ? argv[1] : "data";

    // -----------------------------------------------------------------------
    // Load AXI-Lite coefficient configuration from coeffs.bin.
    // -----------------------------------------------------------------------
    float coeffs[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    float32_array_utils::read_uint32_file_array(
        coeffs, (data_dir + "/coeffs.bin").c_str(), 4);

    // -----------------------------------------------------------------------
    // Load the DATA command header and its sample payload, plus the END
    // command header used to terminate the kernel's persistent loop.
    // -----------------------------------------------------------------------
    PolyCmdHdr data_hdr;
    streamutils::read_uint32_file(data_hdr, (data_dir + "/data_cmd_hdr.bin").c_str());
    const int nsamp = data_hdr.nsamp;

    float samp_in [MAX_NSAMP] = {};
    float samp_out[MAX_NSAMP] = {};
    float32_array_utils::read_uint32_file_array(
        samp_in, (data_dir + "/samp_in_data.bin").c_str(), nsamp);

    PolyCmdHdr end_hdr;
    streamutils::read_uint32_file(end_hdr, (data_dir + "/end_cmd_hdr.bin").c_str());

    // -----------------------------------------------------------------------
    // Queue all input bursts before calling the kernel.  C-sim runs the
    // kernel as a synchronous function call, so the input stream must be
    // fully populated before invocation.
    // -----------------------------------------------------------------------
    hls::stream<axis_word_t> in_stream;
    hls::stream<axis_word_t> out_stream;

    data_hdr.write_axi4_stream<WORD_BW>(in_stream, true);

    static const int pf = float32_array_utils::pf<WORD_BW>();
    for (int i = 0; i < nsamp; i += pf) {
        const int nrem = nsamp - i;
        const bool tlast = (nrem <= pf);
        float32_array_utils::write_axi4_stream_elem<WORD_BW>(
            in_stream, samp_in + i, tlast, nrem);
    }

    end_hdr.write_axi4_stream<WORD_BW>(in_stream, true);

    // -----------------------------------------------------------------------
    // AXI-Lite output scalars.  Vitis HLS emits each s_axilite-bound scalar
    // at the ABI level as a reference argument; C-sim passes them by
    // reference and reads them back after the kernel returns.
    // -----------------------------------------------------------------------
    ap_uint<1>  halted       = 0;
    ap_uint<8>  error_code   = 0;
    ap_uint<16> tx_id_status = 0;

    poly(in_stream, out_stream, coeffs, halted, error_code, tx_id_status);

    // -----------------------------------------------------------------------
    // Drain the response stream.  For the single-DATA-transaction shape we
    // expect exactly one (resp_hdr, samp_out) pair; the END header emits no
    // response.
    // -----------------------------------------------------------------------
    PolyRespHdr resp_hdr;
    streamutils::tlast_status resp_hdr_tlast = streamutils::tlast_status::no_tlast;
    resp_hdr.read_axi4_stream<WORD_BW>(out_stream, resp_hdr_tlast);

    streamutils::tlast_status samp_out_tlast = streamutils::tlast_status::no_tlast;
    float32_array_utils::read_axi4_stream<WORD_BW>(
        out_stream, samp_out, samp_out_tlast, nsamp);

    streamutils::write_uint32_file(resp_hdr, (data_dir + "/resp_hdr_data.bin").c_str());
    float32_array_utils::write_uint32_file_array(
        samp_out, (data_dir + "/samp_out_data.bin").c_str(), nsamp);

    // -----------------------------------------------------------------------
    // Emit regmap_status.json with the final AXI-Lite status values, in the
    // schema ValidateCSimStep expects: { halted, error, tx_id }.
    // -----------------------------------------------------------------------
    std::ofstream status_ofs(data_dir + "/regmap_status.json");
    if (!status_ofs) {
        throw std::runtime_error("Failed to open regmap_status.json for writing.");
    }
    status_ofs
        << "{\n"
        << "  \"halted\": " << (int)halted << ",\n"
        << "  \"error\": "  << (int)error_code << ",\n"
        << "  \"tx_id\": "  << (int)tx_id_status << "\n"
        << "}\n";

    return 0;
}
