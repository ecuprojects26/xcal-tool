"""ECFG -> XDF / CSV conversion.

An ``.ecfg`` is (as best we currently know) a Cummins ECM configuration /
parameter-definition file. The goal is to turn it into:

  * an XDF -- TunerPro's XML definition format (tables/scalars + addresses), so
    the matching ``.bin`` can be edited in TunerPro; and
  * a CSV -- a flat list of parameters for quick review in a spreadsheet.

Since the on-disk ``.ecfg`` layout is not documented, this module is written
around a neutral in-memory model (``Parameter`` / ``Definition``). The parser
that fills that model from real ``.ecfg`` bytes is a stub until we get a
sample; everything downstream (XDF + CSV writers) is real and tested against
the in-memory model.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import List, Optional
from xml.sax.saxutils import escape


class EcfgError(Exception):
    """Raised when an ECFG file cannot be parsed."""


@dataclass
class Parameter:
    """A single tunable value or table in a calibration.

    address/size describe where it lives in the raw .bin so TunerPro (via the
    XDF) can read/write it. rows/cols > 1 describe a table.
    """

    name: str
    address: int = 0
    size: int = 1                 # bytes per cell
    rows: int = 1
    cols: int = 1
    data_type: str = "uint8"      # uint8/int8/uint16/int16/uint32/float
    units: str = ""
    scale: float = 1.0
    offset: float = 0.0
    description: str = ""

    @property
    def is_table(self) -> bool:
        return self.rows > 1 or self.cols > 1


@dataclass
class Definition:
    """A whole calibration definition parsed from an .ecfg."""

    title: str = "Cummins calibration"
    ecm: str = ""
    base_offset: int = 0
    parameters: List[Parameter] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing (stub until a real sample is available)
# ---------------------------------------------------------------------------

def sniff(data: bytes) -> str:
    """Return a rough guess of the ecfg encoding: 'xml', 'text', or 'binary'."""
    head = data[:512].lstrip()
    if head[:5].lower() == b"<?xml" or head[:1] == b"<":
        return "xml"
    printable = sum(1 for b in data[:512] if 0x20 <= b < 0x7F or b in (9, 10, 13))
    if data[:512] and printable / len(data[:512]) > 0.85:
        return "text"
    return "binary"


def parse(data: bytes) -> Definition:
    """Parse raw .ecfg bytes into a Definition.

    NOT yet implemented for real files -- awaiting a sample so we can map the
    actual structure. Raises with a clear message rather than guessing.
    """
    raise EcfgError(
        "ECFG parsing is not implemented yet: the .ecfg format needs to be "
        f"reverse-engineered from a real sample (looks like: {sniff(data)}). "
        "The XDF and CSV exporters are ready and will work as soon as the "
        "parser can build a Definition."
    )


# ---------------------------------------------------------------------------
# Exporters (fully implemented; operate on the in-memory Definition)
# ---------------------------------------------------------------------------

_XDF_TYPE_SIZE = {
    "uint8": 1, "int8": 1,
    "uint16": 2, "int16": 2,
    "uint32": 4, "int32": 4, "float": 4,
}


def to_csv(defn: Definition) -> str:
    """Render a Definition as CSV text."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["name", "address", "size", "rows", "cols", "data_type",
         "units", "scale", "offset", "description"]
    )
    for p in defn.parameters:
        writer.writerow(
            [p.name, f"0x{p.address:X}", p.size, p.rows, p.cols, p.data_type,
             p.units, p.scale, p.offset, p.description]
        )
    return buf.getvalue()


def to_xdf(defn: Definition) -> str:
    """Render a Definition as a TunerPro XDF (XML) document.

    This produces a minimal but valid XDF: a header plus one XDFCONSTANT per
    scalar and one XDFTABLE per table. Address math uses each parameter's
    absolute ``address`` (base_offset already applied by the parser).
    """
    lines: List[str] = []
    lines.append('<!-- Written by xcaltool -->')
    lines.append('<XDFFORMAT version="1.60">')
    lines.append('  <XDFHEADER>')
    lines.append('    <flags>0x1</flags>')
    lines.append(f'    <deftitle>{escape(defn.title)}</deftitle>')
    lines.append(f'    <description>{escape(defn.ecm)}</description>')
    lines.append('    <BASEOFFSET offset="0" subtract="0" />')
    lines.append('  </XDFHEADER>')

    uid = 0
    for p in defn.parameters:
        uid += 1
        size_bits = _XDF_TYPE_SIZE.get(p.data_type, p.size) * 8
        if p.is_table:
            lines.append(f'  <XDFTABLE uniqueid="0x{uid:X}">')
            lines.append(f'    <title>{escape(p.name)}</title>')
            if p.description:
                lines.append(f'    <description>{escape(p.description)}</description>')
            lines.append('    <XDFAXIS id="z">')
            lines.append(
                f'      <EMBEDDEDDATA mmedaddress="0x{p.address:X}" '
                f'mmedelementsizebits="{size_bits}" '
                f'mmedrowcount="{p.rows}" mmedcolcount="{p.cols}" />'
            )
            if p.units:
                lines.append(f'      <units>{escape(p.units)}</units>')
            lines.append(
                f'      <MATH equation="X*{p.scale}+{p.offset}">'
                '<VAR id="X" /></MATH>'
            )
            lines.append('    </XDFAXIS>')
            lines.append('  </XDFTABLE>')
        else:
            lines.append(f'  <XDFCONSTANT uniqueid="0x{uid:X}">')
            lines.append(f'    <title>{escape(p.name)}</title>')
            if p.description:
                lines.append(f'    <description>{escape(p.description)}</description>')
            lines.append(
                f'    <EMBEDDEDDATA mmedaddress="0x{p.address:X}" '
                f'mmedelementsizebits="{size_bits}" />'
            )
            if p.units:
                lines.append(f'    <units>{escape(p.units)}</units>')
            lines.append(
                f'    <MATH equation="X*{p.scale}+{p.offset}">'
                '<VAR id="X" /></MATH>'
            )
            lines.append('  </XDFCONSTANT>')

    lines.append('</XDFFORMAT>')
    return "\n".join(lines) + "\n"
