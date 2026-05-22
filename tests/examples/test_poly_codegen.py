"""Codegen-pipeline tests for the poly example (HlsCodegenStep wiring)."""
from __future__ import annotations


def test_poly_cpp_kernel_name_is_poly():
    """PolyAccelComponent overrides cpp_kernel_name to 'poly' (not 'poly_accel')."""
    from examples.poly.poly import PolyAccelComponent
    from pysilicon.build.hwgen import cpp_kernel_name
    assert cpp_kernel_name(PolyAccelComponent) == "poly"
