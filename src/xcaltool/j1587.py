"""SAE J1587 / J1708 diagnostics helpers (older Cummins engines).

Pure encode/decode logic -- no hardware. J1708 is the data link; J1587 is the
message layer. A message is::

    <MID> <PID> <data...> [<PID> <data...> ...] <checksum>

Diagnostic trouble codes ride in PID 194. Each fault in PID 194 is:

    <id byte> <status byte> [<occurrence count>]

* id byte      -- the PID or SID number of the failed item (0xFF = the real id
                  is in the following byte, for numbers >= 255)
* status byte  -- bit7 1=SID / 0=PID, bit6 = "occurrence count included",
                  bit4 = "inactive", bits0-3 = FMI
* occurrence   -- present only when the count-included bit is set (bit7 spare)

MIDs identify the subsystem (128 = engine, 130 = transmission, ...).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

MID_ENGINE = 128
MID_TRANSMISSION = 130
MID_BRAKES = 136

PID_DIAGNOSTIC = 194
PID_COMPONENT_ID = 243       # ASCII component identification
PID_SOFTWARE_ID = 234


@dataclass
class J1587Dtc:
    code: int                # PID or SID number of the failed item
    fmi: int
    is_sid: bool = False
    occurrence_count: int = 0
    inactive: bool = False

    def __str__(self) -> str:
        kind = "SID" if self.is_sid else "PID"
        state = "inactive" if self.inactive else "active"
        return f"{kind} {self.code} FMI {self.fmi} ({state}, x{self.occurrence_count})"


def checksum(body: bytes) -> int:
    """J1708 checksum: two's complement of the sum of the bytes."""
    return (-sum(body)) & 0xFF


def encode_dtc(dtc: J1587Dtc) -> bytes:
    out = bytearray()
    if dtc.code >= 0xFF:
        out.append(0xFF)
        out.append(dtc.code & 0xFF)
    else:
        out.append(dtc.code)
    status = dtc.fmi & 0x0F
    if dtc.is_sid:
        status |= 0x80
    if dtc.inactive:
        status |= 0x10
    if dtc.occurrence_count:
        status |= 0x40
    out.append(status)
    if dtc.occurrence_count:
        out.append(dtc.occurrence_count & 0x7F)
    return bytes(out)


def decode_pid194(data: bytes) -> List[J1587Dtc]:
    """Decode the data field of PID 194 into DTCs."""
    out: List[J1587Dtc] = []
    i = 0
    n = len(data)
    while i < n - 1:
        code = data[i]
        i += 1
        if code == 0xFF and i < n:
            code = data[i]
            i += 1
        if i >= n:
            break
        status = data[i]
        i += 1
        dtc = J1587Dtc(
            code=code,
            fmi=status & 0x0F,
            is_sid=bool(status & 0x80),
            inactive=bool(status & 0x10),
        )
        if status & 0x40 and i < n:
            dtc.occurrence_count = data[i] & 0x7F
            i += 1
        out.append(dtc)
    return out


def build_message(mid: int, pid: int, data: bytes) -> bytes:
    body = bytes([mid, pid]) + data
    return body + bytes([checksum(body)])


def parse_message(msg: bytes) -> tuple:
    """Return (mid, pid, data) for a single-PID message; checksum is verified."""
    if len(msg) < 3:
        raise ValueError("J1587 message too short")
    if checksum(msg[:-1]) != msg[-1]:
        raise ValueError("bad J1587 checksum")
    return msg[0], msg[1], msg[2:-1]
