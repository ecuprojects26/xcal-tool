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

from . import j1587, j1939, modules
from .transport import CanFrame, SimulationTransport, Transport


@dataclass
class EcuInfo:
    ecm_code: str = ""
    part_number: str = ""
    calibration_id: str = ""     # calibration / ECFG version
    serial: str = ""             # engine serial number (ESN)
    vin: str = ""
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
    def _send_pgn(self, pgn: int, body: bytes,
                  dest: int = j1939.GLOBAL_ADDRESS, priority: int = 6) -> None:
        """Send a J1939 message. Raw-CAN transports get BAM fragmentation for
        payloads > 8 bytes; transports that reassemble TP themselves (RP1210)
        get the whole message in one send."""
        if len(body) <= 8 or self.transport.reassembles_tp:
            self.transport.send(body, j1939.pgn_to_canid(pgn, self.source, priority, dest))
        else:
            for cid, frame in j1939.build_tp_bam(pgn, body, self.source, priority):
                self.transport.send(frame, cid)

    def _collect(self, pgn: int, timeout: float = 1.0) -> Optional[bytes]:
        """Read frames until ``pgn`` arrives, reassembling BAM multi-packet
        responses when the data exceeds 8 bytes."""
        bam_total = 0
        bam_buf = bytearray()
        for _ in range(4096):
            frame = self.transport.recv(timeout)
            if frame is None:
                return None
            fpgn = j1939.canid_to_pgn(frame.can_id)
            if fpgn == pgn:                                 # single-frame answer
                return frame.data
            if fpgn == j1939.PGN_TP_CM:
                bam = j1939.parse_tp_cm_bam(frame.data)
                if bam and bam[2] == pgn:                   # BAM announces our PGN
                    bam_total = bam[0]
                    bam_buf = bytearray()
            elif fpgn == j1939.PGN_TP_DT and bam_total:
                bam_buf += frame.data[1:8]
                if len(bam_buf) >= bam_total:
                    return bytes(bam_buf[:bam_total])
        return None

    def _request_pgn(self, pgn: int, timeout: float = 1.0) -> Optional[bytes]:
        """Request ``pgn`` and return its (possibly multi-packet) payload."""
        self._send_pgn(j1939.PGN_REQUEST, j1939.request_pgn(pgn),
                       dest=j1939.ENGINE_ADDRESS)
        return self._collect(pgn, timeout)

    def identify(self) -> EcuInfo:
        info = EcuInfo()
        if self.protocol == "j1939":
            comp = self._request_pgn(j1939.PGN_COMPONENT_ID)
            if comp:
                c = j1939.decode_component_id(comp)
                info.make, info.model = c["make"], c["model"]
                info.serial = c["serial"]
            vin = self._request_pgn(j1939.PGN_VEHICLE_ID)
            if vin:
                info.vin = j1939.decode_vin(vin)
            ecu = self._request_pgn(j1939.PGN_ECU_ID)
            if ecu:
                e = j1939.decode_ecu_id(ecu)
                info.part_number = e["part_number"]
                if e["serial"] and not info.serial:
                    info.serial = e["serial"]
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

# -- Seed/key security (pluggable; nothing proprietary shipped) -------------

class SecurityProvider(abc.ABC):
    """Turns an ECU seed into the unlock key. Real Cummins ECMs need the
    operator's own licensed/authorized implementation -- none is shipped here."""

    @abc.abstractmethod
    def key_from_seed(self, seed: int, level: int = 1) -> int:
        ...


class DemoSecurityProvider(SecurityProvider):
    """Matches the built-in :class:`SimulatedEcu` ONLY, so the read/write flow
    can be exercised offline. This is a toy transform, NOT a Cummins algorithm,
    and will not unlock any real ECU."""

    def key_from_seed(self, seed: int, level: int = 1) -> int:
        return (seed ^ 0xA5A5) & 0xFFFF


class SecurityError(RuntimeError):
    pass


# -- Simulated ECU ----------------------------------------------------------

class SimulatedEcu:
    """A fake Cummins ECM: answers J1939 request-PGNs and implements a
    DM14/DM15/DM16 memory-access state machine over a flash buffer, including a
    demo seed/key gate, so read/write/verify can be tested with no hardware."""

    def __init__(self, image_size: int = 0x4000, seed: int = 0x1234):
        self.component_id = b"Cummins*CM2450*79512345*UNIT01"
        self.software_id = b"\x01CHR-CC-DP-MY19-V51.19.09.02*"
        self.vin = b"3C63R3EL8KG512345*"
        self.ecu_id = b"4353993*79512345*Engine*ECM*Cummins*"
        self.active = [
            j1939.J1939Dtc(spn=3251, fmi=2, occurrence_count=5),   # DPF pressure
            j1939.J1939Dtc(spn=1569, fmi=31, occurrence_count=1),  # fuel derate
        ]
        self.previously_active = [
            j1939.J1939Dtc(spn=629, fmi=12, occurrence_count=9),   # ECM
        ]
        # Flash buffer, pre-filled with a recognisable pattern.
        self.memory = bytearray((i & 0xFF) for i in range(image_size))
        self.seed = seed
        self.expected_key = (seed ^ 0xA5A5) & 0xFFFF
        self.unlocked = False
        self._pending_write = None          # (address, num_bytes)
        self._rx_bam = None                 # (pgn, total, buf) incoming BAM

    # -- incoming frame handling (with BAM reassembly) ---------------------
    def respond(self, frame: CanFrame) -> List[CanFrame]:
        pgn = j1939.canid_to_pgn(frame.can_id)
        if pgn == j1939.PGN_TP_CM:
            bam = j1939.parse_tp_cm_bam(frame.data)
            if bam:
                self._rx_bam = (bam[2], bam[0], bytearray())
            return []
        if pgn == j1939.PGN_TP_DT and self._rx_bam is not None:
            tp_pgn, total, buf = self._rx_bam
            buf += frame.data[1:8]
            if len(buf) >= total:
                self._rx_bam = None
                return self._handle(tp_pgn, bytes(buf[:total]))
            return []
        return self._handle(pgn, frame.data)

    # -- dispatch a complete message --------------------------------------
    def _handle(self, pgn: int, data: bytes) -> List[CanFrame]:
        if pgn == j1939.PGN_REQUEST and len(data) >= 3:
            req = data[0] | (data[1] << 8) | (data[2] << 16)
            return self._handle_request(req)
        if pgn == j1939.PGN_DM14:
            return self._handle_dm14(data)
        if pgn == j1939.PGN_DM16:
            return self._handle_dm16(data)
        return []

    def _emit(self, pgn: int, body: bytes) -> List[CanFrame]:
        sa = j1939.ENGINE_ADDRESS
        if len(body) <= 8:
            return [CanFrame(j1939.pgn_to_canid(pgn, sa), body)]
        return [CanFrame(cid, fr) for cid, fr in j1939.build_tp_bam(pgn, body, sa)]

    def _handle_request(self, req: int) -> List[CanFrame]:
        table = {
            j1939.PGN_COMPONENT_ID: self.component_id,
            j1939.PGN_VEHICLE_ID: self.vin,
            j1939.PGN_ECU_ID: self.ecu_id,
            j1939.PGN_SOFTWARE_ID: self.software_id,
            j1939.PGN_DM1: j1939.encode_dm(self.active),
            j1939.PGN_DM2: j1939.encode_dm(self.previously_active),
        }
        if req in table:
            return self._emit(req, table[req])
        if req == j1939.PGN_DM11:
            self.active = []
        elif req == j1939.PGN_DM3:
            self.previously_active = []
        return []

    def _handle_dm14(self, data: bytes) -> List[CanFrame]:
        m = j1939.decode_dm14(data)
        cmd = m["command"]
        if cmd == j1939.CMD_STATUS_REQUEST:
            if m["key"] == self.expected_key:
                self.unlocked = True
                return self._emit(j1939.PGN_DM15,
                                  j1939.encode_dm15(0, j1939.STATUS_PROCEED, seed=0))
            return self._emit(j1939.PGN_DM15,
                              j1939.encode_dm15(0, j1939.STATUS_PROCEED, seed=self.seed))
        if not self.unlocked:
            return self._emit(j1939.PGN_DM15,
                              j1939.encode_dm15(0, j1939.STATUS_OP_FAILED, error=1))
        if cmd == j1939.CMD_READ:
            addr, n = m["address"], m["num_bytes"]
            chunk = bytes(self.memory[addr:addr + n])
            return (self._emit(j1939.PGN_DM15, j1939.encode_dm15(n, j1939.STATUS_PROCEED))
                    + self._emit(j1939.PGN_DM16, j1939.encode_dm16(chunk)))
        if cmd == j1939.CMD_WRITE:
            self._pending_write = (m["address"], m["num_bytes"])
            return self._emit(j1939.PGN_DM15,
                              j1939.encode_dm15(m["num_bytes"], j1939.STATUS_PROCEED))
        if cmd == j1939.CMD_ERASE:
            addr, n = m["address"], m["num_bytes"]
            self.memory[addr:addr + n] = b"\xFF" * n
            return self._emit(j1939.PGN_DM15,
                              j1939.encode_dm15(0, j1939.STATUS_OP_COMPLETED))
        return self._emit(j1939.PGN_DM15,
                          j1939.encode_dm15(0, j1939.STATUS_OP_FAILED, error=2))

    def _handle_dm16(self, data: bytes) -> List[CanFrame]:
        if self._pending_write is None:
            return []
        addr, n = self._pending_write
        self._pending_write = None
        payload = j1939.decode_dm16(data)[:n]
        self.memory[addr:addr + len(payload)] = payload
        return self._emit(j1939.PGN_DM15,
                          j1939.encode_dm15(0, j1939.STATUS_OP_COMPLETED))


# -- Flash read/write over J1939 memory access ------------------------------

Progress = Optional[Callable[[int, int], None]]


class J1939Flasher:
    """Reads/writes an ECU flash image over J1939 DM14/DM15/DM16.

    Requires an unlocked session; unlocking needs a :class:`SecurityProvider`
    for the target ECU. Reads assemble a raw ``.bin`` from the module profile's
    regions; writes are backup-first (caller keeps the returned backup) and
    verified by read-back.
    """

    def __init__(self, link: DiagnosticLink, profile: modules.ModuleProfile,
                 security: Optional[SecurityProvider] = None,
                 block_size: int = 256, timeout: float = 2.0):
        self.link = link
        self.profile = profile
        self.security = security
        self.block_size = block_size
        self.timeout = timeout

    def connect(self) -> None:
        self.link.connect()

    def disconnect(self) -> None:
        self.link.disconnect()

    def identify(self) -> EcuInfo:
        return self.link.identify()

    # -- security ----------------------------------------------------------
    def unlock(self) -> None:
        self.link._send_pgn(j1939.PGN_DM14,
                            j1939.encode_dm14(0, j1939.CMD_STATUS_REQUEST, 0),
                            dest=j1939.ENGINE_ADDRESS)
        resp = self.link._collect(j1939.PGN_DM15, self.timeout)
        if resp is None:
            raise SecurityError("no DM15 response to status request")
        st = j1939.decode_dm15(resp)
        if st["seed"] == 0 and st["status"] == j1939.STATUS_PROCEED:
            return                                          # already unlocked
        if self.security is None:
            raise SecurityError(
                "ECU is locked (seed 0x%04X); an authorized SecurityProvider "
                "is required to compute the key." % st["seed"])
        key = self.security.key_from_seed(st["seed"])
        self.link._send_pgn(j1939.PGN_DM14,
                            j1939.encode_dm14(0, j1939.CMD_STATUS_REQUEST, 0, key=key),
                            dest=j1939.ENGINE_ADDRESS)
        resp = self.link._collect(j1939.PGN_DM15, self.timeout)
        if resp is None or j1939.decode_dm15(resp)["status"] != j1939.STATUS_PROCEED:
            raise SecurityError("unlock rejected (wrong key)")

    # -- read --------------------------------------------------------------
    def read_region(self, address: int, size: int, progress: Progress = None) -> bytes:
        out = bytearray()
        done = 0
        while done < size:
            n = min(self.block_size, size - done)
            self.link._send_pgn(j1939.PGN_DM14,
                                j1939.encode_dm14(n, j1939.CMD_READ, address + done),
                                dest=j1939.ENGINE_ADDRESS)
            r = self.link._collect(j1939.PGN_DM15, self.timeout)
            if r is None or j1939.decode_dm15(r)["status"] != j1939.STATUS_PROCEED:
                raise IOError("read denied at 0x%08X" % (address + done))
            d = self.link._collect(j1939.PGN_DM16, self.timeout)
            if d is None:
                raise IOError("no data at 0x%08X" % (address + done))
            out += j1939.decode_dm16(d)
            done += n
            if progress:
                progress(done, size)
        return bytes(out[:size])

    def read_image(self, progress: Progress = None) -> bytes:
        self.unlock()
        image = bytearray(b"\xFF" * self.profile.image_size)
        total = self.profile.total_bytes()
        done = 0

        def region_progress(cur, _size):
            if progress:
                progress(done + cur, total)

        for region in self.profile.regions:
            data = self.read_region(region.address, region.size, region_progress)
            image[region.image_offset:region.image_offset + len(data)] = data
            done += region.size
        return bytes(image)

    # -- write -------------------------------------------------------------
    def write_region(self, address: int, data: bytes, progress: Progress = None) -> None:
        # Erase, then write in blocks.
        self.link._send_pgn(j1939.PGN_DM14,
                            j1939.encode_dm14(len(data), j1939.CMD_ERASE, address),
                            dest=j1939.ENGINE_ADDRESS)
        r = self.link._collect(j1939.PGN_DM15, self.timeout)
        if r is None or j1939.decode_dm15(r)["status"] not in (
                j1939.STATUS_OP_COMPLETED, j1939.STATUS_PROCEED):
            raise IOError("erase denied at 0x%08X" % address)
        done = 0
        while done < len(data):
            n = min(self.block_size, len(data) - done)
            chunk = data[done:done + n]
            self.link._send_pgn(j1939.PGN_DM14,
                                j1939.encode_dm14(n, j1939.CMD_WRITE, address + done),
                                dest=j1939.ENGINE_ADDRESS)
            r = self.link._collect(j1939.PGN_DM15, self.timeout)
            if r is None or j1939.decode_dm15(r)["status"] != j1939.STATUS_PROCEED:
                raise IOError("write denied at 0x%08X" % (address + done))
            self.link._send_pgn(j1939.PGN_DM16, j1939.encode_dm16(chunk),
                                dest=j1939.ENGINE_ADDRESS)
            r = self.link._collect(j1939.PGN_DM15, self.timeout)
            if r is None or j1939.decode_dm15(r)["status"] != j1939.STATUS_OP_COMPLETED:
                raise IOError("write not confirmed at 0x%08X" % (address + done))
            done += n
            if progress:
                progress(done, len(data))

    def write_image(self, image: bytes, progress: Progress = None,
                    verify: bool = True) -> bytes:
        """Write ``image`` region-by-region and verify by read-back.
        Returns the pre-write backup so the caller can save it."""
        self.unlock()
        backup = self.read_image()
        total = self.profile.total_bytes()
        done = 0

        def region_progress(cur, _size):
            if progress:
                progress(done + cur, total)

        for region in self.profile.regions:
            seg = image[region.image_offset:region.image_offset + region.size]
            self.write_region(region.address, seg, region_progress)
            done += region.size
        if verify:
            readback = self.read_image()
            if readback != image:
                raise IOError("verify failed: read-back does not match written image")
        return backup


def simulation_link() -> DiagnosticLink:
    """A ready-to-use diagnostic link backed by a simulated ECM."""
    ecu = SimulatedEcu()
    return DiagnosticLink(SimulationTransport(responder=ecu.respond))


def simulation_flasher(profile: Optional[modules.ModuleProfile] = None) -> J1939Flasher:
    """A flasher wired to a simulated ECM sized to a small demo profile."""
    if profile is None:
        profile = modules.ModuleProfile(
            key="SIM", name="Simulated ECM", description="offline demo",
            protocol="j1939", image_size=0x4000,
            regions=[modules.Region("cal", 0x0, 0x4000, 0x0)])
    ecu = SimulatedEcu(image_size=profile.image_size)
    link = DiagnosticLink(SimulationTransport(responder=ecu.respond))
    return J1939Flasher(link, profile, security=DemoSecurityProvider())


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
