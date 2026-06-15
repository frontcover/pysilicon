// Phase 1a lane-method conformance testbench (template).
//
// Exercises the regime-agnostic *_lane methods over all three interfaces with the SAME
// canonical loop (step by LW = lane_capacity<W>()), for both the vectorized regime (pf >= 1)
// and the wide-element regime (pf == 0, one element spanning ceil(elem/W) words/beats):
//
//   memory : read_array_lane  -> identity -> write_array_lane   (the file round-trip below)
//   fifo   : write_stream_lane -> read_stream_lane
//   axi4   : write_axi4_stream_lane -> read_axi4_stream_lane
//
// The memory path's reconstructed words are written to the out file and compared, bit-exact,
// against the Python golden (arrayutils.write_array) by the pytest driver. The fifo / axi4
// paths are checked in-sim to reproduce the same words; any divergence returns non-zero so the
// csim (and the test) fails.
#include <fstream>
#include <iostream>
#include <vector>

#include <hls_stream.h>

#include "__HEADER__"

namespace au = __NAMESPACE__;

// Canonical MEMORY lane loop: decode all N elements via read_array_lane (running word pointer
// advanced by get_nwords<W>(LW) each step), into a flat element buffer.
template <int W>
static void mem_lane_read(const ap_uint<W>* words, au::value_type* dst, int N) {
    constexpr int LW = au::lane_capacity<W>();
    constexpr int WPU = au::get_nwords<W>(LW);
    const ap_uint<W>* xp = words;
    for (int i = 0; i < N; i += LW) {
        const int n = (N - i < LW) ? (N - i) : LW;
        au::value_type lane[LW];
        au::read_array_lane<W>(xp, lane, n);
        for (int k = 0; k < LW; ++k) {
            if (k < n) dst[i + k] = lane[k];
        }
        xp += WPU;
    }
}

// Canonical MEMORY lane loop: encode all N elements via write_array_lane.
template <int W>
static void mem_lane_write(const au::value_type* src, ap_uint<W>* words, int N) {
    constexpr int LW = au::lane_capacity<W>();
    constexpr int WPU = au::get_nwords<W>(LW);
    ap_uint<W>* yp = words;
    for (int i = 0; i < N; i += LW) {
        const int n = (N - i < LW) ? (N - i) : LW;
        au::value_type lane[LW];
        for (int k = 0; k < LW; ++k) {
            if (k < n) lane[k] = src[i + k];
        }
        au::write_array_lane<W>(lane, yp, n);
        yp += WPU;
    }
}

// FIFO lane loop: src elements -> write_stream_lane -> stream -> read_stream_lane -> dst.
template <int W>
static void fifo_lane_roundtrip(const au::value_type* src, au::value_type* dst, int N) {
    constexpr int LW = au::lane_capacity<W>();
    hls::stream<ap_uint<W>> s;
    for (int i = 0; i < N; i += LW) {
        const int n = (N - i < LW) ? (N - i) : LW;
        au::value_type lane[LW];
        for (int k = 0; k < LW; ++k) {
            if (k < n) lane[k] = src[i + k];
        }
        au::write_stream_lane<W>(lane, s, n);
    }
    for (int i = 0; i < N; i += LW) {
        const int n = (N - i < LW) ? (N - i) : LW;
        au::value_type lane[LW];
        au::read_stream_lane<W>(s, lane, n);
        for (int k = 0; k < LW; ++k) {
            if (k < n) dst[i + k] = lane[k];
        }
    }
}

// AXI4-Stream lane loop: src -> write_axi4_stream_lane -> stream -> read_axi4_stream_lane -> dst.
template <int W>
static void axi4_lane_roundtrip(const au::value_type* src, au::value_type* dst, int N) {
    constexpr int LW = au::lane_capacity<W>();
    hls::stream<streamutils::axi4s_word<W>> s;
    for (int i = 0; i < N; i += LW) {
        const int n = (N - i < LW) ? (N - i) : LW;
        const bool tlast = (i + LW >= N);
        au::value_type lane[LW];
        for (int k = 0; k < LW; ++k) {
            if (k < n) lane[k] = src[i + k];
        }
        au::write_axi4_stream_lane<W>(lane, s, tlast, n);
    }
    for (int i = 0; i < N; i += LW) {
        const int n = (N - i < LW) ? (N - i) : LW;
        au::value_type lane[LW];
        streamutils::tlast_status tl = streamutils::tlast_status::no_tlast;
        au::read_axi4_stream_lane<W>(s, lane, n, tl);
        for (int k = 0; k < LW; ++k) {
            if (k < n) dst[i + k] = lane[k];
        }
    }
}

int main(int argc, char** argv) {
    const char* in_words_path = (argc > 1) ? argv[1] : "array_words.txt";
    const char* out_words_path = (argc > 2) ? argv[2] : "array_words_out.txt";

    std::ifstream in_words(in_words_path);
    if (!in_words) {
        std::cerr << "Failed to open input words file: " << in_words_path << std::endl;
        return 1;
    }

    std::vector<ap_uint<__WORD_BW__>> words;
    unsigned long long raw = 0;
    while (in_words >> raw) {
        words.push_back((ap_uint<__WORD_BW__>)raw);
    }
    if ((int)words.size() != __NWORDS__) {
        std::cerr << "Unexpected word count: got " << words.size()
                  << ", expected " << __NWORDS__ << std::endl;
        return 1;
    }

    constexpr int N = __ARRAY_LEN__;

    // Decode the golden words into elements via the memory lane loop.
    au::value_type elems[N];
    mem_lane_read<__WORD_BW__>(words.data(), elems, N);

    // (1) Memory: re-encode via the lane loop; this is the golden-compared output.
    ap_uint<__WORD_BW__> out_words[__NWORDS__];
    mem_lane_write<__WORD_BW__>(elems, out_words, N);

    // (2) FIFO round-trip -> re-encode -> must match the memory words.
    au::value_type fifo_elems[N];
    fifo_lane_roundtrip<__WORD_BW__>(elems, fifo_elems, N);
    ap_uint<__WORD_BW__> fifo_words[__NWORDS__];
    mem_lane_write<__WORD_BW__>(fifo_elems, fifo_words, N);

    // (3) AXI4-Stream round-trip -> re-encode -> must match the memory words.
    au::value_type axi_elems[N];
    axi4_lane_roundtrip<__WORD_BW__>(elems, axi_elems, N);
    ap_uint<__WORD_BW__> axi_words[__NWORDS__];
    mem_lane_write<__WORD_BW__>(axi_elems, axi_words, N);

    for (int i = 0; i < __NWORDS__; ++i) {
        if (fifo_words[i] != out_words[i]) {
            std::cerr << "FIFO lane mismatch at word " << i << std::endl;
            return 2;
        }
        if (axi_words[i] != out_words[i]) {
            std::cerr << "AXI4 lane mismatch at word " << i << std::endl;
            return 3;
        }
    }

    std::ofstream out(out_words_path);
    if (!out) {
        std::cerr << "Failed to open output words file: " << out_words_path << std::endl;
        return 1;
    }
    for (int i = 0; i < __NWORDS__; ++i) {
        out << static_cast<unsigned long long>(out_words[i]) << "\n";
    }
    return 0;
}
