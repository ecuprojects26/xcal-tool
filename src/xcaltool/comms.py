"""ECU communication: a simple diagnostic link (connect / identify / read &
clear DTCs) plus the flash read/write interface for later.

The diagnostic link runs over any :class:`~xcaltool.transport.Transport`
(simulation, RP1210, J2534, SocketCAN) and speaks either J1939 (CAN) or
J1587 (J1708). Fault codes are decoded to SPN/FMI (J1939) or PID/SID/FMI
(J1587); descriptions can be filled in from the Cummins fault-code table.

Nothing here bypasses ECU security. Reading/writing *protected* flash needs a
seed/key unlock that is Cummins-proprietary; that is a pluggable slot
(:class:`SecurityProvider`) the operator supplies when licensed/authorized.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import j1587, j1939
from .transport import CanFrame, SimulationTransport, Transport


@dataclass
class EcuInfo:
    ecm_code: str = ""
    part_number: str = ""
    calibration_id: str = ""
    serial: str = ""
    make: str = ""
    model: str = ""
    software: List[str] = field(default_factory=list)


@dataclass
class DtcResult:
    protocol: str            # "j1939" / "j1587"
    spn: int = 0
    fmi: int = 0
    pid: int = 0
    sid: int = 0
    is_sid: bool = False
    occurrence_count: int = 0
    inactive: bool = False
    description: str = ""

    def label(self) -> str:
        if self.protocol == "j1939":
            base = f"SPN {self.spn} / FMI {self.fmi}"
        else:
            base = f"{'SID' if self.is_sid else 'PID'} {self.sid or self.pid} / FMI {self.fmi}"
        if self.occurrence_count:
            base += f" (x{self.occurrence_count})"
        return base + (f" - {self.description}" if self.description else "")


ProgressCallback = Optional[Callable[[int, int], None]]


class DiagnosticLink:
    """connect -> identify / read DTCs / clear DTCs over a transport."""

    def __init__(self, transport: Transport, source_address: int = 0xF9):
        self.transport = transport
        self.protocol = transport.protocol
        self.source = source_address

    def connect(self) -> None:
        self.transport.open()

    def disconnect(self) -> None:
        self.transport.close()

    def __enter__(self) -> "DiagnosticLink":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    # -- J1939 helpers ------------------------------------------------------
    def _request_pgn(self, pgn: int, timeout: float = 1.0) -> Optional[bytes]:
        cid = j1939.pgn_to_canid(j1939.PGN_REQUEST, self.source,
                                 dest=j1939.ENGINE_ADDRESS)
        self.transport.send(j1939.request_pgn(pgn), cid)
        deadline_frames = 0
        while deadline_frames < 32:
            frame = self.transport.recv(timeout)
            if frame is None:
                return None
            if j1939.canid_to_pgn(frame.can_id) == pgn:
                return frame.data
            deadline_frames += 1
        return None

    def identify(self) -> EcuInfo:
        info = EcuInfo()
        if self.protocol == "j1939":
            comp = self._request_pgn(j1939.PGN_COMPONENT_ID)
            if comp:
                c = j1939.decode_component_id(comp)
                info.make, info.model = c["make"], c["model"]
                info.serial = c["serial"]
            soft = self._request_pgn(j1939.PGN_SOFTWARE_ID)
            if soft:
                info.software = j1939.decode_software_id(soft)
                if info.software:
                    info.calibration_id = info.software[0]
            info.ecm_code = info.model or "Cummins ECM"
        else:
            comp = self._j1587_request(j1587.PID_COMPONENT_ID)
            if comp:
                info.model = comp.decode("latin-1", "replace").strip("\x00 ")
                info.ecm_code = info.model
            soft = self._j1587_request(j1587.PID_SOFTWARE_ID)
            if soft:
                info.software = [soft.decode("latin-1", "replace").strip("\x00 ")]
                info.calibration_id = info.software[0]
        return info

    def read_dtcs(self, active: bool = True) -> List[DtcResult]:
        if self.protocol == "j1939":
            pgn = j1939.PGN_DM1 if active else j1939.PGN_DM2
            data = self._request_pgn(pgn)
            if not data:
                return []
            out = []
            for d in j1939.decode_dm(data):
                out.append(DtcResult("j1939", spn=d.spn, fmi=d.fmi,
                                     occurrence_count=d.occurrence_count,
                                     inactive=not active))
            return out
        data = self._j1587_request(j1587.PID_DIAGNOSTIC)
        if not data:
            return []
        out = []
        for d in j1587.decode_pid194(data):
            out.append(DtcResult("j1587", fmi=d.fmi, is_sid=d.is_sid,
                                 pid=0 if d.is_sid else d.code,
                                 sid=d.code if d.is_sid else 0,
                                 occurrence_count=d.occurrence_count,
                                 inactive=d.inactive or not active))
        return out

    def clear_dtcs(self, active: bool = True) -> None:
        if self.protocol == "j1939":
            pgn = j1939.PGN_DM11 if active else j1939.PGN_DM3
            cid = j1939.pgn_to_canid(j1939.PGN_REQUEST, self.source,
                                     dest=j1939.ENGINE_ADDRESS)
            self.transport.send(j1939.request_pgn(pgn), cid)
        else:
            # J1587: clear via PID 194 with an empty diagnostic request.
            msg = j1587.build_message(j1587.MID_ENGINE, j1587.PID_DIAGNOSTIC, b"\x00")
            self.transport.send(msg)

    def _j1587_request(self, pid: int, timeout: float = 1.0) -> Optional[bytes]:
        # J1708 request: MID + PID 0 (request) + requested PID.
        self.transport.send(j1587.build_message(0xAC, 0, bytes([pid])))
        frame = self.transport.recv(timeout)
        if frame is None:
            return None
        try:
            _mid, rpid, data = j1587.parse_message(frame.data)
        except ValueError:
            return None
        return data if rpid == pid else None


def annotate_descriptions(dtcs: List[DtcResult], fault_records) -> None:
    """Fill DtcResult.description from a Cummins fault-code table (matched on
    SPN + J1939 FMI). ``fault_records`` is a list of faultcodes.FaultCode."""
    index = {}
    for r in fault_records:
        if r.spn and r.j1939_fmi:
            index[(r.spn, r.j1939_fmi)] = r.description
    for d in dtcs:
        if d.protocol == "j1939":
            d.description = index.get((str(d.spn), str(d.fmi)), d.description)


# ---------------------------------------------------------------------------
# Simulation ECU (offline testing)
# ---------------------------------------------------------------------------

class SimulatedEcu:
    """A fake Cummins ECM that answers J1939 request-PGNs."""

    def __init__(self):
        self.component_id = b"Cummins*CM2450*79512345*UNIT01"
        self.software_id = b"\x01CHR-CC-DP-MY19-V51.19.09.02*"
        self.active = [
            j1939.J1939Dtc(spn=3251, fmi=2, occurrence_count=5),   # DPF pressure
            j1939.J1939Dtc(spn=1569, fmi=31, occurrence_count=1),  # fuel derate
        ]
        self.previously_active = [
            j1939.J1939Dtc(spn=629, fmi=12, occurrence_count=9),   # ECM
        ]

    def respond(self, frame: CanFrame) -> List[CanFrame]:
        if j1939.canid_to_pgn(frame.can_id) != j1939.PGN_REQUEST or len(frame.data) < 3:
            return []
        pgn = frame.data[0] | (frame.data[1] << 8) | (frame.data[2] << 16)
        sa = j1939.ENGINE_ADDRESS

        def reply(p, body):
            return CanFrame(j1939.pgn_to_canid(p, sa), body)

        if pgn == j1939.PGN_COMPONENT_ID:
            return [reply(pgn, self.component_id)]
        if pgn == j1939.PGN_SOFTWARE_ID:
            return [reply(pgn, self.software_id)]
        if pgn == j1939.PGN_DM1:
            return [reply(pgn, j1939.encode_dm(self.active))]
        if pgn == j1939.PGN_DM2:
            return [reply(pgn, j1939.encode_dm(self.previously_active))]
        if pgn == j1939.PGN_DM11:
            self.active = []
            return []
        if pgn == j1939.PGN_DM3:
            self.previously_active = []
            return []
        return []


def simulation_link() -> DiagnosticLink:
    """A ready-to-use diagnostic link backed by a simulated ECM."""
    ecu = SimulatedEcu()
    return DiagnosticLink(SimulationTransport(responder=ecu.respond))


# ---------------------------------------------------------------------------
# Flash read/write interface (security-gated; hardware backends come later)
# ---------------------------------------------------------------------------

class SecurityProvider(abc.ABC):
    """Supplies the seed/key unlock for protected read/write. Not shipped; the
    operator plugs in a licensed/authorized implementation."""

    @abc.abstractmethod
    def key_from_seed(self, seed: bytes, level: int) -> bytes:
        ...


class EcuLink(abc.ABC):
    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @abc.abstractmethod
    def identify(self) -> EcuInfo: ...

    @abc.abstractmethod
    def read_image(self, progress: ProgressCallback = None) -> bytes: ...

    @abc.abstractmethod
    def write_image(self, image: bytes, progress: ProgressCallback = None) -> None: ...

    def __enter__(self) -> "EcuLink":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()


class NotConnectedBackend(EcuLink):
    _MSG = (
        "Flash read/write needs a hardware transport and, for protected "
        "memory, a licensed security unlock (SecurityProvider). Diagnostics "
        "(identify / read / clear codes) work now via the ECU tab."
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
