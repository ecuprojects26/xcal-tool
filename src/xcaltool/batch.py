"""Batch conversion of a folder of calibration files.

For every ``.xcal`` found, writes the raw flat ``.bin`` and the EFILive compact
``*_efi.bin`` next to it. Pure library logic (no GUI, no I/O side effects beyond
the files it is asked to write) so it can be unit tested.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import xcalfmt


@dataclass
class BatchItem:
    source: str
    outputs: List[str]
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def find_xcals(folder: str) -> List[str]:
    """List ``.xcal`` files in ``folder`` (non-recursive). Also accepts the
    extension-less EFILive naming by sniffing the header."""
    found = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if name.lower().endswith(".xcal"):
            found.append(path)
            continue
        try:
            with open(path, "rb") as fh:
                head = fh.read(8192)
            if xcalfmt.is_xcal(head):
                found.append(path)
        except OSError:
            pass
    return found


def convert_folder(folder: str,
                   progress: Optional[Callable[[int, int, str], None]] = None
                   ) -> List[BatchItem]:
    """Convert every ``.xcal`` in ``folder`` to ``.bin`` + ``*_efi.bin``.

    ``progress(done, total, name)`` is called after each file if supplied.
    Returns one :class:`BatchItem` per source file (with any error captured)."""
    sources = find_xcals(folder)
    items: List[BatchItem] = []
    total = len(sources)
    for i, src in enumerate(sources, 1):
        item = BatchItem(src, [])
        try:
            with open(src, "rb") as fh:
                data = fh.read()
            x = xcalfmt.parse(data)
            base = os.path.splitext(src)[0]
            bin_path = base + ".bin"
            with open(bin_path, "wb") as fh:
                fh.write(x.image)
            item.outputs.append(bin_path)
            efi_path = base + "_efi.bin"
            with open(efi_path, "wb") as fh:
                fh.write(xcalfmt.to_efi_bin(x))
            item.outputs.append(efi_path)
        except (OSError, xcalfmt.XcalError) as exc:
            item.error = str(exc)
        items.append(item)
        if progress:
            progress(i, total, os.path.basename(src))
    return items
