// Hand-written response hook for the shared_mem (histogram) example.
//
// Mirrors the response writes in the hand-written hist.cpp (the diff target):
// build a HistResp echoing the transaction id with the given status and emit it
// on the output AXI4-Stream with TLAST.  Templated on the stream width, so it is
// #include'd from hist.hpp (like poly's templated hooks).
#include "include/hist_resp.h"

namespace hist_impl {

template <int out_bw>
void respond(hls::stream<streamutils::axi4s_word<out_bw>>& m_out,
             int tx_id, ap_uint<8> status) {
    HistResp resp;
    resp.tx_id = tx_id;
    // status arrives as ap_uint<8> (the codegen's enum carrier); HistResp.status
    // is the scoped HistError enum, so convert back through the integer value.
    resp.status = static_cast<HistError>(static_cast<unsigned int>(status));
    resp.write_axi4_stream<out_bw>(m_out, true);
}

}  // namespace hist_impl
