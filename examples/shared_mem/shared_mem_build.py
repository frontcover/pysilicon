"""Build helpers for the codegen-driven ``shared_mem`` (histogram) example.

Generates the Vitis HLS include headers (reusing ``HistTest.gen_vitis_code``) and
the **generated** kernel (``gen/hist.cpp`` / ``gen/hist.hpp``) from the Python
``HistAccel``, writes per-case input vectors, and exposes the golden expectation
(status + counts) — the pieces the Phase-4 C-sim test drives.  The generated
kernel is compiled by ``run_gen.tcl`` against the hand-written datapath hooks and
the robust ``hist_csim_tb.cpp`` driver.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from pysilicon.build.hwgen import header_to_cpp, kernel_to_cpp
from pysilicon.hw.arrayutils import read_uint32_file, write_uint32_file

try:
    from examples.shared_mem.hist import HistAccel, golden_counts
    from examples.shared_mem.hist_demo import (
        Float32, HistError, HistResp, HistTest, MAX_NBINS, MAX_NDATA, Uint32Field,
    )
except ModuleNotFoundError:  # direct execution from the example dir
    from hist import HistAccel, golden_counts  # type: ignore[no-redef]
    from hist_demo import (  # type: ignore[no-redef]
        Float32, HistError, HistResp, HistTest, MAX_NBINS, MAX_NDATA, Uint32Field,
    )


def generate_vitis_sources(work_dir: str | Path) -> Path:
    """Generate ``include/`` headers and the generated ``gen/hist.{cpp,hpp}``.

    Returns the ``gen`` directory.  The include headers come from
    ``HistTest.gen_vitis_code`` (the proven path); the kernel + header are
    generated from :class:`HistAccel`.
    """
    from pysilicon.build.build import BuildConfig, BuildDag
    from pysilicon.build.streamutils import MemMgrStep

    work_dir = Path(work_dir)
    HistTest(example_dir=work_dir).gen_vitis_code()   # streams + schemas + array-utils
    # gen_vitis_code omits the memory manager; the generated header + TB include
    # include/memmgr.hpp / memmgr_tb.hpp, so generate those too.
    mm_dag = BuildDag()
    mm_dag.add(MemMgrStep(output_dir="include"))
    mm_dag.run(BuildConfig(root_dir=work_dir))
    gen = work_dir / "gen"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "hist.cpp").write_text(kernel_to_cpp(HistAccel), encoding="utf-8")
    (gen / "hist.hpp").write_text(header_to_cpp(HistAccel), encoding="utf-8")
    return gen


@dataclass
class HistCase:
    """One C-sim coverage case + its golden expectation."""

    ndata: int
    nbins: int
    seed: int = 3

    def gen_data(self) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
        rng = np.random.default_rng(self.seed)
        data = (rng.normal(0.0, 1.25, size=max(self.ndata, 0)).astype(np.float32)
                if self.ndata > 0 else np.zeros(0, np.float32))
        edges = np.sort(
            rng.uniform(-2.5, 2.5, size=max(self.nbins - 1, 0)).astype(np.float32)
        )
        return data, edges

    @property
    def expected_status(self) -> HistError:
        # Mirrors HistAccel.validate (and the hand-written hist.cpp).
        if self.ndata <= 0 or self.ndata > MAX_NDATA:
            return HistError.INVALID_NDATA
        if self.nbins <= 0 or self.nbins > MAX_NBINS:
            return HistError.INVALID_NBINS
        return HistError.NO_ERROR

    def write_inputs(self, data_dir: str | Path) -> tuple[np.ndarray, np.ndarray]:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        data, edges = self.gen_data()
        (data_dir / "params.json").write_text(
            json.dumps({"tx_id": self.seed, "ndata": self.ndata, "nbins": self.nbins}),
            encoding="utf-8",
        )
        if self.ndata > 0:
            write_uint32_file(data, elem_type=Float32,
                              file_path=data_dir / "data_array.bin", nwrite=self.ndata)
        if self.nbins > 1:
            write_uint32_file(edges, elem_type=Float32,
                              file_path=data_dir / "edges_array.bin", nwrite=len(edges))
        return data, edges

    def check_outputs(self, data_dir: str | Path, data, edges) -> tuple[bool, str]:
        """Compare the C-sim outputs against the golden; returns (passed, detail)."""
        data_dir = Path(data_dir)
        resp = HistResp().read_uint32_file(str(data_dir / "resp_data.bin"))
        if int(resp.status) != int(self.expected_status):
            return False, (f"status {int(resp.status)} != expected "
                           f"{int(self.expected_status)}")
        if self.expected_status != HistError.NO_ERROR:
            return True, f"status={int(resp.status)} (expected error)"
        counts = np.asarray(
            read_uint32_file(str(data_dir / "counts_array.bin"),
                             elem_type=Uint32Field, shape=self.nbins),
            dtype=np.uint32,
        )
        gold = golden_counts(data, edges, self.nbins)
        if not np.array_equal(counts, gold):
            return False, f"counts {counts.tolist()} != golden {gold.tolist()}"
        return True, f"counts={counts.tolist()} match golden"


# The Phase-4 coverage set: nbins==1 (unconditional zero-count edges read),
# nbins>1 with several bins (normal binning), and a validation-failure case.
CSIM_CASES = [
    HistCase(ndata=37, nbins=1),
    HistCase(ndata=37, nbins=6),
    HistCase(ndata=200, nbins=12),
    HistCase(ndata=37, nbins=0),    # INVALID_NBINS
]
