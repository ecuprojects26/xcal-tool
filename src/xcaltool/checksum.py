"""Checksum helpers.

Calibration containers usually protect their payload with a checksum so the
tool that flashes the ECU can detect corruption. We don't yet know which
algorithm the Calterm/INSITE `.xcal` container uses, so this module provides
the common candidates. Once a real sample is available we can confirm which
one matches and wire it into the codec.
"""

from __future__ import annotations

import zlib


def sum8(data: bytes) -> int:
    """Simple 8-bit additive checksum (mod 256)."""
    return sum(data) & 0xFF


def sum16(data: bytes) -> int:
    """Simple 16-bit additive checksum (mod 65536)."""
    return sum(data) & 0xFFFF


def sum32(data: bytes) -> int:
    """Simple 32-bit additive checksum (mod 2**32)."""
    return sum(data) & 0xFFFFFFFF


def crc32(data: bytes) -> int:
    """Standard CRC-32 (as used by zlib / PKZIP)."""
    return zlib.crc32(data) & 0xFFFFFFFF


def crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE, a very common choice in automotive tooling."""
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


# Registry so the codec / GUI can offer a choice by name.
ALGORITHMS = {
    "sum8": sum8,
    "sum16": sum16,
    "sum32": sum32,
    "crc16_ccitt": crc16_ccitt,
    "crc32": crc32,
}
