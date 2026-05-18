#ifndef POLY_HPP
#define POLY_HPP

#include <ap_int.h>
#include <hls_stream.h>

#include "include/poly_error.h"
#include "include/poly_cmd_type.h"
#include "include/coeff_array.h"
#include "include/poly_cmd_hdr.h"
#include "include/poly_resp_hdr.h"
#include "include/float32_array_utils.h"
#include "include/streamutils_hls.h"


static const int WORD_BW = 32;
// To build a 64-bit variant, set WORD_BW to 64.
static_assert(WORD_BW == 32 || WORD_BW == 64, "WORD_BW must be 32 or 64");

using axis_word_t = streamutils::axi4s_word<WORD_BW>;

static const int MAX_NSAMP = 128;

void poly(hls::stream<axis_word_t>& in_stream,
          hls::stream<axis_word_t>& out_stream,
          const float coeffs[4],
          ap_uint<1>& halted,
          ap_uint<8>& error_code,
          ap_uint<16>& tx_id_status);

#endif
