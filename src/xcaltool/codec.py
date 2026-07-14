"""Conversion between `.xcal` calibration containers and raw `.bin` images.

Important honesty note
----------------------
The Cummins Calterm/INSITE `.xcal` container is **not publicly documented**.
Rather than hard-code a guessed layout, this module treats an `.xcal` file as a
generic container:

    [ header ][ ...raw flash image (the .bin)... ][ trailer / checksum ]

and lets the caller (or the auto-detector) decide where the payload starts and
ends. That makes the tool immediately useful as a reverse-engineering aid, and
once we have a confirmed sample we only need to lock in the correct
`ContainerSpec` defaults / checksum algorithm here.

Round-tripping is lossless: when we extract a `.bin` we keep the original
header and trailer bytes so the exact `.xcal` can be rebuilt.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field, asdict
from typing import Optional

from . import checksum as _checksum


class ConversionError(Exception):
    """Raised when a file cannot be converted."""


@dataclass
class ContainerSpec:
    """Describes where the raw image lives inside an `.xcal` container.

    header_len   number of bytes before the raw image
    trailer_len  number of bytes after the raw image (e.g. a checksum block)
    checksum     name of the checksum algorithm covering the payload, or None.
                 Must be a key in checksum.ALGORITHMS.
    """

    header_len: int = 0
    trailer_len: int = 0
    checksum: Optional[str] = None

    def validate(self) -> None:
        if self.header_len < 0 or self.trailer_len < 0:
            raise ConversionError("header_len and trailer_len must be >= 0")
        if self.checksum is not None and self.checksum not in _checksum.ALGORITHMS:
            raise ConversionError(f"unknown checksum algorithm: {self.checksum!r}")


@dataclass
class ExtractResult:
    """Result of pulling the raw image out of an `.xcal` container."""

    payload: bytes                      # the raw .bin flash image
    header: bytes = b""                 # bytes preserved from before the image
    trailer: bytes = b""                # bytes preserved from after the image
    spec: ContainerSpec = field(default_factory=ContainerSpec)

    def sidecar_dict(self) -> dict:
        """Metadata needed to rebuild the exact original `.xcal`."""
        return {
            "header_b64": base64.b64encode(self.header).decode("ascii"),
            "trailer_b64": base64.b64encode(self.trailer).decode("ascii"),
            "spec": asdict(self.spec),
            "payload_len": len(self.payload),
        }


# ---------------------------------------------------------------------------
# Analysis / auto-detection
# ---------------------------------------------------------------------------

_PRINTABLE = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def _printable_prefix_len(data: bytes, min_run_end: int = 4) -> int:
    """Length of the leading run of mostly-printable ASCII (a likely header).

    Returns 0 if the file starts as binary (looks like a raw image already).
    """
    i = 0
    n = len(data)
    while i < n and data[i] in _PRINTABLE:
        i += 1
    # A handful of printable bytes at the start of a binary file is normal
    # noise; only treat a reasonably long run as a real text header.
    return i if i >= 16 else 0


def analyze(data: bytes) -> dict:
    """Return a human-readable report about a file's likely structure."""
    size = len(data)
    zeros = data.count(0)
    ffs = data.count(0xFF)
    printable = sum(1 for b in data if b in _PRINTABLE)
    header_guess = _printable_prefix_len(data)
    return {
        "size": size,
        "printable_pct": round(100 * printable / size, 1) if size else 0.0,
        "zero_pct": round(100 * zeros / size, 1) if size else 0.0,
        "ff_pct": round(100 * ffs / size, 1) if size else 0.0,
        "ascii_header_len_guess": header_guess,
        "looks_like_raw_bin": header_guess == 0,
    }


def guess_spec(data: bytes) -> ContainerSpec:
    """Best-effort guess of the container layout.

    This is a heuristic starting point, NOT a confirmed Calterm layout. The GUI
    exposes header/trailer offsets so a human can correct it, and once we have a
    real sample we can set the correct defaults here.
    """
    header_len = _printable_prefix_len(data)
    return ContainerSpec(header_len=header_len, trailer_len=0, checksum=None)


# ---------------------------------------------------------------------------
# Core conversions
# ---------------------------------------------------------------------------

def extract_bin(data: bytes, spec: Optional[ContainerSpec] = None) -> ExtractResult:
    """xcal -> bin. Strip the container and return the raw flash image."""
    if spec is None:
        spec = guess_spec(data)
    spec.validate()
    if spec.header_len + spec.trailer_len > len(data):
        raise ConversionError(
            "header_len + trailer_len is larger than the file; "
            "check the offsets."
        )
    start = spec.header_len
    end = len(data) - spec.trailer_len
    return ExtractResult(
        payload=data[start:end],
        header=data[:start],
        trailer=data[end:] if spec.trailer_len else b"",
        spec=spec,
    )


def build_xcal(
    payload: bytes,
    header: bytes = b"",
    spec: Optional[ContainerSpec] = None,
    trailer_override: Optional[bytes] = None,
) -> bytes:
    """bin -> xcal. Wrap a raw image back into a container.

    If ``trailer_override`` is given (e.g. the original trailer preserved during
    extraction), it is used verbatim so the round-trip is byte-exact. Otherwise,
    if ``spec.checksum`` is set, a fresh checksum trailer is computed over
    ``header + payload``.
    """
    if spec is None:
        spec = ContainerSpec(header_len=len(header))
    spec.validate()

    body = header + payload
    if trailer_override is not None:
        trailer = trailer_override
    elif spec.checksum:
        value = _checksum.ALGORITHMS[spec.checksum](body)
        width = {"sum8": 1, "sum16": 2, "crc16_ccitt": 2, "sum32": 4, "crc32": 4}[spec.checksum]
        trailer = value.to_bytes(width, "little")
    else:
        trailer = b""
    return body + trailer


def rebuild_from_sidecar(payload: bytes, sidecar: dict) -> bytes:
    """Rebuild the exact original `.xcal` using metadata saved at extract time."""
    header = base64.b64decode(sidecar.get("header_b64", ""))
    trailer = base64.b64decode(sidecar.get("trailer_b64", ""))
    spec = ContainerSpec(**sidecar.get("spec", {}))
    return build_xcal(payload, header=header, spec=spec, trailer_override=trailer)


def write_sidecar(path: str, result: ExtractResult) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result.sidecar_dict(), fh, indent=2)


def read_sidecar(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
