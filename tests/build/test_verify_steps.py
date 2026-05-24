"""Tests for ``pysilicon/build/verify_steps.py``.

Phase 5 of the HwTestbench codegen project introduces a generic
``FunctionalVerifyStep`` that replaces the poly-specific
``ValidateCSimStep``.  These tests exercise the three comparator
shapes (schema, array, json) plus the report-writing and failure paths.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

from pysilicon.build.build import BuildConfig
from pysilicon.build.verify_steps import FunctionalVerifyStep
from examples.poly.poly import Float32, PolyCmdHdr, PolyRespHdr


pytestmark = pytest.mark.phase5


def _write_cmd_hdr(path: Path, cmd_type: int = 0, tx_id: int = 0, nsamp: int = 3) -> None:
    hdr = PolyCmdHdr()
    hdr.cmd_type = cmd_type
    hdr.tx_id = tx_id
    hdr.nsamp = nsamp
    hdr.write_uint32_file(path)


def _write_resp_hdr(path: Path, tx_id: int = 0) -> None:
    hdr = PolyRespHdr()
    hdr.tx_id = tx_id
    hdr.write_uint32_file(path)


def _write_float32_array(path: Path, values) -> None:
    """Write a flat float32 sample array in the same uint32-packed format the
    codegen toolchain emits — one float per uint32 word."""
    arr = np.asarray(values, dtype=np.float32)
    raw_words = arr.view(np.uint32).astype("<u4")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_words.tofile(path)


def _write_status(path: Path, halted: int = 0, error: int = 0, tx_id: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"halted": halted, "error": error, "tx_id": tx_id}),
        encoding="utf-8",
    )


def test_functional_verify_passes_on_matching_outputs(tmp_path):
    """A clean comparison between identical golden/actual passes and emits
    a ``pass=true`` report at the configured location."""
    golden = tmp_path / "golden"
    actual = tmp_path / "actual"
    golden.mkdir()
    actual.mkdir()

    cmd_hdr_path = tmp_path / "cmd_hdr.bin"
    _write_cmd_hdr(cmd_hdr_path, nsamp=3)

    _write_resp_hdr(golden / "resp_hdr.bin", tx_id=7)
    _write_resp_hdr(actual / "resp_hdr_data.bin", tx_id=7)
    _write_float32_array(golden / "samp_out.bin", [1.0, 2.0, 3.0])
    _write_float32_array(actual / "samp_out_data.bin", [1.0, 2.0, 3.0])
    _write_status(golden / "regmap_status.json")
    _write_status(actual / "regmap_status.json")

    step = FunctionalVerifyStep(
        name="poly_verify",
        golden_dir_artifact="sim_dir",
        actual_dir_artifact="csim_dir",
        extra_artifacts=["data_cmd_hdr"],
        schemas=[
            {"filename": "resp_hdr_data.bin",
             "golden_filename": "resp_hdr.bin", "schema": PolyRespHdr},
        ],
        arrays=[
            {"filename": "samp_out_data.bin",
             "golden_filename": "samp_out.bin",
             "elem_type": Float32,
             "count_from_extra": "data_cmd_hdr",
             "count_schema": PolyCmdHdr,
             "count_field": "nsamp"},
        ],
        jsons=[
            {"filename": "regmap_status.json",
             "expect_zero": ["halted", "error"]},
        ],
        report_path="verify_report.json",
    )
    result = step.run(
        BuildConfig(root_dir=tmp_path),
        sim_dir=golden,
        csim_dir=actual,
        data_cmd_hdr=cmd_hdr_path,
    )
    report_path = result["verify_report"]
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["pass"] is True
    kinds = [c["kind"] for c in report["checks"]]
    assert kinds == ["schema", "array", "json"]


def test_functional_verify_array_mismatch_raises(tmp_path):
    """Array values that exceed atol/rtol cause a RuntimeError and the
    report records a non-pass entry for the failing file."""
    golden = tmp_path / "g"
    actual = tmp_path / "a"
    golden.mkdir()
    actual.mkdir()
    cmd_hdr_path = tmp_path / "cmd_hdr.bin"
    _write_cmd_hdr(cmd_hdr_path, nsamp=2)
    _write_resp_hdr(golden / "resp_hdr.bin")
    _write_resp_hdr(actual / "resp_hdr_data.bin")
    _write_float32_array(golden / "samp_out.bin", [1.0, 2.0])
    _write_float32_array(actual / "samp_out_data.bin", [1.0, 99.0])  # mismatch
    _write_status(golden / "regmap_status.json")
    _write_status(actual / "regmap_status.json")

    step = FunctionalVerifyStep(
        name="poly_verify_fail",
        golden_dir_artifact="sim_dir",
        actual_dir_artifact="csim_dir",
        extra_artifacts=["data_cmd_hdr"],
        schemas=[
            {"filename": "resp_hdr_data.bin",
             "golden_filename": "resp_hdr.bin", "schema": PolyRespHdr},
        ],
        arrays=[
            {"filename": "samp_out_data.bin",
             "golden_filename": "samp_out.bin",
             "elem_type": Float32,
             "count_from_extra": "data_cmd_hdr",
             "count_schema": PolyCmdHdr,
             "count_field": "nsamp"},
        ],
        jsons=[
            {"filename": "regmap_status.json",
             "expect_zero": ["halted", "error"]},
        ],
        report_path="verify_report.json",
    )
    with pytest.raises(RuntimeError, match="array mismatch"):
        step.run(
            BuildConfig(root_dir=tmp_path),
            sim_dir=golden, csim_dir=actual, data_cmd_hdr=cmd_hdr_path,
        )
    report = json.loads((tmp_path / "verify_report.json").read_text(encoding="utf-8"))
    assert report["pass"] is False


def test_functional_verify_status_nonzero_fails(tmp_path):
    """A regmap_status.json with non-zero error/halted fails the check
    even when schema + array comparisons would pass."""
    golden = tmp_path / "g"
    actual = tmp_path / "a"
    golden.mkdir()
    actual.mkdir()
    cmd_hdr_path = tmp_path / "cmd_hdr.bin"
    _write_cmd_hdr(cmd_hdr_path, nsamp=1)
    _write_resp_hdr(golden / "resp_hdr.bin")
    _write_resp_hdr(actual / "resp_hdr_data.bin")
    _write_float32_array(golden / "samp_out.bin", [1.0])
    _write_float32_array(actual / "samp_out_data.bin", [1.0])
    _write_status(golden / "regmap_status.json")
    _write_status(actual / "regmap_status.json", halted=1, error=5)

    step = FunctionalVerifyStep(
        name="poly_verify_status",
        golden_dir_artifact="sim_dir",
        actual_dir_artifact="csim_dir",
        extra_artifacts=["data_cmd_hdr"],
        schemas=[
            {"filename": "resp_hdr_data.bin",
             "golden_filename": "resp_hdr.bin", "schema": PolyRespHdr},
        ],
        arrays=[
            {"filename": "samp_out_data.bin",
             "golden_filename": "samp_out.bin",
             "elem_type": Float32,
             "count_from_extra": "data_cmd_hdr",
             "count_schema": PolyCmdHdr,
             "count_field": "nsamp"},
        ],
        jsons=[
            {"filename": "regmap_status.json",
             "expect_zero": ["halted", "error"]},
        ],
        report_path="verify_report.json",
    )
    with pytest.raises(RuntimeError, match="halted"):
        step.run(
            BuildConfig(root_dir=tmp_path),
            sim_dir=golden, csim_dir=actual, data_cmd_hdr=cmd_hdr_path,
        )


def test_functional_verify_copies_actual_to_output_dir(tmp_path):
    """With ``output_dir`` set, the actual outputs are mirrored into that
    directory so downstream tooling can find them in one place."""
    golden = tmp_path / "g"
    actual = tmp_path / "a"
    golden.mkdir()
    actual.mkdir()
    cmd_hdr_path = tmp_path / "cmd_hdr.bin"
    _write_cmd_hdr(cmd_hdr_path, nsamp=1)
    _write_resp_hdr(golden / "resp_hdr.bin")
    _write_resp_hdr(actual / "resp_hdr_data.bin")
    _write_float32_array(golden / "samp_out.bin", [1.0])
    _write_float32_array(actual / "samp_out_data.bin", [1.0])
    _write_status(golden / "regmap_status.json")
    _write_status(actual / "regmap_status.json")

    step = FunctionalVerifyStep(
        name="poly_verify_mirror",
        golden_dir_artifact="sim_dir",
        actual_dir_artifact="csim_dir",
        extra_artifacts=["data_cmd_hdr"],
        schemas=[
            {"filename": "resp_hdr_data.bin",
             "golden_filename": "resp_hdr.bin", "schema": PolyRespHdr},
        ],
        arrays=[
            {"filename": "samp_out_data.bin",
             "golden_filename": "samp_out.bin",
             "elem_type": Float32,
             "count_from_extra": "data_cmd_hdr",
             "count_schema": PolyCmdHdr,
             "count_field": "nsamp"},
        ],
        jsons=[
            {"filename": "regmap_status.json",
             "expect_zero": ["halted", "error"]},
        ],
        output_dir="results/vitis",
        output_artifact="vitis_dir",
        report_path="verify_report.json",
    )
    result = step.run(
        BuildConfig(root_dir=tmp_path),
        sim_dir=golden, csim_dir=actual, data_cmd_hdr=cmd_hdr_path,
    )
    out_dir = result["vitis_dir"]
    assert out_dir == tmp_path / "results" / "vitis"
    assert (out_dir / "resp_hdr_data.bin").exists()
    assert (out_dir / "samp_out_data.bin").exists()
    assert (out_dir / "regmap_status.json").exists()
