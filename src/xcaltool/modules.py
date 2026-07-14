"""Cummins ECM module profiles.

Each profile describes the flash memory regions the tool reads/writes for a
given controller family and how a full read maps back to a raw ``.bin`` image.

IMPORTANT: the region addresses/sizes below are starting profiles based on the
observed EFILive/xcal layouts (byte-exact for CM24xx) and public J1939 memory
ranges. Older families (CM870/CM871/CM2250/CM2350) still need per-ECU
verification against a known-good read before writing. Nothing here bypasses
Cummins security -- a read/write still requires an authorized SecurityProvider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Region:
    """A contiguous flash region to transfer, and where it lands in the image."""
    name: str
    address: int          # ECU flash address (source of the DM14 read/write)
    size: int             # bytes
    image_offset: int     # offset in the assembled raw .bin


@dataclass(frozen=True)
class ModuleProfile:
    key: str
    name: str
    description: str
    protocol: str                 # default protocol for this family
    image_size: int               # size of the assembled raw .bin
    regions: List[Region] = field(default_factory=list)

    def total_bytes(self) -> int:
        return sum(r.size for r in self.regions)


# Region layouts. CM24xx mirrors the verified xcal->bin mapping used by the
# converter; the calibration bank dominates the transfer. Older families use a
# single calibration-block profile pending a verified full-flash map.

_CM2450 = ModuleProfile(
    key="CM2450",
    name="CM2450 (X15 / ISX15 2017+)",
    description="Bosch MD1, 29-bit J1939 @ 250k. Verified xcal/bin layout.",
    protocol="j1939",
    image_size=0x844000,
    regions=[
        Region("boot/app", 0x1080, 0xFF78, 0x1080),
        Region("app2", 0x11000, 0xD1E0, 0x11000),
        Region("calibration", 0x840000, 0x474BE0, 0x80000),
        Region("id_block_a", 0x1000000, 0x1600, 0x840000),
        Region("id_block_b", 0x2000000, 0x10, 0x842000),
    ],
)

_CM2350 = ModuleProfile(
    key="CM2350",
    name="CM2350 (ISX15 2013-2016 / ISB 2013+)",
    description="J1939 @ 250k. Calibration profile; verify full map before write.",
    protocol="j1939",
    image_size=0x400000,
    regions=[Region("calibration", 0x800000, 0x400000, 0x0)],
)

_CM2250 = ModuleProfile(
    key="CM2250",
    name="CM2250 (ISX15 2010-2012)",
    description="J1939 @ 250k. Calibration profile; verify full map before write.",
    protocol="j1939",
    image_size=0x400000,
    regions=[Region("calibration", 0x800000, 0x400000, 0x0)],
)

_CM871 = ModuleProfile(
    key="CM871",
    name="CM871 (ISX 2007-2009)",
    description="J1939 @ 250k (also J1587). Calibration profile; verify before write.",
    protocol="j1939",
    image_size=0x200000,
    regions=[Region("calibration", 0x100000, 0x200000, 0x0)],
)

_CM870 = ModuleProfile(
    key="CM870",
    name="CM870 (ISX 2003-2006)",
    description="J1939 @ 250k + J1587 @ 9.6k. Calibration profile; verify before write.",
    protocol="j1939",
    image_size=0x200000,
    regions=[Region("calibration", 0x100000, 0x200000, 0x0)],
)

_PROFILES: Dict[str, ModuleProfile] = {
    p.key: p for p in (_CM870, _CM871, _CM2250, _CM2350, _CM2450)
}


def profile_keys() -> List[str]:
    return list(_PROFILES.keys())


def profile_labels() -> List[Tuple[str, str]]:
    return [(p.key, p.name) for p in _PROFILES.values()]


def get_profile(key: str) -> ModuleProfile:
    return _PROFILES[key]


def guess_profile(module_name: str) -> ModuleProfile:
    """Best-effort match from an identify/xcal module string (e.g. 'CM24xx')."""
    text = (module_name or "").upper()
    for key in ("CM2450", "CM2350", "CM2250", "CM871", "CM870"):
        if key in text:
            return _PROFILES[key]
    if "CM24" in text:
        return _CM2450
    if "CM23" in text:
        return _CM2350
    if "CM22" in text:
        return _CM2250
    return _CM2450
