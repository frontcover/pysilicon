"""Codegen-pipeline tests for the poly example (HlsCodegenStep wiring)."""
from __future__ import annotations

from pathlib import Path


def test_poly_cpp_kernel_name_is_poly():
    """PolyAccelComponent overrides cpp_kernel_name to 'poly' (not 'poly_accel')."""
    from examples.poly.poly import PolyAccelComponent
    from pysilicon.build.hwgen import cpp_kernel_name
    assert cpp_kernel_name(PolyAccelComponent) == "poly"


def test_poly_codegen_step_extracts_and_writes(tmp_path: Path):
    """The gen_kernel step writes three files into <root>/gen/."""
    from examples.poly.poly_build import build_poly_dag
    from pysilicon.build.build import BuildConfig

    dag = build_poly_dag()
    results = dag.run(BuildConfig(root_dir=tmp_path), through="gen_kernel")
    assert results["gen_kernel"].success, results["gen_kernel"].message
    gen_dir = tmp_path / "gen"
    assert (gen_dir / "poly.hpp").exists()
    assert (gen_dir / "poly.cpp").exists()
    assert (gen_dir / "poly_evaluate_impl.tpp").exists()


def test_poly_kernel_signature_has_raw_coeffs_array():
    """CoeffArray uses cpp_storage='raw': signature has 'float coeffs[4]', not 'CoeffArray& coeffs'."""
    from examples.poly.poly import PolyAccelComponent
    from pysilicon.build.hwgen import kernel_signature
    from pysilicon.simulation.simulation import Simulation

    comp = PolyAccelComponent(name="poly", sim=Simulation())
    sig = kernel_signature(comp)
    assert "float coeffs[4]" in sig
    assert "CoeffArray& coeffs" not in sig


def test_poly_codegen_step_kernel_contains_raw_coeffs(tmp_path: Path):
    """End-to-end: generated poly.hpp contains 'coeffs[4]' in the kernel signature."""
    from examples.poly.poly_build import build_poly_dag
    from pysilicon.build.build import BuildConfig

    dag = build_poly_dag()
    results = dag.run(BuildConfig(root_dir=tmp_path), through="gen_kernel")
    assert results["gen_kernel"].success, results["gen_kernel"].message
    hpp = (tmp_path / "gen" / "poly.hpp").read_text()
    assert "float coeffs[4]" in hpp
    assert "CoeffArray& coeffs" not in hpp
