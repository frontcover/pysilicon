"""vivado_report.py — read a Vivado ``report_utilization`` table into resource counters.

The sibling of :mod:`waveflow.utils.csynthparse` for **Vivado** synthesis and implementation, where
the numbers are measured on a netlist rather than estimated by HLS.  ``sv_synth`` / ``sv_impl`` write
``util.rpt`` and :func:`waveflow.build.axi_xbar.synth_axi_xbar` writes one per run; this is what turns
it into the counters a resource record stores (``waveflow.calib.record_store``).

The report is a set of ASCII tables.  Only the ``Site Type | Used | ...`` rows are read, by name, so
the parser is indifferent to the column layout (which moves between Vivado releases) and to the
families' different spellings:

==========================  =========================  ============
UltraScale+                 7-series                   counter
==========================  =========================  ============
``CLB LUTs``                ``Slice LUTs``             ``lut``
``CLB Registers``           ``Slice Registers``        ``ff``
``LUT as Shift Register``   ``LUT as Shift Register``  ``srl``
``LUT as Distributed RAM``  ``LUT as Distributed RAM`` ``lutram``
``Block RAM Tile``          ``Block RAM Tile``         ``bram``
``URAM``                    --                         ``uram``
``DSPs``                    ``DSPs``                   ``dsp``
==========================  =========================  ============

**BRAM is counted in 18K halves**, not in the 36K tiles Vivado reports, so a Vivado record and an
``hls_estimate`` record (``BRAM_18K``) of the same module can be compared counter for counter.

A counter that appears twice (``LUT as Shift Register`` sits under both "LUT as Memory" and a
summary in some releases) keeps its **first** value: the first table is the top-level summary.
"""
from __future__ import annotations

import re

#: Site-type row name (``*`` and surrounding space stripped) -> canonical counter.
UTIL_ROWS: dict[str, str] = {
    "CLB LUTs": "lut", "Slice LUTs": "lut",
    "CLB Registers": "ff", "Slice Registers": "ff",
    "LUT as Shift Register": "srl",
    "LUT as Distributed RAM": "lutram",
    "Block RAM Tile": "bram",
    "URAM": "uram",
    "DSPs": "dsp",
}

_ROW = re.compile(r"^\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<used>[0-9.]+)\s*\|")


def parse_utilization(text: str) -> dict[str, int]:
    """Counters from a ``report_utilization`` text report.

    Returns ``{lut, ff, srl, lutram, bram, uram, dsp}`` for whichever rows the report has; a family
    without a resource (no URAM on a 7-series) simply lacks the key.  Raises ``ValueError`` if the
    text has no LUT row at all -- a report that cannot be read must not become a record of zeros.
    """
    out: dict[str, int] = {}
    for line in text.splitlines():
        m = _ROW.match(line.strip())
        if m is None:
            continue
        name = m.group("name").rstrip("*").strip()
        key = UTIL_ROWS.get(name)
        if key is None or key in out:
            continue
        used = float(m.group("used"))
        out[key] = int(round(used * 2)) if key == "bram" else int(round(used))
    if "lut" not in out:
        raise ValueError("no LUT row in the utilization report; is this a report_utilization file?")
    return out


def parse_markers(text: str, prefix: str = "WF_") -> dict[str, str]:
    """``{NAME: value}`` from ``WF_NAME value`` lines a TCL script printed with ``puts``.

    Values Vivado computes directly (slack, logic levels, the tool version) are printed by the
    script rather than parsed out of a timing report, which is both shorter and immune to the
    report's layout.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            name, _sep, value = line[len(prefix):].partition(" ")
            out[name] = value.strip()
    return out
