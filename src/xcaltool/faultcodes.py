"""Cummins service fault-code reference.

Imports the published Cummins "Service Fault Codes" spreadsheet (CES 14602) into
a normalized table you can search or export, and read it back as CSV with only
the standard library.

Columns captured per fault code: Cummins fault code, SPN, J1939 FMI, J1587 FMI,
PID, SID, MID, J2012 P-code, lamp colour, lamp device, description.

The ``.xls`` import needs the optional ``xlrd`` package (``pip install xlrd``);
everything else (CSV load/save, lookup) is standard-library only. Import once,
save the CSV, and the app then works from the CSV without xlrd.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, fields
from typing import Dict, List, Optional


FIELDNAMES = [
    "source", "published", "fault_code", "spn", "j1939_fmi", "j1587_fmi",
    "pid", "sid", "mid", "pcode", "lamp_color", "lamp_device", "description",
]


@dataclass
class FaultCode:
    source: str = ""          # "CoreII" / "CoreI"
    published: str = ""
    fault_code: str = ""
    spn: str = ""
    j1939_fmi: str = ""
    j1587_fmi: str = ""
    pid: str = ""
    sid: str = ""
    mid: str = ""
    pcode: str = ""
    lamp_color: str = ""
    lamp_device: str = ""
    description: str = ""


_EMPTY = {"", "not mapped", "none", "n/a", "na"}


def _clean(value) -> str:
    """Normalize a spreadsheet cell to a tidy string.

    Turns Excel floats like ``111.0`` into ``111`` and blanks out placeholder
    values such as ``Not Mapped`` / ``None``.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        text = str(int(value)) if value.is_integer() else str(value)
    else:
        text = str(value).strip()
    if text.lower() in _EMPTY:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


# Header label (lowercased, trimmed) -> FaultCode field.
_HEADER_MAP = {
    "published in ces 14602?": "published",
    "published in ces 14602": "published",
    "cummins fault code": "fault_code",
    "spn": "spn",
    "j1939 fmi": "j1939_fmi",
    "j1587 fmi": "j1587_fmi",
    "pid": "pid",
    "sid": "sid",
    "mid": "mid",
    "j2012 pcode": "pcode",
    "lamp color": "lamp_color",
    "lamp device": "lamp_device",
    "cummins description": "description",
}


def import_xls(path: str) -> List[FaultCode]:
    """Parse a Cummins service-fault-code .xls into FaultCode records.

    Requires the optional ``xlrd`` package.
    """
    try:
        import xlrd  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise RuntimeError(
            "Reading .xls needs the optional 'xlrd' package. Install it with "
            "'pip install xlrd', or convert the sheet to CSV yourself and use "
            "load_csv()."
        ) from exc

    wb = xlrd.open_workbook(path)
    valid = {f.name for f in fields(FaultCode)}
    out: List[FaultCode] = []
    for sheet in wb.sheets():
        if sheet.nrows < 2:
            continue
        header = [_clean(sheet.cell_value(0, c)).lower() for c in range(sheet.ncols)]
        col_to_field = {
            c: _HEADER_MAP[h] for c, h in enumerate(header) if h in _HEADER_MAP
        }
        if "fault_code" not in col_to_field.values():
            continue
        source = "CoreII" if "coreii" in sheet.name.lower() or "new" in sheet.name.lower() else "CoreI"
        for r in range(1, sheet.nrows):
            row = {col_to_field[c]: _clean(sheet.cell_value(r, c))
                   for c in col_to_field}
            fc = row.get("fault_code", "")
            if not fc or fc.lower() == "import test":
                continue
            row = {k: v for k, v in row.items() if k in valid}
            out.append(FaultCode(source=source, **row))
    return out


def to_csv(records: List[FaultCode]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDNAMES)
    w.writeheader()
    for rec in records:
        w.writerow(asdict(rec))
    return buf.getvalue()


def load_csv(path: str) -> List[FaultCode]:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        valid = {f.name for f in fields(FaultCode)}
        return [FaultCode(**{k: (row.get(k) or "") for k in valid}) for row in reader]


def build_index(records: List[FaultCode]) -> Dict[str, FaultCode]:
    """Index by Cummins fault code for quick lookup."""
    return {r.fault_code: r for r in records if r.fault_code}


def lookup(records: List[FaultCode], fault_code: str) -> Optional[FaultCode]:
    return build_index(records).get(str(fault_code))
