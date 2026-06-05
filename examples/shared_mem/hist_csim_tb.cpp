// hist_csim_tb.cpp — Phase-4 csim driver for the GENERATED histogram kernel.
//
// A robust variant of the hand-written hist_tb.cpp (which is the Phase-5 diff
// target and assumes valid params): it clamps allocation sizes to >= 1 and only
// reads the counts back on a NO_ERROR response, so it can drive validation-
// failure cases (bad ndata/nbins) as well as the happy path — including
// nbins == 1, where the edges read is a zero-count no-op.
#include <fstream>
#include <cstdint>
#include <string>
#include <stdexcept>
#include <sstream>
#include <iterator>

#include "hist.hpp"
#include "include/streamutils_tb.h"
#include "include/memmgr_tb.hpp"
#include "include/float32_array_utils_tb.h"
#include "include/uint32_array_utils_tb.h"

static const int MEM_SIZE = max_mem_words;

int main(int argc, char** argv) {
    const std::string data_dir = (argc > 1) ? argv[1] : "data";

    int tx_id = 0, ndata = 0, nbins = 0;
    {
        std::ifstream params_ifs(data_dir + "/params.json");
        if (!params_ifs) throw std::runtime_error("Failed to open params.json.");
        const std::string js((std::istreambuf_iterator<char>(params_ifs)),
                             std::istreambuf_iterator<char>());
        size_t pos = 0;
        streamutils::json_expect_char(js, pos, '{');
        while (true) {
            streamutils::json_skip_ws(js, pos);
            if (pos < js.size() && js[pos] == '}') break;
            const std::string key = streamutils::json_parse_string(js, pos);
            streamutils::json_expect_char(js, pos, ':');
            const int val = static_cast<int>(streamutils::json_parse_number(js, pos));
            if (key == "tx_id") tx_id = val;
            else if (key == "ndata") ndata = val;
            else if (key == "nbins") nbins = val;
            streamutils::json_skip_ws(js, pos);
            if (pos < js.size() && js[pos] == ',') ++pos;
        }
    }

    // Read inputs (only what fits the static buffers; out-of-range cases are
    // rejected by the kernel before any read anyway).
    static float data_buf[max_ndata] = {};
    static float edge_buf[max_nbins] = {};
    if (ndata > 0 && ndata <= max_ndata)
        float32_array_utils::read_uint32_file_array(data_buf, (data_dir + "/data_array.bin").c_str(), ndata);
    if (nbins > 1 && (nbins - 1) <= max_nbins)
        float32_array_utils::read_uint32_file_array(edge_buf, (data_dir + "/edges_array.bin").c_str(), nbins - 1);

    static mem_word_t mem[MEM_SIZE] = {};
    pysilicon::memmgr::MemMgr<mem_dwidth> mgr(mem, MEM_SIZE);

    // Clamp every region to >= 1 word so MemMgr::alloc never sees a zero size
    // on a validation-failure case (the kernel returns before touching memory).
    auto clamp1 = [](int n) { return n > 0 ? n : 1; };
    const int nwords_data  = clamp1(float32_array_utils::get_nwords<mem_dwidth>(ndata > 0 ? ndata : 1));
    const int nwords_edges = clamp1((nbins > 1) ? float32_array_utils::get_nwords<mem_dwidth>(nbins - 1) : 1);
    const int nwords_count = clamp1(uint32_array_utils::get_nwords<mem_dwidth>(nbins > 0 ? nbins : 1));

    const int data_word_idx  = mgr.alloc(nwords_data);
    const int edge_word_idx  = mgr.alloc(nwords_edges);
    const int count_word_idx = mgr.alloc(nwords_count);

    const int bytes_per_word = mem_dwidth / 8;
    const ap_uint<mem_awidth> data_byte_addr  = data_word_idx  * bytes_per_word;
    const ap_uint<mem_awidth> edge_byte_addr  = edge_word_idx  * bytes_per_word;
    const ap_uint<mem_awidth> count_byte_addr = count_word_idx * bytes_per_word;

    if (ndata > 0 && ndata <= max_ndata)
        float32_array_utils::write_array<mem_dwidth>(data_buf, mem + data_word_idx, ndata);
    if (nbins > 1 && (nbins - 1) <= max_nbins)
        float32_array_utils::write_array<mem_dwidth>(edge_buf, mem + edge_word_idx, nbins - 1);

    HistCmd cmd;
    cmd.tx_id          = tx_id;
    cmd.data_addr      = data_byte_addr;
    cmd.bin_edges_addr = edge_byte_addr;
    cmd.ndata          = ndata;
    cmd.nbins          = nbins;
    cmd.cnt_addr       = count_byte_addr;

    hls::stream<axis_word_t> in_stream;
    hls::stream<axis_word_t> out_stream;
    cmd.write_axi4_stream<stream_dwidth>(in_stream, true);
    hist(in_stream, out_stream, mem);

    HistResp resp;
    streamutils::tlast_status resp_tlast = streamutils::tlast_status::no_tlast;
    resp.read_axi4_stream<stream_dwidth>(out_stream, resp_tlast);

    // Counts are only valid on NO_ERROR; reading them otherwise would index past
    // the (max_nbins-sized) buffer for an out-of-range nbins.
    static ap_uint<32> count_buf[max_nbins] = {};
    const bool ok = (resp.status == HistError::NO_ERROR);
    if (ok)
        uint32_array_utils::read_array<mem_dwidth>(mem + count_word_idx, count_buf, nbins);

    streamutils::write_uint32_file(resp, (data_dir + "/resp_data.bin").c_str());
    uint32_array_utils::write_uint32_file_array(
        count_buf, (data_dir + "/counts_array.bin").c_str(), ok ? nbins : 0);

    return 0;
}
