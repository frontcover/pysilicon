// Hand-written binning hook for the shared_mem (histogram) example.
//
// This is the datapath, lifted verbatim from the inner loop of the hand-written
// hist.cpp (the diff target): zero the counts, then for each sample find its bin
// (number of edges it meets or exceeds) and increment that count.  HLS can't
// return an array by value, so the kernel declares the static count buffer and
// passes it as the `out` out-parameter; this hook fills it in place.
#include "hist.hpp"

namespace hist_impl {

void compute(float data[1024], float edges[32], int ndata, int nbins,
             ap_uint<32> out[32]) {
    for (int i = 0; i < max_nbins; ++i) {
#pragma HLS PIPELINE II=1
        out[i] = 0;
    }

    for (int i = 0; i < ndata; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=max_ndata
        float sample = data[i];
        int bin = 0;

    hist_search:
        for (int b = 0; b < nbins - 1; ++b) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=max_nbins
#pragma HLS PIPELINE II=1
            if (sample >= edges[b])
                bin = b + 1;
        }

        out[bin] = out[bin] + 1;
    }
}

}  // namespace hist_impl
