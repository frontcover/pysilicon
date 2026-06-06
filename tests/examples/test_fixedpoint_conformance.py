"""Phase 4 milestone: Python FixedField == Vitis ap_fixed, bit-exact.

Non-Vitis tests check the Python golden + source generation. The ``-m vitis`` test
is the conformance proof: per curated config, Vitis C-sim quantizes the edge-value
sweep through ``ap_fixed<W,I,Q,O>`` and the emitted bits must equal the Python
``fixputils`` bits with **zero LSB disagreement**.

A failed csim is a real failure here — we only skip when Vitis is not installed at
all (never soft-skip a csim error into a pass).
"""
import json

import pytest

from examples.schemas.fixedpoint.fixedpoint_build import (
    CURATED_CONFIGS, conformance_for_config, conformance_values, expected_bits,
    gen_config_sources,
)
from pysilicon.toolchain import toolchain
from pysilicon.utils.fixputils import AP_RND, AP_SAT, AP_TRN, AP_WRAP


def test_curated_set_covers_all_v1_modes_and_signedness():
    modes = {(c.q_mode, c.o_mode) for c in CURATED_CONFIGS}
    assert modes == {(AP_TRN, AP_WRAP), (AP_RND, AP_WRAP), (AP_TRN, AP_SAT), (AP_RND, AP_SAT)}
    assert any(not c.signed for c in CURATED_CONFIGS)   # unsigned (ap_ufixed) covered
    assert any(c.W - c.int_bits == 0 for c in CURATED_CONFIGS)   # F=0 (overflow-only) covered


def test_gen_sources_writes_kernel_with_field_type_and_golden(tmp_path):
    cfg = next(c for c in CURATED_CONFIGS if c.name == "s8_4_rnd_sat")
    d = gen_config_sources(cfg, tmp_path / cfg.name)
    cpp = (d / "quantize_tb.cpp").read_text(encoding="utf-8")
    assert cfg.cpp_type in cpp                          # the FixedField type drives the kernel
    assert "ap_fixed<8, 4, AP_RND, AP_SAT>" == cfg.cpp_type
    exp = json.loads((d / "expected.json").read_text(encoding="utf-8"))
    assert exp["expected_bits"] == expected_bits(cfg, conformance_values(cfg))
    assert len(exp["values"]) == len(exp["expected_bits"])


@pytest.mark.vitis
@pytest.mark.parametrize("cfg", CURATED_CONFIGS, ids=lambda c: c.name)
def test_python_fixedfield_matches_vitis_ap_fixed_bit_exact(tmp_path, cfg):
    if not toolchain.find_vitis_path():
        pytest.skip("Vitis installation not found; cannot run bit-exact conformance.")
    result = conformance_for_config(cfg, tmp_path)
    assert result["count_ok"], (
        f"{cfg.name} ({cfg.cpp_type}): Vitis emitted a different number of bits "
        f"than the {result['n_values']}-value sweep.")
    assert result["exact"], (
        f"{cfg.name} ({cfg.cpp_type}): {len(result['mismatches'])} LSB disagreement(s) "
        f"between Python fixputils and Vitis ap_fixed — the Python model is wrong, "
        f"fix fixputils (do NOT loosen). First few: {result['mismatches'][:5]}")
