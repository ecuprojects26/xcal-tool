"""Session / diagnostic report helpers.

Builds a plain-text service report from an identify result plus stored codes,
decodes a VIN into its standard fields, and computes integrity hashes over a
flash image so a saved backup can be verified later.
"""

from __future__ import annotations

import datetime
import hashlib
import zlib
from typing import Dict, List, Optional

from .comms import DtcResult, EcuInfo

# ISO-3779 model-year codes (position 10). Repeats on a 30-year cycle.
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


def decode_vin(vin: str) -> Dict[str, str]:
    """Decode the standard fixed fields of a 17-char VIN. Returns an empty dict
    for anything that isn't a plausible 17-character VIN."""
    vin = (vin or "").strip().upper()
    out: Dict[str, str] = {}
    if len(vin) != 17:
        return out
    out["wmi"] = vin[:3]
    out["vds"] = vin[3:9]
    out["vis"] = vin[9:]
    year_char = vin[9]
    if year_char in _YEAR_CODES:
        base = 1980 + _YEAR_CODES.index(year_char)
        # 30-year ambiguity; pick the most recent year not in the future.
        this_year = datetime.date.today().year
        while base + 30 <= this_year:
            base += 30
        out["model_year"] = str(base)
    out["plant"] = vin[10]
    out["serial"] = vin[11:]
    out["valid_check_digit"] = "yes" if _check_digit_ok(vin) else "no"
    return out


# Standard VIN transliteration table (letters -> digit values; I,O,Q unused).
_TRANSLIT: Dict[str, int] = {}
for _c in "0123456789":
    _TRANSLIT[_c] = int(_c)
for _v, _chars in {
    1: "AJ", 2: "BKS", 3: "CLT", 4: "DMU", 5: "ENV",
    6: "FW", 7: "GPX", 8: "HQY", 9: "RZ",
}.items():
    for _c in _chars:
        _TRANSLIT[_c] = _v
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def _check_digit_ok(vin: str) -> bool:
    try:
        total = sum(_TRANSLIT[ch] * w for ch, w in zip(vin, _WEIGHTS))
    except KeyError:
        return False
    rem = total % 11
    expected = "X" if rem == 10 else str(rem)
    return vin[8] == expected


def image_hashes(image: bytes) -> Dict[str, str]:
    """Integrity fingerprints for a flash image / backup."""
    return {
        "size": str(len(image)),
        "crc32": f"{zlib.crc32(image) & 0xFFFFFFFF:08X}",
        "sha256": hashlib.sha256(image).hexdigest(),
    }


def build_report(info: EcuInfo,
                 active: Optional[List[DtcResult]] = None,
                 previously_active: Optional[List[DtcResult]] = None,
                 image: Optional[bytes] = None) -> str:
    """Assemble a human-readable service report string."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "=" * 56,
        " xcaltool -- ECU service report",
        f" Generated: {now}",
        "=" * 56,
        "",
        "ECU DATA TAG",
        "-" * 56,
    ]
    tag = [
        ("VIN", info.vin),
        ("ESN", info.serial),
        ("ECU CODE", info.ecm_code),
        ("SW VERSION", info.calibration_id),
        ("ECU PN", info.part_number),
        ("ENGINE CPL", info.cpl),
        ("ENGINE HP", info.rated_hp),
        ("ENGINE TQ", info.rated_torque),
        ("MAKE/MODEL", f"{info.make} {info.model}".strip()),
    ]
    for label, value in tag:
        lines.append(f"  {label:<12}: {value or '-'}")

    vin_info = decode_vin(info.vin)
    if vin_info:
        lines += ["", "VIN DECODE", "-" * 56]
        lines.append(f"  WMI         : {vin_info.get('wmi', '-')}")
        lines.append(f"  Model year  : {vin_info.get('model_year', '-')}")
        lines.append(f"  Plant       : {vin_info.get('plant', '-')}")
        lines.append(f"  Serial      : {vin_info.get('serial', '-')}")
        lines.append(f"  Check digit : {vin_info.get('valid_check_digit', '-')}")

    lines += ["", "FAULT CODES", "-" * 56]
    lines.append(f"  Active            : {len(active or [])}")
    for d in active or []:
        lines.append(f"    {_fmt_dtc(d)}")
    lines.append(f"  Previously active : {len(previously_active or [])}")
    for d in previously_active or []:
        lines.append(f"    {_fmt_dtc(d)}")

    if image is not None:
        h = image_hashes(image)
        lines += ["", "IMAGE / BACKUP INTEGRITY", "-" * 56]
        lines.append(f"  Size   : {int(h['size']):,} bytes")
        lines.append(f"  CRC32  : {h['crc32']}")
        lines.append(f"  SHA256 : {h['sha256']}")

    lines.append("")
    return "\n".join(lines)


def _fmt_dtc(d: DtcResult) -> str:
    return d.label()
