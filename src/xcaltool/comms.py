"""Abstract interface for talking to an ECU (future read/write support).

Nothing here talks to real hardware yet. The point is to fix a small, stable
interface now so that when we add live ECU **read** (dump the flash to a .bin)
and **write** (flash a .bin/.xcal), the GUI and codec code won't need to
change -- we just add a concrete backend (e.g. a J2534 pass-thru device or a
SocketCAN adapter) that implements ``EcuLink``.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class EcuInfo:
    """Basic identity read back from a connected ECU."""

    ecm_code: str = ""          # e.g. "CM2350"
    part_number: str = ""
    calibration_id: str = ""
    serial: str = ""


# Progress callback: (bytes_done, bytes_total) -> None
ProgressCallback = Optional[Callable[[int, int], None]]


class EcuLink(abc.ABC):
    """A connection to an ECU. Concrete backends implement these methods."""

    @abc.abstractmethod
    def connect(self) -> None:
        ...

    @abc.abstractmethod
    def disconnect(self) -> None:
        ...

    @abc.abstractmethod
    def identify(self) -> EcuInfo:
        """Read ECU identity (part number, calibration id, ...)."""

    @abc.abstractmethod
    def read_image(self, progress: ProgressCallback = None) -> bytes:
        """Read the full flash image from the ECU and return it as raw bytes."""

    @abc.abstractmethod
    def write_image(self, image: bytes, progress: ProgressCallback = None) -> None:
        """Write a raw flash image to the ECU."""

    # Context-manager sugar so callers can use ``with backend() as ecu:``
    def __enter__(self) -> "EcuLink":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()


class NotConnectedBackend(EcuLink):
    """Placeholder backend used until a real transport is implemented.

    It intentionally raises so the GUI can show a clear "read/write not wired
    up yet" message instead of silently doing nothing.
    """

    _MSG = (
        "ECU read/write is not implemented yet. This build only converts "
        "files. A hardware backend (J2534 / CAN) will implement EcuLink."
    )

    def connect(self) -> None:
        raise NotImplementedError(self._MSG)

    def disconnect(self) -> None:
        pass

    def identify(self) -> EcuInfo:
        raise NotImplementedError(self._MSG)

    def read_image(self, progress: ProgressCallback = None) -> bytes:
        raise NotImplementedError(self._MSG)

    def write_image(self, image: bytes, progress: ProgressCallback = None) -> None:
        raise NotImplementedError(self._MSG)
