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
