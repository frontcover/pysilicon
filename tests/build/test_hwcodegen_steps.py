"""Tests for HlsCodegenStep — BuildStep wrapper around HLS codegen."""
from __future__ import annotations

from pathlib import Path

import pytest

from pysilicon.build.build import BuildConfig
from pysilicon.build.hwcodegen_steps import HlsCodegenStep
from pysilicon.build.hwgen import kernel_files_to_str
from pysilicon.simulation.simulation import Simulation
from tests.hw.test_resolve import DemoComponent


# ---------------------------------------------------------------------------
# Phase 1: skeleton + always-overwrite hpp/cpp
# ---------------------------------------------------------------------------

def test_produces_default_output_dir():
    step = HlsCodegenStep(comp_class=DemoComponent, source_artifact="demo_src")
    produces = step.produces
    assert set(produces.keys()) == {"demo_hpp", "demo_cpp", "demo_process_impl"}
    assert produces["demo_hpp"] == Path("demo.hpp")
    assert produces["demo_cpp"] == Path("demo.cpp")
    assert produces["demo_process_impl"] == Path("demo_process_impl.cpp")


def test_produces_with_output_dir():
    step = HlsCodegenStep(
        comp_class=DemoComponent,
        source_artifact="demo_src",
        output_dir="gen",
    )
    produces = step.produces
    assert produces["demo_hpp"] == Path("gen/demo.hpp")
    assert produces["demo_cpp"] == Path("gen/demo.cpp")
    assert produces["demo_process_impl"] == Path("gen/demo_process_impl.cpp")


def test_consumes_is_source_artifact():
    step = HlsCodegenStep(comp_class=DemoComponent, source_artifact="demo_src")
    assert step.consumes == ["demo_src"]


def test_run_writes_hpp_and_cpp(tmp_path: Path):
    step = HlsCodegenStep(comp_class=DemoComponent, source_artifact="demo_src")
    config = BuildConfig(root_dir=tmp_path)
    artifacts = step.run(config)

    hpp = tmp_path / "demo.hpp"
    cpp = tmp_path / "demo.cpp"
    assert hpp.exists()
    assert cpp.exists()

    expected = kernel_files_to_str(DemoComponent(name="_codegen", sim=Simulation()))
    assert hpp.read_text(encoding="utf-8") == expected["demo.hpp"]
    assert cpp.read_text(encoding="utf-8") == expected["demo.cpp"]

    # Artifacts dict contains expected keys and points at the written files.
    assert artifacts["demo_hpp"] == hpp
    assert artifacts["demo_cpp"] == cpp


def test_second_run_rewrites_hpp_and_cpp(tmp_path: Path):
    """Running twice must update the hpp/cpp mtimes (always-overwrite rule)."""
    import os
    step = HlsCodegenStep(comp_class=DemoComponent, source_artifact="demo_src")
    config = BuildConfig(root_dir=tmp_path)
    step.run(config)
    hpp = tmp_path / "demo.hpp"
    cpp = tmp_path / "demo.cpp"

    # Backdate the mtimes so a rewrite shows up clearly.
    old_time = 0.0
    os.utime(hpp, (old_time, old_time))
    os.utime(cpp, (old_time, old_time))

    step.run(config)
    assert hpp.stat().st_mtime > old_time
    assert cpp.stat().st_mtime > old_time


def test_run_creates_output_dir(tmp_path: Path):
    step = HlsCodegenStep(
        comp_class=DemoComponent,
        source_artifact="demo_src",
        output_dir="nested/gen",
    )
    config = BuildConfig(root_dir=tmp_path)
    step.run(config)
    assert (tmp_path / "nested" / "gen" / "demo.hpp").exists()
    assert (tmp_path / "nested" / "gen" / "demo.cpp").exists()


# ---------------------------------------------------------------------------
# Phase 2: sticky impl-file behavior
# ---------------------------------------------------------------------------

def test_first_run_creates_impl_stub(tmp_path: Path):
    step = HlsCodegenStep(comp_class=DemoComponent, source_artifact="demo_src")
    step.run(BuildConfig(root_dir=tmp_path))
    impl = tmp_path / "demo_process_impl.cpp"
    assert impl.exists()
    content = impl.read_text(encoding="utf-8")
    assert "// TODO: implement process" in content


def test_rerun_preserves_user_edited_impl(tmp_path: Path):
    step = HlsCodegenStep(comp_class=DemoComponent, source_artifact="demo_src")
    config = BuildConfig(root_dir=tmp_path)
    step.run(config)

    impl = tmp_path / "demo_process_impl.cpp"
    custom = "// hand-written implementation, do not overwrite\n"
    impl.write_text(custom, encoding="utf-8")

    step.run(config)
    assert impl.read_text(encoding="utf-8") == custom


def test_rerun_does_not_touch_existing_impl_mtime(tmp_path: Path):
    """A second run must not even rewrite identical contents (mtime is preserved)."""
    import os
    step = HlsCodegenStep(comp_class=DemoComponent, source_artifact="demo_src")
    config = BuildConfig(root_dir=tmp_path)
    step.run(config)

    impl = tmp_path / "demo_process_impl.cpp"
    backdated = 1_000_000_000.0
    os.utime(impl, (backdated, backdated))

    step.run(config)
    assert impl.stat().st_mtime == backdated


# ---------------------------------------------------------------------------
# Phase 3: DAG integration + freshness skipping
# ---------------------------------------------------------------------------

def _make_dag_with_source(tmp_path: Path):
    """Build a DAG with a real SourceStep + HlsCodegenStep against ``tmp_path``."""
    from pysilicon.build.build import BuildDag, SourceStep

    src = tmp_path / "demo_source.py"
    src.write_text("# placeholder source\n", encoding="utf-8")

    dag = BuildDag()
    dag.add(SourceStep(artifact="demo_src", path="demo_source.py"))
    dag.add(HlsCodegenStep(
        comp_class=DemoComponent,
        source_artifact="demo_src",
        output_dir="gen",
    ))
    return dag, src


def test_dag_run_writes_all_three_files(tmp_path: Path):
    dag, _src = _make_dag_with_source(tmp_path)
    results = dag.run(BuildConfig(root_dir=tmp_path))

    for name, result in results.items():
        assert result.success, f"{name} failed: {result.message}"

    gen = tmp_path / "gen"
    assert (gen / "demo.hpp").exists()
    assert (gen / "demo.cpp").exists()
    assert (gen / "demo_process_impl.cpp").exists()


def test_dag_second_run_skips_step(tmp_path: Path):
    dag, _src = _make_dag_with_source(tmp_path)
    config = BuildConfig(root_dir=tmp_path)
    dag.run(config)
    results = dag.run(config)
    codegen = results["HlsCodegenStep"]
    assert codegen.success
    assert codegen.skipped is True


def test_dag_source_touch_invalidates_step(tmp_path: Path):
    import os
    dag, src = _make_dag_with_source(tmp_path)
    config = BuildConfig(root_dir=tmp_path)
    dag.run(config)

    # Touch the source forward so the produced files look stale relative to it.
    future = src.stat().st_mtime + 10.0
    os.utime(src, (future, future))

    results = dag.run(config)
    assert results["HlsCodegenStep"].skipped is False


def test_dag_rebuild_preserves_impl_under_cascade(tmp_path: Path):
    """Even when the .hpp/.cpp are re-generated on cascade, impl file is sticky."""
    import os
    dag, src = _make_dag_with_source(tmp_path)
    config = BuildConfig(root_dir=tmp_path)
    dag.run(config)

    impl = tmp_path / "gen" / "demo_process_impl.cpp"
    custom = "// user-edited content\n"
    impl.write_text(custom, encoding="utf-8")

    future = src.stat().st_mtime + 10.0
    os.utime(src, (future, future))

    results = dag.run(config)
    assert results["HlsCodegenStep"].skipped is False
    assert impl.read_text(encoding="utf-8") == custom


def test_dag_force_reruns_step_but_impl_stays_sticky(tmp_path: Path):
    dag, _src = _make_dag_with_source(tmp_path)
    config = BuildConfig(root_dir=tmp_path)
    dag.run(config)

    impl = tmp_path / "gen" / "demo_process_impl.cpp"
    custom = "// user-edited\n"
    impl.write_text(custom, encoding="utf-8")

    results = dag.run(config, force=True)
    assert results["HlsCodegenStep"].skipped is False
    assert impl.read_text(encoding="utf-8") == custom
