"""ECFG -> XDF / CSV conversion.

A Cummins ``.ecfg`` is an XML "Engineering Tool Config File"
(``<Engineering_Tool_Config_File>``) describing every calibration parameter in
a module: name, numeric id, data type, engineering units, min/max, resolution,
table dimensions, enumerations, etc. A single file can hold ~20,000 parameters.

This module parses that XML into a neutral in-memory model
(``Parameter`` / ``Definition``) and exports it as:

  * CSV  -- a flat table of every parameter definition (great for search/review);
  * XDF  -- a TunerPro definition (tables/scalars) skeleton.

Address note
------------
The ``.ecfg`` addresses parameters by **id** (resolved at runtime through the
module's index table), not by a raw byte offset into the ``.bin``. So the CSV is
fully accurate, but the XDF's element addresses are the parameter **ids**: to
make the XDF editable against a specific ``.bin`` in TunerPro you still need the
id -> flash-offset mapping from that module's index table. That resolution step
is deliberately left as a separate feature.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape


class EcfgError(Exception):
    """Raised when an ECFG file cannot be parsed."""


@dataclass
class Parameter:
    """A single calibration parameter (scalar, table, axis, enum, ...)."""

    name: str
    param_id: int = 0
    kind: str = "Fixed_Point"     # ecfg data_type xsi:type
    data_type: str = "uint8"      # resolved numeric type for XDF sizing
    size: int = 1                 # bytes per element (data_length)
    rows: int = 1                 # element_count for tables/arrays
    cols: int = 1
    units: str = ""
    engr_min: Optional[float] = None
    engr_max: Optional[float] = None
    resolution: Optional[float] = None
    scalar_multiplier: Optional[float] = None
    sign: str = "U"               # 'U' or 'S'
    subfile: Optional[str] = None
    itn: Optional[str] = None
    enums: List[Tuple[int, str]] = field(default_factory=list)
    description: str = ""

    @property
    def address(self) -> int:
        """Best available address key: the itn if present, else the id."""
        if self.itn:
            try:
                return int(self.itn, 16)
            except ValueError:
                pass
        return self.param_id

    @property
    def scale(self) -> float:
        if self.resolution:
            return self.resolution
        if self.scalar_multiplier:
            return self.scalar_multiplier
        return 1.0

    @property
    def is_table(self) -> bool:
        return self.rows > 1 or self.cols > 1


@dataclass
class Definition:
    title: str = "Cummins calibration"
    ecm: str = ""
    version: str = ""
    parameters: List[Parameter] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def sniff(data: bytes) -> str:
    head = data[:512].lstrip()
    if head[:5].lower() == b"<?xml" or head[:1] == b"<":
        return "xml"
    return "binary"


_PARAM_RE = re.compile(rb"<parameter\b[^>]*>.*?</parameter>", re.S)
_NAME_RE = re.compile(r'<parameter\s+name="([^"]*)"')
_ID_RE = re.compile(r"<id>(\d+)</id>")
_DTYPE_RE = re.compile(r'<data_type\s+xsi:type="([^"]+)"')
_ELEMTYPE_RE = re.compile(r'<element_type\s+xsi:type="([^"]+)"')
_ELEMCOUNT_RE = re.compile(r"<element_count>(\d+)</element_count>")
_LEN_RE = re.compile(r"<data_length>(\d+)</data_length>")
_UNITS_RE = re.compile(r"<engr_units>([^<]*)</engr_units>")
_MIN_RE = re.compile(r"<engr_min>([^<]*)</engr_min>")
_MAX_RE = re.compile(r"<engr_max>([^<]*)</engr_max>")
_RES_RE = re.compile(r"<min_resolution>([^<]*)</min_resolution>")
_SIGN_RE = re.compile(r"<sign>([^<]*)</sign>")
_MULT_RE = re.compile(r"<scalar_multiplier>([^<]*)</scalar_multiplier>")
_DESC_RE = re.compile(r"<description>(.*?)</description>", re.S)
_SUBFILE_RE = re.compile(r"<subfile>([^<]*)</subfile>")
_ITN_RE = re.compile(r"<itn>([^<]*)</itn>")
_ENUM_RE = re.compile(r'<value\s+numeric_value="(-?\d+)"\s+symbolic_value="([^"]*)"')
_HDR_FIELD = lambda tag, text: (
    m.group(1) if (m := re.search(rf"<{tag}>([^<]*)</{tag}>", text)) else ""
)


def _num(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _xdf_numeric_type(kind: str, size: int, sign: str) -> str:
    if kind == "Floating_Point":
        return "float"
    prefix = "int" if sign == "S" else "uint"
    bits = {1: 8, 2: 16, 4: 32}.get(size, 8)
    return f"{prefix}{bits}"


def _parse_parameter(block: str) -> Optional[Parameter]:
    nm = _NAME_RE.search(block)
    if not nm:
        return None
    name = nm.group(1)
    pid = int(_ID_RE.search(block).group(1)) if _ID_RE.search(block) else 0
    kind = _DTYPE_RE.search(block)
    kind = kind.group(1) if kind else "Unknown"

    rows = 1
    # For tables/arrays/axes the numeric detail lives in element_type.
    cell_len = _LEN_RE.search(block)
    if kind in ("Table", "Array", "X_Axis", "Y_Axis", "Z_Axis", "Contiguous_Structure"):
        ec = _ELEMCOUNT_RE.search(block)
        rows = int(ec.group(1)) if ec else 1
        et = _ELEMTYPE_RE.search(block)
        elem_kind = et.group(1) if et else "Fixed_Point"
    else:
        elem_kind = kind

    size = int(cell_len.group(1)) if cell_len else 1
    sign = _SIGN_RE.search(block)
    sign = sign.group(1) if sign else "U"
    units = _UNITS_RE.search(block)
    desc = _DESC_RE.search(block)
    sub = _SUBFILE_RE.search(block)
    itn = _ITN_RE.search(block)

    return Parameter(
        name=name,
        param_id=pid,
        kind=kind,
        data_type=_xdf_numeric_type(elem_kind, size, sign),
        size=size,
        rows=rows,
        units=(units.group(1) if units else ""),
        engr_min=_num(_MIN_RE.search(block).group(1)) if _MIN_RE.search(block) else None,
        engr_max=_num(_MAX_RE.search(block).group(1)) if _MAX_RE.search(block) else None,
        resolution=_num(_RES_RE.search(block).group(1)) if _RES_RE.search(block) else None,
        scalar_multiplier=_num(_MULT_RE.search(block).group(1)) if _MULT_RE.search(block) else None,
        sign=sign,
        subfile=(sub.group(1) if sub else None),
        itn=(itn.group(1).strip() if itn else None),
        enums=[(int(n), s) for n, s in _ENUM_RE.findall(block)],
        description=(desc.group(1).strip() if desc else ""),
    )


def parse(data: bytes) -> Definition:
    """Parse raw .ecfg bytes into a Definition."""
    if sniff(data) != "xml":
        raise EcfgError("this .ecfg is not XML; unsupported encoding")
    text = data.decode("utf-8", errors="replace")
    header = text[:4000]
    defn = Definition(
        title=_HDR_FIELD("product_id", header) or "Cummins calibration",
        ecm=_HDR_FIELD("module_name", header),
        version=_HDR_FIELD("calibration_version", header),
    )
    for m in _PARAM_RE.finditer(data):
        p = _parse_parameter(m.group(0).decode("utf-8", errors="replace"))
        if p:
            defn.parameters.append(p)
    if not defn.parameters:
        raise EcfgError("no <parameter> definitions found in this .ecfg")
    return defn


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------

def to_csv(defn: Definition) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["name", "id", "id_hex", "kind", "data_type", "size", "rows",
         "units", "engr_min", "engr_max", "resolution", "scalar_multiplier",
         "sign", "subfile", "itn", "enums", "description"]
    )
    for p in defn.parameters:
        enums = "; ".join(f"{n}={s}" for n, s in p.enums)
        writer.writerow(
            [p.name, p.param_id, f"0x{p.param_id:X}", p.kind, p.data_type,
             p.size, p.rows, p.units,
             "" if p.engr_min is None else p.engr_min,
             "" if p.engr_max is None else p.engr_max,
             "" if p.resolution is None else p.resolution,
             "" if p.scalar_multiplier is None else p.scalar_multiplier,
             p.sign, p.subfile or "", p.itn or "", enums,
             " ".join(p.description.split())]
        )
    return buf.getvalue()


_XDF_TYPE_BITS = {"uint8": 8, "int8": 8, "uint16": 16, "int16": 16,
                  "uint32": 32, "int32": 32, "float": 32}


def to_xdf(defn: Definition) -> str:
    """Render a Definition as a TunerPro XDF (XML) skeleton.

    Addresses are the parameter ids (see the module docstring). Scalars become
    XDFCONSTANT, tables/arrays become a single-axis XDFTABLE.
    """
    lines: List[str] = []
    lines.append("<!-- Written by xcaltool from an .ecfg. "
                 "Addresses are parameter IDs; resolve via the module index "
                 "table to edit a specific .bin. -->")
    lines.append('<XDFFORMAT version="1.60">')
    lines.append("  <XDFHEADER>")
    lines.append("    <flags>0x1</flags>")
    lines.append(f"    <deftitle>{escape(defn.title)} {escape(defn.ecm)} "
                 f"{escape(defn.version)}</deftitle>")
    lines.append("    <BASEOFFSET offset=\"0\" subtract=\"0\" />")
    lines.append("  </XDFHEADER>")

    for p in defn.parameters:
        uid = p.param_id & 0xFFFFFF
        bits = _XDF_TYPE_BITS.get(p.data_type, 8)
        math = f'X*{p.scale}' if p.scale != 1.0 else "X"
        if p.is_table:
            lines.append(f'  <XDFTABLE uniqueid="0x{uid:X}">')
            lines.append(f"    <title>{escape(p.name)}</title>")
            if p.description:
                lines.append(f"    <description>{escape(' '.join(p.description.split()))}</description>")
            lines.append('    <XDFAXIS id="z">')
            lines.append(
                f'      <EMBEDDEDDATA mmedaddress="0x{p.address:X}" '
                f'mmedelementsizebits="{bits}" mmedrowcount="{p.rows}" '
                f'mmedcolcount="{p.cols}" />'
            )
            if p.units:
                lines.append(f"      <units>{escape(p.units)}</units>")
            lines.append(f'      <MATH equation="{math}"><VAR id="X" /></MATH>')
            lines.append("    </XDFAXIS>")
            lines.append("  </XDFTABLE>")
        else:
            lines.append(f'  <XDFCONSTANT uniqueid="0x{uid:X}">')
            lines.append(f"    <title>{escape(p.name)}</title>")
            if p.description:
                lines.append(f"    <description>{escape(' '.join(p.description.split()))}</description>")
            lines.append(
                f'    <EMBEDDEDDATA mmedaddress="0x{p.address:X}" '
                f'mmedelementsizebits="{bits}" />'
            )
            if p.units:
                lines.append(f"    <units>{escape(p.units)}</units>")
            lines.append(f'    <MATH equation="{math}"><VAR id="X" /></MATH>')
            lines.append("  </XDFCONSTANT>")

    lines.append("</XDFFORMAT>")
    return "\n".join(lines) + "\n"
