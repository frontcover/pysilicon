"""Tests for ``pysilicon.build.cosim_steps``.

Exercises ``ExtractCosimTimingStep`` end-to-end against the same fixture
the parser tests use — both fixture formats land at the same
``transaction_cycles`` field in the produced JSON.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pysilicon.build.build import BuildConfig
from pysilicon.build.cosim_steps import ExtractCosimTimingStep


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "utils" / "cosim_fixtures"
POLY_RPT = FIXTURE_DIR / "poly_cosim.rpt"
LEGACY_LOG = FIXTURE_DIR / "cosim.log"


def _seed_solution(tmp_path: Path, fixture: Path, target_name: str) -> Path:
    """Copy ``fixture`` into a tmp ``<sol>/sim/report/`` layout and return sol path."""
    sol = tmp_path / "pysilicon_poly_proj" / "solution1"
    report_dir = sol / "sim" / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixture, report_dir / target_name)
    return sol


def test_extract_cosim_timing_2025_rpt(tmp_path):
    """Vitis 2025.1+ report: cycles parsed, vitis_version tag set."""
    sol = _seed_solution(tmp_path, POLY_RPT, "poly_cosim.rpt")
    step = ExtractCosimTimingStep(
        name="extract_cosim_timing",
        top="poly",
        report_dir_artifact="report_dir",
        output_path="results/cosim_timing.json",
    )
    result = step.run(BuildConfig(root_dir=tmp_path), report_dir=sol)
    out_path = result["cosim_timing"]
    assert out_path == tmp_path / "results" / "cosim_timing.json"
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["transaction_cycles"] == 144
    assert data["source"] == "cosim"
    assert data["top"] == "poly"
    assert data["vitis_version"] == "2025.1+"
    assert data["report_path"].endswith("poly_cosim.rpt")


def test_extract_cosim_timing_legacy_log(tmp_path):
    """Legacy cosim.log: same JSON shape, vitis_version tagged as pre-2025."""
    sol = _seed_solution(tmp_path, LEGACY_LOG, "cosim.log")
    step = ExtractCosimTimingStep(
        name="extract_cosim_timing",
        top="poly",
        report_dir_artifact="report_dir",
        output_path="results/cosim_timing.json",
    )
    result = step.run(BuildConfig(root_dir=tmp_path), report_dir=sol)
    data = json.loads(result["cosim_timing"].read_text(encoding="utf-8"))
    assert data["transaction_cycles"] == 110
    assert data["vitis_version"] == "pre-2025"
    assert data["report_path"].endswith("cosim.log")


def test_extract_cosim_timing_missing_report_raises(tmp_path):
    """An empty solution dir yields a FileNotFoundError from the parser
    (the step does not swallow it)."""
    sol = tmp_path / "pysilicon_poly_proj" / "solution1"
    (sol / "sim" / "report").mkdir(parents=True)
    step = ExtractCosimTimingStep(
        name="extract_cosim_timing", top="poly",
        report_dir_artifact="report_dir",
    )
    with pytest.raises(FileNotFoundError):
        step.run(BuildConfig(root_dir=tmp_path), report_dir=sol)


def test_extract_cosim_timing_consumes_produces():
    """Property accessors mirror the constructor parameters."""
    step = ExtractCosimTimingStep(
        name="extract_cosim_timing", top="poly",
        report_dir_artifact="custom_report_dir",
        output_path="custom/path.json",
    )
    assert step.consumes == ["custom_report_dir"]
    assert step.produces == {"cosim_timing": Path("custom/path.json")}
