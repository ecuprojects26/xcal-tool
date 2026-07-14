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
import ctypes
import os
import queue
import time
from dataclasses import dataclass
from typing import List, Optional


class _PassThruMsg(ctypes.Structure):
    """SAE J2534 PASSTHRU_MSG. For CAN the payload is the 4-byte CAN id
    (big-endian) followed by the data bytes."""
    _fields_ = [
        ("ProtocolID", ctypes.c_ulong),
        ("RxStatus", ctypes.c_ulong),
        ("TxFlags", ctypes.c_ulong),
        ("Timestamp", ctypes.c_ulong),
        ("DataSize", ctypes.c_ulong),
        ("ExtraDataIndex", ctypes.c_ulong),
        ("Data", ctypes.c_ubyte * 4128),
    ]


@dataclass
class CanFrame:
    can_id: int          # 29-bit extended id for J1939
    data: bytes


class Transport(abc.ABC):
    """Minimal send/receive interface."""

    protocol = "j1939"   # or "j1587"
    # True when the driver reassembles J1939 Transport Protocol (BAM/RTS-CTS)
    # itself and delivers complete multi-packet messages (e.g. RP1210). Raw-CAN
    # transports leave this False so the diagnostic layer does TP itself.
    reassembles_tp = False

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
    """RP1210 vendor-DLL transport for heavy-duty adapters (Nexiq USB-Link 2,
    Noregon DLA, Dearborn DPA, ...). The driver reassembles J1939 Transport
    Protocol itself, so complete messages are delivered/accepted here.

    NOTE: RP1210 vendor DLLs are almost always **32-bit** (e.g. the Nexiq
    ``NULN2R32.dll``), so this must run under **32-bit Python** on Windows.
    Implemented to the RP1210C spec; verify framing against your adapter.
    """

    reassembles_tp = True

    # RP1210 SendCommand numbers
    _CMD_RESET = 0
    _CMD_SET_ALL_FILTERS_TO_PASS = 3
    _CMD_PROTECT_J1939_ADDRESS = 19

    def __init__(self, dll_name: str, device_id: int = 1,
                 protocol: str = "j1939", address: int = 0xF9):
        self.dll_name = dll_name
        self.device_id = device_id
        self.protocol = protocol
        self.address = address
        self._api = None
        self._client = None
        self._ctypes = None

    def _bind(self, api, ctypes):
        api.RP1210_ClientConnect.restype = ctypes.c_short
        api.RP1210_ClientDisconnect.restype = ctypes.c_short
        api.RP1210_SendMessage.restype = ctypes.c_short
        api.RP1210_ReadMessage.restype = ctypes.c_short
        api.RP1210_SendCommand.restype = ctypes.c_short

    def open(self) -> None:
        import ctypes                                   # noqa: PLC0415 (optional)
        self._ctypes = ctypes
        try:
            self._api = ctypes.windll.LoadLibrary(self.dll_name)
        except OSError as exc:
            raise RuntimeError(
                f"Could not load RP1210 DLL {self.dll_name!r} ({exc}). "
                "RP1210 driver DLLs are 32-bit -- run this under 32-bit "
                "Python on Windows with the adapter's drivers installed.")
        self._bind(self._api, ctypes)
        proto = (b"J1939:Baud=250" if self.protocol == "j1939"
                 else b"J1708:Baud=9600")
        # ClientConnect(hwndClient, nDeviceID, fpchProtocol, nTx, nRx, nIsAppPacketizing)
        client = self._api.RP1210_ClientConnect(
            0, self.device_id, proto, 0, 0, 0)
        if client > 127:                               # 128..255 are error codes
            raise RuntimeError(f"RP1210_ClientConnect failed ({client})")
        self._client = client
        # Pass all traffic, then claim our source address on J1939.
        self._api.RP1210_SendCommand(self._CMD_SET_ALL_FILTERS_TO_PASS,
                                     client, None, 0)
        if self.protocol == "j1939":
            name = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
            cmd = bytes([self.address]) + name + bytes([0x00])   # +block flag
            buf = ctypes.create_string_buffer(cmd, len(cmd))
            self._api.RP1210_SendCommand(self._CMD_PROTECT_J1939_ADDRESS,
                                         client, buf, len(cmd))

    def close(self) -> None:
        if self._api is not None and self._client is not None:
            self._api.RP1210_ClientDisconnect(self._client)
            self._client = None

    @staticmethod
    def _split_canid(can_id: int):
        priority = (can_id >> 26) & 0x07
        pf = (can_id >> 16) & 0xFF
        ps = (can_id >> 8) & 0xFF
        sa = can_id & 0xFF
        if pf < 240:                                   # PDU1 destination-specific
            return priority, (pf << 8), sa, ps
        return priority, ((pf << 8) | ps), sa, 0xFF    # PDU2 broadcast

    @staticmethod
    def _make_canid(pgn: int, sa: int, priority: int, da: int) -> int:
        pf = (pgn >> 8) & 0xFF
        ps = pgn & 0xFF
        if pf < 240:
            cid = (priority << 26) | (pf << 16) | (da << 8) | sa
        else:
            cid = (priority << 26) | (pf << 16) | (ps << 8) | sa
        return cid & 0x1FFFFFFF

    def send(self, data: bytes, can_id: int = 0) -> None:
        if self._api is None or self._client is None:
            raise RuntimeError("transport not open")
        priority, pgn, sa, da = self._split_canid(can_id)
        if not sa:
            sa = self.address
        # RP1210 J1939 tx block: PGN(3, LE), priority, source, dest, data...
        msg = bytes([pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF,
                     priority & 0x07, sa, da]) + data
        buf = self._ctypes.create_string_buffer(msg, len(msg))
        rc = self._api.RP1210_SendMessage(self._client, buf, len(msg), 0, 0)
        if rc != 0:
            raise RuntimeError(f"RP1210_SendMessage failed ({rc})")

    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        if self._api is None or self._client is None:
            raise RuntimeError("transport not open")
        buf = self._ctypes.create_string_buffer(2048)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            n = self._api.RP1210_ReadMessage(self._client, buf, 2048, 0)
            if n > 10:
                raw = buf.raw[:n]
                # rx block: timestamp(4), PGN(3, LE), priority, SA, DA, data...
                pgn = raw[4] | (raw[5] << 8) | (raw[6] << 16)
                priority, sa, da = raw[7] & 0x07, raw[8], raw[9]
                return CanFrame(self._make_canid(pgn, sa, priority, da), raw[10:])
            time.sleep(0.002)
        return None


# ---------------------------------------------------------------------------
# J2534 pass-thru (CAN / J1939) -- Windows, untested here
# ---------------------------------------------------------------------------

class J2534Transport(Transport):
    """SAE J2534 pass-thru transport (raw 29-bit CAN at 250 kbit/s for J1939).
    Windows only, needs the device's J2534 DLL. This does raw CAN frames, so
    the diagnostic layer performs J1939 Transport Protocol (BAM) itself.
    Implemented to the J2534 API; verify against your device on the bench."""

    protocol = "j1939"

    _CAN = 5                       # ProtocolID CAN
    _CAN_29BIT_ID = 0x00000100     # TxFlags / RxStatus extended-id bit
    _PASS_FILTER = 1

    def __init__(self, dll_path: str, baud: int = 250000):
        self.dll_path = dll_path
        self.baud = baud
        self._api = None
        self._device = None
        self._channel = None
        self._filter = None

    def _check(self, rc: int, what: str) -> None:
        if rc != 0:
            raise RuntimeError(f"{what} failed (J2534 error {rc})")

    def open(self) -> None:
        try:
            self._api = ctypes.windll.LoadLibrary(self.dll_path)
        except OSError as exc:
            raise RuntimeError(
                f"Could not load J2534 DLL {self.dll_path!r} ({exc}). "
                "Install the adapter's J2534 driver; match Python bitness to "
                "the DLL.")
        dev = ctypes.c_ulong()
        self._check(self._api.PassThruOpen(None, ctypes.byref(dev)),
                    "PassThruOpen")
        self._device = dev
        chan = ctypes.c_ulong()
        self._check(self._api.PassThruConnect(dev, self._CAN, self._CAN_29BIT_ID,
                                              self.baud, ctypes.byref(chan)),
                    "PassThruConnect")
        self._channel = chan
        self._start_pass_filter()

    def _start_pass_filter(self) -> None:
        # Pass-all: mask 0 / pattern 0 so every frame is received.
        mask = self._make_msg(0, b"\x00\x00\x00\x00")
        patt = self._make_msg(0, b"\x00\x00\x00\x00")
        fid = ctypes.c_ulong()
        self._check(self._api.PassThruStartMsgFilter(
            self._channel, self._PASS_FILTER,
            ctypes.byref(mask), ctypes.byref(patt), None,
            ctypes.byref(fid)), "PassThruStartMsgFilter")
        self._filter = fid

    def _make_msg(self, tx_flags: int, payload: bytes) -> _PassThruMsg:
        msg = _PassThruMsg()
        msg.ProtocolID = self._CAN
        msg.TxFlags = tx_flags
        msg.DataSize = len(payload)
        for i, b in enumerate(payload):
            msg.Data[i] = b
        return msg

    def close(self) -> None:
        if self._api and self._channel is not None:
            self._api.PassThruDisconnect(self._channel)
            self._channel = None
        if self._api and self._device is not None:
            self._api.PassThruClose(self._device)
            self._device = None

    def send(self, data: bytes, can_id: int = 0) -> None:
        if self._api is None or self._channel is None:
            raise RuntimeError("transport not open")
        payload = (can_id & 0x1FFFFFFF).to_bytes(4, "big") + data
        msg = self._make_msg(self._CAN_29BIT_ID, payload)
        count = ctypes.c_ulong(1)
        self._check(self._api.PassThruWriteMsgs(
            self._channel, ctypes.byref(msg), ctypes.byref(count), 100),
            "PassThruWriteMsgs")

    def recv(self, timeout: float = 1.0) -> Optional[CanFrame]:
        if self._api is None or self._channel is None:
            raise RuntimeError("transport not open")
        msg = _PassThruMsg()
        count = ctypes.c_ulong(1)
        rc = self._api.PassThruReadMsgs(
            self._channel, ctypes.byref(msg), ctypes.byref(count),
            int(timeout * 1000))
        if rc != 0 or count.value == 0 or msg.DataSize < 4:
            return None
        raw = bytes(msg.Data[:msg.DataSize])
        can_id = int.from_bytes(raw[:4], "big")
        return CanFrame(can_id, raw[4:])


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


# ---------------------------------------------------------------------------
# Adapter discovery -- populate the GUI dropdown with what's actually installed
# ---------------------------------------------------------------------------

@dataclass
class Adapter:
    """A concrete, ready-to-open transport choice for the dropdown."""
    label: str
    kind: str                 # simulation | pythoncan | rp1210 | j2534 | socketcan
    interface: str = ""
    channel: str = ""
    dll: str = ""
    protocol: str = "j1939"

    def make(self) -> Transport:
        if self.kind == "simulation":
            from . import comms                          # noqa: PLC0415
            return comms.SimulationTransport(responder=comms.SimulatedEcu().respond)
        if self.kind == "pythoncan":
            return PythonCanTransport(interface=self.interface, channel=self.channel)
        if self.kind == "rp1210":
            return Rp1210Transport(self.dll, protocol=self.protocol)
        if self.kind == "j2534":
            return J2534Transport(self.dll)
        if self.kind == "socketcan":
            return SocketCanTransport(self.channel)
        raise ValueError(f"unknown adapter kind {self.kind!r}")


def _discover_pythoncan() -> List[Adapter]:
    out: List[Adapter] = []
    try:
        import can                                        # noqa: PLC0415
        for cfg in can.interface.detect_available_configs():
            iface = cfg.get("interface", "")
            chan = str(cfg.get("channel", ""))
            out.append(Adapter(
                label=f"{iface}:{chan} (python-can)",
                kind="pythoncan", interface=iface, channel=chan))
    except Exception:                                     # library missing / probe error
        pass
    return out


def _discover_rp1210() -> List[Adapter]:
    out: List[Adapter] = []
    try:
        import configparser                               # noqa: PLC0415
        windir = os.environ.get("WINDIR", r"C:\Windows")
        root = configparser.ConfigParser()
        root.read(os.path.join(windir, "RP121032.ini"))
        vendors = root.get("RP1210Support", "APIImplementations", fallback="")
        for name in [v.strip() for v in vendors.split(",") if v.strip()]:
            vp = configparser.ConfigParser()
            vp.read(os.path.join(windir, name + ".ini"))
            vname = vp.get("VendorInformation", "Name", fallback=name)
            for sect in vp.sections():
                if sect.lower().startswith("deviceinformation"):
                    dev = vp.get(sect, "DeviceDescription",
                                 fallback=vp.get(sect, "DeviceName", fallback=""))
                    out.append(Adapter(
                        label=f"{vname} - {dev} (RP1210)".replace(" - ", " ", 0),
                        kind="rp1210", dll=name))
            if not any(a.dll == name for a in out):
                out.append(Adapter(label=f"{vname} (RP1210)", kind="rp1210", dll=name))
    except Exception:
        pass
    return out


def _discover_j2534() -> List[Adapter]:
    out: List[Adapter] = []
    try:
        import winreg                                     # noqa: PLC0415
        for hive_path in (r"SOFTWARE\WOW6432Node\PassThruSupport.04.04",
                          r"SOFTWARE\PassThruSupport.04.04"):
            try:
                base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_path)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                try:
                    k = winreg.OpenKey(base, sub)
                    name = winreg.QueryValueEx(k, "Name")[0]
                    dll = winreg.QueryValueEx(k, "FunctionLibrary")[0]
                    out.append(Adapter(label=f"{name} (J2534)", kind="j2534", dll=dll))
                except OSError:
                    continue
    except Exception:
        pass
    return out


def _discover_socketcan() -> List[Adapter]:
    out: List[Adapter] = []
    net = "/sys/class/net"
    try:
        for iface in sorted(os.listdir(net)):
            if iface.startswith(("can", "vcan", "slcan")):
                out.append(Adapter(label=f"{iface} (SocketCAN)",
                                   kind="socketcan", channel=iface))
    except OSError:
        pass
    return out


def discover_adapters() -> List[Adapter]:
    """Scan the machine for usable adapters. Simulation is always first."""
    adapters = [Adapter(label="Simulation (no hardware)", kind="simulation")]
    for finder in (_discover_pythoncan, _discover_socketcan,
                   _discover_rp1210, _discover_j2534):
        adapters.extend(finder())
    return adapters


def list_backends() -> List[str]:
    return ["Simulation", "python-can (USB-CAN)", "RP1210 (J1939/J1708)",
            "J2534 (CAN)", "SocketCAN"]
