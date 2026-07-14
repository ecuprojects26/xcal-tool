"""SAE J1939 (CAN) diagnostics helpers.

Pure encode/decode logic -- no hardware. Covers the parts we need for a simple
diagnostic session on Cummins Core-II modules (CM23xx/CM24xx/CM2450):

* build a "request PGN" frame (PGN 59904)
* decode active / previously-active DTCs (DM1 / DM2)
* build the "clear DTCs" requests (DM11 active, DM3 previously-active)
* decode Component ID (PGN 65259) and Software ID (PGN 65242)

A J1939 DTC packs a 19-bit SPN (Suspect Parameter Number), a 5-bit FMI
(Failure Mode Indicator) and an occurrence count into 4 bytes, using the
common "version 4" SPN layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

# Parameter Group Numbers we use.
PGN_REQUEST = 59904          # 0xEA00
PGN_DM1 = 65226              # 0xFECA  active DTCs
PGN_DM2 = 65227              # 0xFECB  previously active DTCs
PGN_DM3 = 65228             # 0xFECC  clear previously active DTCs
PGN_DM11 = 65235            # 0xFED3  clear active DTCs
PGN_COMPONENT_ID = 65259    # 0xFEEB  Make*Model*Serial*Unit
PGN_SOFTWARE_ID = 65242     # 0xFEDA

GLOBAL_ADDRESS = 0xFF
ENGINE_ADDRESS = 0x00       # Cummins engine ECM default source address


@dataclass
class J1939Dtc:
    spn: int
    fmi: int
    occurrence_count: int = 0
    conversion_method: int = 0

    def __str__(self) -> str:
        return f"SPN {self.spn} FMI {self.fmi} (x{self.occurrence_count})"


def canid_to_pgn(can_id: int) -> int:
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    return (pf << 8) if pf < 240 else ((pf << 8) | ps)


def canid_source(can_id: int) -> int:
    return can_id & 0xFF


def pgn_to_canid(pgn: int, source: int = 0, priority: int = 6,
                 dest: int = GLOBAL_ADDRESS) -> int:
    pf = (pgn >> 8) & 0xFF
    ps = pgn & 0xFF
    if pf < 240:                                   # PDU1 (destination-specific)
        cid = (priority << 26) | (pf << 16) | (dest << 8) | source
    else:                                          # PDU2 (broadcast)
        cid = (priority << 26) | (pf << 16) | (ps << 8) | source
    return cid & 0x1FFFFFFF


def request_pgn(pgn: int, dest: int = GLOBAL_ADDRESS) -> bytes:
    """Body of a request for ``pgn`` (3 bytes, little-endian). ``dest`` is the
    target address (caller builds the 29-bit CAN id)."""
    return bytes([pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF])


def encode_dtc(dtc: J1939Dtc) -> bytes:
    spn, fmi = dtc.spn, dtc.fmi
    return bytes([
        spn & 0xFF,
        (spn >> 8) & 0xFF,
        ((spn >> 16) & 0x07) << 5 | (fmi & 0x1F),
        ((dtc.conversion_method & 0x01) << 7) | (dtc.occurrence_count & 0x7F),
    ])


def decode_dtc(b: bytes) -> J1939Dtc:
    spn = b[0] | (b[1] << 8) | ((b[2] >> 5) & 0x07) << 16
    fmi = b[2] & 0x1F
    return J1939Dtc(
        spn=spn,
        fmi=fmi,
        occurrence_count=b[3] & 0x7F,
        conversion_method=(b[3] >> 7) & 0x01,
    )


def decode_dm(data: bytes) -> List[J1939Dtc]:
    """Decode a DM1/DM2 payload: 2 lamp-status bytes then 4 bytes per DTC."""
    body = data[2:]
    out: List[J1939Dtc] = []
    for i in range(0, len(body) - 3, 4):
        chunk = body[i:i + 4]
        if chunk == b"\x00\x00\x00\x00" or chunk == b"\xFF\xFF\xFF\xFF":
            continue
        out.append(decode_dtc(chunk))
    return out


def encode_dm(dtcs: List[J1939Dtc], lamp_status: int = 0, lamp_flash: int = 0xFF) -> bytes:
    """Build a DM1/DM2 payload (2 lamp bytes + DTCs). If there are no DTCs the
    standard "no faults" body is 6 bytes of 0x00/0xFF."""
    out = bytes([lamp_status, lamp_flash])
    if not dtcs:
        return out + b"\x00\x00\x00\x00"
    for d in dtcs:
        out += encode_dtc(d)
    return out


def decode_component_id(data: bytes) -> dict:
    fields = data.split(b"*")
    keys = ["make", "model", "serial", "unit"]
    text = [f.decode("latin-1", "replace").strip("\x00 ") for f in fields]
    return {k: (text[i] if i < len(text) else "") for i, k in enumerate(keys)}


def decode_software_id(data: bytes) -> List[str]:
    # First byte is the field count; fields are '*'-separated ASCII.
    body = data[1:] if data else b""
    return [f.decode("latin-1", "replace").strip("\x00 ")
            for f in body.split(b"*") if f]
