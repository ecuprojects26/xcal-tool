"""DTC (diagnostic trouble code) catalog for swaps and diagnostics.

Given a parsed ``.ecfg`` (see :mod:`xcaltool.ecfg`), this builds a catalog of the
fault-code / diagnostic parameters in a calibration and classifies each by
subsystem, flagging the ones that are **emissions monitors**.

Intended use is diagnostics and legitimate hardware swaps (e.g. auto->manual
transmission, removing a fuel tank/sender, engine swaps) where the ECM logs
codes for hardware that genuinely changed.

Two exports:

  * :func:`to_csv`  -- every DTC/diagnostic parameter, including emissions ones,
    as a **read-only reference** (reading/decoding codes is always fine).
  * :func:`to_xdf`  -- an editable TunerPro map pack that, by default, **excludes
    emissions-related** entries, so it covers configuration/driveline codes only.

This tool deliberately does not disable, mask, or defeat emissions-related DTCs,
monitors, or derates.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import List

from .ecfg import Definition, Parameter, to_xdf as _defn_to_xdf


# A parameter is treated as DTC/diagnostic related if its name or description
# mentions any of these.
_DTC_HINT = re.compile(
    r"fault|\bdtc\b|diagnos|_fc_|\bfc\b|lamp|\bmil\b|derate|trouble|"
    r"malfunction|freeze[_ ]?frame",
    re.I,
)

# Subsystem classification. Order matters: first match wins. Emissions is first
# so anything emissions-related is caught before a looser category.
_SUBSYSTEMS = [
    ("emissions", re.compile(
        r"dpf|egr|scr|\bnox\b|\bdef\b|urea|ammonia|\bnh3\b|aftertreatment|\baft_|"
        r"catalyst|\bcat_|\bdoc\b|\bo2\b|lambda|soot|particulate|regen|dosing|"
        r"exhaust[_ ]?gas|\bpm_|\bmil\b|malfunction[_ ]?indicator|evap|crankcase|"
        r"\bobd\b|tailpipe|emiss", re.I)),
    ("transmission", re.compile(
        r"\btrans\b|transmission|\btcm\b|gearbox|\bgear\b|clutch|\bshift|"
        r"\bpto\b|driveline|transfer[_ ]?case|\btcase\b", re.I)),
    ("fuel_tank", re.compile(
        r"fuel[_ ]?tank|\btank\b|fuel[_ ]?level|fuel[_ ]?lvl|lift[_ ]?pump|"
        r"fuel[_ ]?sender|fuel[_ ]?gauge", re.I)),
    ("driveline", re.compile(
        r"\babs\b|traction|\besp\b|wheel[_ ]?speed|axle|\bcruise\b|\bpto\b", re.I)),
    ("electrical", re.compile(
        r"battery|voltage|\bcan\b|j1939|sensor|circuit|open[_ ]?load|short[_ ]?to",
        re.I)),
]


@dataclass
class DtcEntry:
    name: str
    param_id: int
    kind: str
    data_type: str
    size: int
    rows: int
    subsystem: str
    emissions_related: bool
    address: int
    description: str


def _text(p: Parameter) -> str:
    return f"{p.name} {p.description}"


def is_dtc_param(p: Parameter) -> bool:
    return bool(_DTC_HINT.search(_text(p)))


def classify(p: Parameter) -> str:
    text = _text(p)
    for name, rx in _SUBSYSTEMS:
        if rx.search(text):
            return name
    return "other"


def build_catalog(defn: Definition) -> List[DtcEntry]:
    """Return the DTC/diagnostic parameters of ``defn`` as classified entries."""
    entries: List[DtcEntry] = []
    for p in defn.parameters:
        if not is_dtc_param(p):
            continue
        subsystem = classify(p)
        entries.append(
            DtcEntry(
                name=p.name,
                param_id=p.param_id,
                kind=p.kind,
                data_type=p.data_type,
                size=p.size,
                rows=p.rows,
                subsystem=subsystem,
                emissions_related=(subsystem == "emissions"),
                address=p.address,
                description=" ".join(p.description.split()),
            )
        )
    entries.sort(key=lambda e: (e.subsystem, e.name))
    return entries


def to_csv(entries: List[DtcEntry]) -> str:
    """Read-only reference of every DTC/diagnostic parameter."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "id", "id_hex", "subsystem", "emissions_related",
                "kind", "data_type", "size", "rows", "description"])
    for e in entries:
        w.writerow([e.name, e.param_id, f"0x{e.param_id:X}", e.subsystem,
                    "yes" if e.emissions_related else "no", e.kind,
                    e.data_type, e.size, e.rows, e.description])
    return buf.getvalue()


def to_xdf(defn: Definition, entries: List[DtcEntry],
           include_emissions: bool = False) -> str:
    """Editable TunerPro map pack of DTC parameters.

    By default emissions-related entries are excluded, so the pack covers only
    configuration / driveline codes relevant to hardware swaps.
    """
    keep = {e.name for e in entries if include_emissions or not e.emissions_related}
    subset = Definition(
        title=f"{defn.title} DTC maps",
        ecm=defn.ecm,
        version=defn.version,
        parameters=[p for p in defn.parameters if p.name in keep],
    )
    return _defn_to_xdf(subset)


def summary(entries: List[DtcEntry]) -> str:
    from collections import Counter
    by_sub = Counter(e.subsystem for e in entries)
    emis = sum(1 for e in entries if e.emissions_related)
    lines = [f"Found {len(entries)} DTC/diagnostic parameters:"]
    for sub, n in sorted(by_sub.items()):
        lines.append(f"  {sub:14s} {n}")
    lines.append("")
    lines.append(f"{emis} are emissions-related (excluded from the editable "
                 "XDF pack by default).")
    lines.append(f"{len(entries) - emis} are non-emissions (swap/config/diag).")
    return "\n".join(lines)
