"""Data-link transports for talking to a truck/ECU.

Everything here is behind a tiny abstract interface so the diagnostic layer
doesn't care how bytes get on the wire:

* ``SimulationTransport`` -- a fake ECU that lives in memory / a file, so the
  whole connect -> identify -> read/clear codes flow can be exercised with no
  hardware.
* ``Rp1210Transport`` -- the standard heavy-duty API (Nexiq, DPA, etc.) that
  speaks both J1939 and J1708/J1587. Windows only; loads the vendor DLL named
  in the driver's INI. **Written but untested against real hardware.**
* ``J2534Transport`` -- SAE J2534 pass-thru (CAN), common on lighter modules.
  Windows only. **Written but untested against real hardware.**
* ``SocketCanTransport`` -- Linux SocketCAN for bench work with a CAN adapter.

A transport moves *protocol data units*: for J1939 that's (29-bit id, data);
for J1708 that's a raw J1587 message. The diagnostic layer picks the protocol.
"""

from __future__ import annotations

import abc
import queue
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CanFrame:
    can_id: int          # 29-bit extended id for J1939
    data: bytes


class Transport(abc.ABC):
    """Minimal send/receive interface."""

    protocol = "j1939"   # or "j1587"

    @abc.abstractmethod
    def open(self) -> None:
        ...

    @abc.abstractmethod
    def close(self) -> None:
        ...

    @abc.abstractmethod
    def send(self, data: bytes, can_id: int = 0) -> None:
        ...

    @abc.abstractmethod
    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        ...

    def __enter__(self) -> "Transport":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Simulation (no hardware)
# ---------------------------------------------------------------------------

class SimulationTransport(Transport):
    """A fake ECU. Responds to J1939 request-PGNs from an in-memory bank so the
    GUI's connect/identify/read-codes/clear-codes flow works offline."""

    def __init__(self, responder=None):
        self._responder = responder            # callable(CanFrame) -> List[CanFrame]
        self._rx: "queue.Queue[CanFrame]" = queue.Queue()
        self._open = False

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def send(self, data: bytes, can_id: int = 0) -> None:
        if not self._open:
            raise RuntimeError("transport not open")
        if self._responder:
            for frame in self._responder(CanFrame(can_id, data)):
                self._rx.put(frame)

    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return None


# ---------------------------------------------------------------------------
# RP1210 (heavy-duty; J1939 + J1708/J1587) -- Windows, untested here
# ---------------------------------------------------------------------------

class Rp1210Transport(Transport):
    """RP1210 vendor-DLL transport. Requires a compliant driver installed on
    Windows (e.g. Nexiq USB-Link, Noregon DLA, Dearborn DPA). Not exercised in
    this environment; kept structurally complete so it can be tested on the
    truck."""

    def __init__(self, dll_name: str, device_id: int = 1,
                 protocol: str = "j1939", address: int = 0xF9):
        self.dll_name = dll_name
        self.device_id = device_id
        self.protocol = protocol
        self.address = address
        self._api = None
        self._client = None

    def open(self) -> None:
        import ctypes                                   # noqa: PLC0415 (optional)
        self._api = ctypes.windll.LoadLibrary(self.dll_name)
        proto = (b"J1939:Baud=250" if self.protocol == "j1939"
                 else b"J1708:Baud=9600")
        client = self._api.RP1210_ClientConnect(
            0, self.device_id, proto, 0, 0, 0)
        if client > 127:
            raise RuntimeError(f"RP1210_ClientConnect failed ({client})")
        self._client = client

    def close(self) -> None:
        if self._api is not None and self._client is not None:
            self._api.RP1210_ClientDisconnect(self._client)
            self._client = None

    def send(self, data: bytes, can_id: int = 0) -> None:
        raise NotImplementedError(
            "RP1210 send is device-specific; wire this up on the truck with "
            "your adapter's RP1210 driver.")

    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        raise NotImplementedError(
            "RP1210 recv is device-specific; wire this up on the truck.")


# ---------------------------------------------------------------------------
# J2534 pass-thru (CAN / J1939) -- Windows, untested here
# ---------------------------------------------------------------------------

class J2534Transport(Transport):
    """SAE J2534 pass-thru transport (CAN). Windows only, needs the device's
    J2534 DLL. Structurally complete; not tested against hardware here."""

    protocol = "j1939"

    def __init__(self, dll_path: str, baud: int = 250000):
        self.dll_path = dll_path
        self.baud = baud
        self._api = None
        self._device = None
        self._channel = None

    def open(self) -> None:
        import ctypes                                   # noqa: PLC0415 (optional)
        self._api = ctypes.windll.LoadLibrary(self.dll_path)
        dev = ctypes.c_ulong()
        if self._api.PassThruOpen(None, ctypes.byref(dev)) != 0:
            raise RuntimeError("PassThruOpen failed")
        self._device = dev
        chan = ctypes.c_ulong()
        CAN = 5
        if self._api.PassThruConnect(dev, CAN, 0, self.baud,
                                     ctypes.byref(chan)) != 0:
            raise RuntimeError("PassThruConnect failed")
        self._channel = chan

    def close(self) -> None:
        if self._api and self._channel is not None:
            self._api.PassThruDisconnect(self._channel)
            self._channel = None
        if self._api and self._device is not None:
            self._api.PassThruClose(self._device)
            self._device = None

    def send(self, data: bytes, can_id: int = 0) -> None:
        raise NotImplementedError(
            "J2534 send needs PASSTHRU_MSG marshalling; complete on hardware.")

    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        raise NotImplementedError(
            "J2534 recv needs PASSTHRU_MSG marshalling; complete on hardware.")


# ---------------------------------------------------------------------------
# SocketCAN -- Linux bench work
# ---------------------------------------------------------------------------

class SocketCanTransport(Transport):
    """Linux SocketCAN transport for bench testing with a CAN adapter
    (``ip link set can0 type can bitrate 250000``)."""

    protocol = "j1939"

    def __init__(self, channel: str = "can0"):
        self.channel = channel
        self._sock = None

    def open(self) -> None:
        import socket                                   # noqa: PLC0415
        s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        s.bind((self.channel,))
        s.settimeout(1.0)
        self._sock = s

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def send(self, data: bytes, can_id: int = 0) -> None:
        import struct                                   # noqa: PLC0415
        if self._sock is None:
            raise RuntimeError("transport not open")
        can_id |= 0x80000000                            # extended-frame flag
        frame = struct.pack("=IB3x8s", can_id, len(data), data.ljust(8, b"\x00"))
        self._sock.send(frame)

    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        import struct                                   # noqa: PLC0415
        if self._sock is None:
            raise RuntimeError("transport not open")
        self._sock.settimeout(timeout)
        try:
            frame = self._sock.recv(16)
        except (TimeoutError, OSError):
            return None
        can_id, length, payload = struct.unpack("=IB3x8s", frame)
        return CanFrame(can_id & 0x1FFFFFFF, payload[:length])


class PythonCanTransport(Transport):
    """Cross-platform CAN via the ``python-can`` library. Covers most bench
    USB-CAN adapters (CANable/SLCAN, PCAN, Vector, Kvaser, USB2CAN, SocketCAN,
    ...) through one API. ``interface``/``channel`` map to python-can's config,
    e.g. interface="slcan" channel="COM5", or interface="pcan" channel="PCAN_USBBUS1".

    Install with ``pip install python-can`` (optional). J1939 uses 29-bit
    extended frames at 250 kbit/s by default.
    """

    protocol = "j1939"

    def __init__(self, interface: str = "socketcan", channel: str = "can0",
                 bitrate: int = 250000):
        self.interface = interface
        self.channel = channel
        self.bitrate = bitrate
        self._bus = None

    def open(self) -> None:
        import can                                       # noqa: PLC0415 (optional)
        self._bus = can.Bus(interface=self.interface, channel=self.channel,
                            bitrate=self.bitrate)

    def close(self) -> None:
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None

    def send(self, data: bytes, can_id: int = 0) -> None:
        import can                                       # noqa: PLC0415
        if self._bus is None:
            raise RuntimeError("transport not open")
        self._bus.send(can.Message(arbitration_id=can_id, data=data,
                                   is_extended_id=True))

    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        if self._bus is None:
            raise RuntimeError("transport not open")
        msg = self._bus.recv(timeout)
        if msg is None:
            return None
        return CanFrame(msg.arbitration_id & 0x1FFFFFFF, bytes(msg.data))


def list_backends() -> List[str]:
    return ["Simulation", "python-can (USB-CAN)", "RP1210 (J1939/J1708)",
            "J2534 (CAN)", "SocketCAN"]
