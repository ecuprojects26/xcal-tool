"""EFILive/Cummins ``.xcal`` container format.

Reverse-engineered from real sample calibrations. Confirmed byte-exact against
STK/TUN/MOD `.xcal` files for CM22xx, CM23xx and CM2450A modules.

File layout
-----------
::

    <token>\\r\\n                     4 hex chars, a file-integrity checksum
    <compatibility_header ...>...</compatibility_header>\\r\\n   (XML, one line)
    :020000040000FA\\r\\n              Intel-HEX records (CRLF-terminated)
    :20....\\r\\n
    ...
    :00000001FF\\r\\n                  Intel-HEX EOF

The Intel-HEX payload decodes to the raw flash image (base address 0, gaps
filled with 0xFF). EFILive's own ``*_efi.bin`` is that same image plus a small
proprietary identification block appended near the top; we reproduce the true
flash content and preserve everything needed to rebuild the exact ``.xcal``.

Round-trip
----------
``xcal -> bin`` returns the image plus a small ``meta`` dict (the token, the
exact header bytes, and the Intel-HEX "runs"). ``bin -> xcal`` uses that meta to
regenerate a byte-identical ``.xcal`` -- so long as the bytes inside the HEX
runs are unchanged, the rebuild is exact.

Known limitation
----------------
The 16-bit ``<token>`` is an EFILive file-integrity checksum whose algorithm is
not published. We preserve it for lossless round-trips, but we do NOT recompute
it: if you modify bytes inside the image and rebuild, EFILive may reject the
file until it recomputes its own checksum. (We deliberately don't reverse
EFILive's integrity check.)
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

RECORD_SIZE = 32          # data bytes per Intel-HEX data record
FILL_BYTE = 0xFF
EOL = b"\r\n"

Run = Tuple[int, int]     # (absolute_start_address, length_in_bytes)


class XcalError(Exception):
    """Raised when an .xcal file cannot be parsed."""


# ---------------------------------------------------------------------------
# Intel-HEX helpers
# ---------------------------------------------------------------------------

def _ihex_line(rectype: int, addr16: int, payload: bytes) -> bytes:
    body = bytes([len(payload), (addr16 >> 8) & 0xFF, addr16 & 0xFF, rectype]) + payload
    checksum = (-sum(body)) & 0xFF
    return b":" + (body + bytes([checksum])).hex().upper().encode("ascii")


# ---------------------------------------------------------------------------
# Parsed representation
# ---------------------------------------------------------------------------

@dataclass
class XcalFile:
    token: str                       # 4-hex-char integrity token, verbatim
    header: bytes                    # the compatibility_header XML bytes
    image: bytes                     # raw flash image (base 0, 0xFF filled)
    runs: List[Run] = field(default_factory=list)   # HEX coverage

    @property
    def fields(self) -> Dict[str, str]:
        return parse_header_fields(self.header)

    def meta(self) -> dict:
        """Everything needed (besides the image bytes) to rebuild the .xcal."""
        return {
            "format": "efilive_cummins_xcal",
            "token": self.token,
            "header_b64": base64.b64encode(self.header).decode("ascii"),
            "runs": self.runs,
            "record_size": RECORD_SIZE,
            "image_size": len(self.image),
        }


def parse_header_fields(header: bytes) -> Dict[str, str]:
    """Pull the simple <tag>value</tag> pairs out of the compatibility header."""
    text = header.decode("latin-1", errors="replace")
    return {m.group(1): m.group(2) for m in re.finditer(r"<([\w]+)>([^<]*)</\1>", text)}


# ---------------------------------------------------------------------------
# Parsing (xcal -> image + meta)
# ---------------------------------------------------------------------------

def is_xcal(data: bytes) -> bool:
    return b"<compatibility_header>" in data[:4096] and b":020000040000FA" in data[:8192]


def parse(data: bytes) -> XcalFile:
    nl = data.find(EOL)
    if nl < 0:
        raise XcalError("not an .xcal file (no header line)")
    token = data[:nl].decode("ascii", errors="replace").strip()

    tag = b"</compatibility_header>"
    he = data.find(tag)
    if he < 0:
        raise XcalError("missing compatibility_header")
    he += len(tag)
    header = data[nl + 2:he]

    hs = data.find(b":", he)
    if hs < 0:
        raise XcalError("no Intel-HEX data found")

    image, runs = _decode_ihex(data[hs:])
    return XcalFile(token=token, header=header, image=image, runs=runs)


def _decode_ihex(hexblob: bytes) -> Tuple[bytes, List[Run]]:
    upper = 0
    mem: Dict[int, int] = {}
    datarecs: List[Run] = []
    for line in hexblob.split(EOL):
        if not line.startswith(b":"):
            continue
        try:
            raw = bytes.fromhex(line[1:].decode("ascii"))
        except ValueError as exc:
            raise XcalError(f"malformed Intel-HEX line: {line[:16]!r}") from exc
        count = raw[0]
        if len(raw) != 5 + count:
            raise XcalError(f"Intel-HEX record length mismatch: {line[:16]!r}")
        addr = (raw[1] << 8) | raw[2]
        rectype = raw[3]
        payload = raw[4:4 + count]
        if (sum(raw[:4 + count]) + raw[4 + count]) & 0xFF != 0:
            raise XcalError(f"bad Intel-HEX checksum: {line[:16]!r}")
        if rectype == 0x00:
            full = (upper << 16) + addr
            datarecs.append((full, count))
            for k, b in enumerate(payload):
                mem[full + k] = b
        elif rectype == 0x04:
            upper = (payload[0] << 8) | payload[1]
        elif rectype == 0x02:
            upper = ((payload[0] << 8) | payload[1]) >> 12
        elif rectype == 0x01:
            break
    if not mem:
        raise XcalError("no data records")
    size = max(mem) + 1
    image = bytearray([FILL_BYTE]) * size
    for a, b in mem.items():
        image[a] = b
    return bytes(image), _merge_runs(datarecs)


def _merge_runs(datarecs: List[Run]) -> List[Run]:
    runs: List[Run] = []
    cur_start = None
    cur_len = 0
    for addr, length in datarecs:
        if cur_start is not None and addr == cur_start + cur_len:
            cur_len += length
        else:
            if cur_start is not None:
                runs.append((cur_start, cur_len))
            cur_start, cur_len = addr, length
    if cur_start is not None:
        runs.append((cur_start, cur_len))
    return runs


# ---------------------------------------------------------------------------
# Rebuilding (image + meta -> xcal)
# ---------------------------------------------------------------------------

def build(image: bytes, meta: dict) -> bytes:
    if meta.get("format") != "efilive_cummins_xcal":
        raise XcalError("meta is not for the EFILive Cummins xcal format")
    token = meta["token"].encode("ascii")
    header = base64.b64decode(meta["header_b64"])
    runs = [tuple(r) for r in meta["runs"]]
    record_size = int(meta.get("record_size", RECORD_SIZE))
    hexblob = _encode_ihex(image, runs, record_size)
    return token + EOL + header + EOL + hexblob


def _encode_ihex(image: bytes, runs: List[Run], record_size: int = RECORD_SIZE) -> bytes:
    out: List[bytes] = []
    cur_upper = None
    for start, total in runs:
        pos = start
        end = start + total
        while pos < end:
            upper = (pos >> 16) & 0xFFFF
            if upper != cur_upper:
                out.append(_ihex_line(0x04, 0, bytes([(upper >> 8) & 0xFF, upper & 0xFF])))
                cur_upper = upper
            next_boundary = ((pos >> 16) + 1) << 16
            chunk = min(record_size, end - pos, next_boundary - pos)
            out.append(_ihex_line(0x00, pos & 0xFFFF, image[pos:pos + chunk]))
            pos += chunk
    out.append(_ihex_line(0x01, 0, b""))
    return EOL.join(out) + EOL


# ---------------------------------------------------------------------------
# Convenience one-shots used by the GUI
# ---------------------------------------------------------------------------

def xcal_to_bin(data: bytes) -> Tuple[bytes, dict]:
    """Return (raw_flash_image, meta). Save ``meta`` next to the .bin."""
    x = parse(data)
    return x.image, x.meta()


def bin_to_xcal(image: bytes, meta: dict) -> bytes:
    """Rebuild an .xcal from a raw image and the meta saved at extract time."""
    return build(image, meta)
