"""Compare two calibration images (``.bin`` / ``.xcal``).

Loads each side to its raw flash image and reports the byte ranges that differ,
coalesced into contiguous "diff runs" -- so you can see exactly what (and how
much) a tune changed between two files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from . import xcalfmt


def load_image(data: bytes) -> bytes:
    """Return the raw flash image for a ``.xcal`` or a plain ``.bin``."""
    if xcalfmt.is_xcal(data):
        return xcalfmt.parse(data).image
    return data


@dataclass
class DiffRun:
    start: int          # absolute image offset of the first differing byte
    length: int
    a: bytes            # bytes from image A over this run
    b: bytes            # bytes from image B over this run
    param: str = ""     # ecfg parameter name, if known

    @property
    def end(self) -> int:
        return self.start + self.length


@dataclass
class CompareResult:
    size_a: int
    size_b: int
    runs: List[DiffRun]

    @property
    def diff_bytes(self) -> int:
        return sum(r.length for r in self.runs)

    @property
    def identical(self) -> bool:
        return not self.runs and self.size_a == self.size_b


def compare_images(a: bytes, b: bytes, max_gap: int = 8) -> CompareResult:
    """Diff two raw images. Differing bytes within ``max_gap`` of each other are
    merged into a single run so a changed 16-bit value or table reads as one
    entry instead of many single-byte hits."""
    n = min(len(a), len(b))
    runs: List[DiffRun] = []
    i = 0
    start = -1
    while i < n:
        if a[i] != b[i]:
            if start < 0:
                start = i
            last = i
            j = i + 1
            while j < n and (a[j] != b[j] or (j - last) <= max_gap):
                if a[j] != b[j]:
                    last = j
                j += 1
            runs.append(DiffRun(start, last - start + 1,
                                a[start:last + 1], b[start:last + 1]))
            start = -1
            i = j
        else:
            i += 1
    # Trailing region present in only one image (different lengths).
    if len(a) != len(b):
        lo, hi = min(len(a), len(b)), max(len(a), len(b))
        longer = a if len(a) > len(b) else b
        runs.append(DiffRun(lo, hi - lo,
                            longer[lo:hi] if longer is a else b"",
                            longer[lo:hi] if longer is b else b""))
    return CompareResult(len(a), len(b), runs)


def format_report(result: CompareResult, limit: int = 200) -> str:
    """A concise text summary suitable for the GUI log or a saved file."""
    lines = [
        f"Image A: {result.size_a:,} bytes",
        f"Image B: {result.size_b:,} bytes",
    ]
    if result.identical:
        lines.append("Result : IDENTICAL")
        return "\n".join(lines)
    lines.append(f"Result : {len(result.runs)} diff run(s), "
                 f"{result.diff_bytes:,} bytes changed")
    lines.append("")
    for r in result.runs[:limit]:
        tag = f"  [{r.param}]" if r.param else ""
        a_hex = r.a[:16].hex(" ")
        b_hex = r.b[:16].hex(" ")
        lines.append(f"0x{r.start:08X}  +{r.length}{tag}")
        lines.append(f"    A: {a_hex}{' ...' if r.length > 16 else ''}")
        lines.append(f"    B: {b_hex}{' ...' if r.length > 16 else ''}")
    if len(result.runs) > limit:
        lines.append(f"... {len(result.runs) - limit} more run(s)")
    return "\n".join(lines)
