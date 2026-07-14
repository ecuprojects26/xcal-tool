"""SAE J1939 (CAN) diagnostics helpers.

Pure encode/decode logic -- no hardware. Covers the parts we need for a simple
diagnostic session on Cummins Core-II modules (CM23xx/CM24xx/CM2450):

* build a "request PGN" frame (PGN 59904)
* decode active / previously-active DTCs (DM1 / DM2)
* build the "clear DTCs" requests (DM11 active, DM3 previously-active)
* decode Component ID (PGN 65259) and Software ID (PGN 65242)

A J1939 DTC packs a 19-bit SPN (Suspect Parameter Number), a 5-bit FMI
(Failure Mode Indicator) and an occurrence count into 4 bytes, using the
common "version 4" SPN layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

# Parameter Group Numbers we use.
PGN_REQUEST = 59904          # 0xEA00
PGN_DM1 = 65226              # 0xFECA  active DTCs
PGN_DM2 = 65227              # 0xFECB  previously active DTCs
PGN_DM3 = 65228             # 0xFECC  clear previously active DTCs
PGN_DM11 = 65235            # 0xFED3  clear active DTCs
PGN_COMPONENT_ID = 65259    # 0xFEEB  Make*Model*Serial*Unit
PGN_SOFTWARE_ID = 65242     # 0xFEDA
PGN_VEHICLE_ID = 65260      # 0xFEEC  VIN
PGN_ECU_ID = 64965          # 0xFDC5  ECU part#*serial*location*type*mfr
PGN_ENGINE_CONFIG = 65251   # 0xFEE3  Engine Configuration (rated torque/speed)
PGN_TP_CM = 60416           # 0xEC00  transport-protocol connection management
PGN_TP_DT = 60160           # 0xEB00  transport-protocol data transfer
TP_BAM = 0x20               # broadcast-announce control byte

GLOBAL_ADDRESS = 0xFF
ENGINE_ADDRESS = 0x00       # Cummins engine ECM default source address


@dataclass
class J1939Dtc:
    spn: int
    fmi: int
    occurrence_count: int = 0
    conversion_method: int = 0

    def __str__(self) -> str:
        return f"SPN {self.spn} FMI {self.fmi} (x{self.occurrence_count})"


def canid_to_pgn(can_id: int) -> int:
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    return (pf << 8) if pf < 240 else ((pf << 8) | ps)


def canid_source(can_id: int) -> int:
    return can_id & 0xFF


def pgn_to_canid(pgn: int, source: int = 0, priority: int = 6,
                 dest: int = GLOBAL_ADDRESS) -> int:
    pf = (pgn >> 8) & 0xFF
    ps = pgn & 0xFF
    if pf < 240:                                   # PDU1 (destination-specific)
        cid = (priority << 26) | (pf << 16) | (dest << 8) | source
    else:                                          # PDU2 (broadcast)
        cid = (priority << 26) | (pf << 16) | (ps << 8) | source
    return cid & 0x1FFFFFFF


def request_pgn(pgn: int, dest: int = GLOBAL_ADDRESS) -> bytes:
    """Body of a request for ``pgn`` (3 bytes, little-endian). ``dest`` is the
    target address (caller builds the 29-bit CAN id)."""
    return bytes([pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF])


def encode_dtc(dtc: J1939Dtc) -> bytes:
    spn, fmi = dtc.spn, dtc.fmi
    return bytes([
        spn & 0xFF,
        (spn >> 8) & 0xFF,
        ((spn >> 16) & 0x07) << 5 | (fmi & 0x1F),
        ((dtc.conversion_method & 0x01) << 7) | (dtc.occurrence_count & 0x7F),
    ])


def decode_dtc(b: bytes) -> J1939Dtc:
    spn = b[0] | (b[1] << 8) | ((b[2] >> 5) & 0x07) << 16
    fmi = b[2] & 0x1F
    return J1939Dtc(
        spn=spn,
        fmi=fmi,
        occurrence_count=b[3] & 0x7F,
        conversion_method=(b[3] >> 7) & 0x01,
    )


def decode_dm(data: bytes) -> List[J1939Dtc]:
    """Decode a DM1/DM2 payload: 2 lamp-status bytes then 4 bytes per DTC."""
    body = data[2:]
    out: List[J1939Dtc] = []
    for i in range(0, len(body) - 3, 4):
        chunk = body[i:i + 4]
        if chunk == b"\x00\x00\x00\x00" or chunk == b"\xFF\xFF\xFF\xFF":
            continue
        out.append(decode_dtc(chunk))
    return out


def encode_dm(dtcs: List[J1939Dtc], lamp_status: int = 0, lamp_flash: int = 0xFF) -> bytes:
    """Build a DM1/DM2 payload (2 lamp bytes + DTCs). If there are no DTCs the
    standard "no faults" body is 6 bytes of 0x00/0xFF."""
    out = bytes([lamp_status, lamp_flash])
    if not dtcs:
        return out + b"\x00\x00\x00\x00"
    for d in dtcs:
        out += encode_dtc(d)
    return out


def decode_component_id(data: bytes) -> dict:
    fields = data.split(b"*")
    keys = ["make", "model", "serial", "unit"]
    text = [f.decode("latin-1", "replace").strip("\x00 ") for f in fields]
    return {k: (text[i] if i < len(text) else "") for i, k in enumerate(keys)}


# --- Memory access (DM14 request / DM15 response / DM16 data) --------------
PGN_DM14 = 55552            # 0xD900  memory access request  (tester -> ECM)
PGN_DM15 = 54528            # 0xD500  memory access response (ECM -> tester)
PGN_DM16 = 53760            # 0xD200  binary data transfer

# DM14 commands
CMD_ERASE = 0
CMD_READ = 1
CMD_WRITE = 2
CMD_STATUS_REQUEST = 3
CMD_OP_COMPLETED = 4
CMD_OP_FAILED = 5
CMD_BOOT_LOAD = 6
CMD_EDCP_GENERATION = 7

# DM15 status
STATUS_PROCEED = 0
STATUS_BUSY = 1
STATUS_OP_COMPLETED = 4
STATUS_OP_FAILED = 5

NO_KEY = 0xFFFF


def encode_dm14(num_bytes: int, command: int, address: int,
                key: int = NO_KEY, pointer_type: int = 0) -> bytes:
    b0 = num_bytes & 0xFF
    b1 = ((num_bytes >> 8) & 0x07) | ((pointer_type & 0x01) << 3) | ((command & 0x07) << 5)
    return bytes([b0, b1]) + (address & 0xFFFFFFFF).to_bytes(4, "little") + \
        (key & 0xFFFF).to_bytes(2, "little")


def decode_dm14(d: bytes) -> dict:
    return {
        "num_bytes": d[0] | ((d[1] & 0x07) << 8),
        "pointer_type": (d[1] >> 3) & 0x01,
        "command": (d[1] >> 5) & 0x07,
        "address": int.from_bytes(d[2:6], "little"),
        "key": int.from_bytes(d[6:8], "little"),
    }


def encode_dm15(num_bytes: int, status: int, seed: int = 0, error: int = 0) -> bytes:
    b0 = num_bytes & 0xFF
    b1 = ((num_bytes >> 8) & 0x07) | ((status & 0x07) << 5)
    return bytes([b0, b1]) + (seed & 0xFFFF).to_bytes(2, "little") + \
        bytes([error & 0xFF, 0xFF, 0xFF, 0xFF])


def decode_dm15(d: bytes) -> dict:
    return {
        "num_bytes": d[0] | ((d[1] & 0x07) << 8),
        "status": (d[1] >> 5) & 0x07,
        "seed": int.from_bytes(d[2:4], "little"),
        "error": d[4] if len(d) > 4 else 0,
    }


def encode_dm16(data: bytes) -> bytes:
    # DM16 carries the raw bytes; the count comes from the preceding DM15's
    # num_bytes field (blocks can exceed the 255 a single length byte holds).
    return bytes(data)


def decode_dm16(d: bytes) -> bytes:
    return bytes(d)


def build_tp_bam(pgn: int, data: bytes, source: int,
                 priority: int = 7) -> List[tuple]:
    """Split ``data`` into a broadcast (BAM) sequence for a large message.

    Returns [(can_id, frame_bytes), ...]: one TP.CM (BAM) announce frame then
    one TP.DT frame per 7-byte packet. Used to transmit responses that don't
    fit in a single 8-byte CAN frame (Component ID, VIN, multi-DTC DM1, ...).
    """
    n = len(data)
    npackets = (n + 6) // 7
    cm = bytes([TP_BAM, n & 0xFF, (n >> 8) & 0xFF, npackets, 0xFF,
                pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF])
    frames = [(pgn_to_canid(PGN_TP_CM, source, priority, dest=GLOBAL_ADDRESS), cm)]
    for i in range(npackets):
        chunk = data[i * 7:(i + 1) * 7].ljust(7, b"\xFF")
        frames.append((pgn_to_canid(PGN_TP_DT, source, priority, dest=GLOBAL_ADDRESS),
                       bytes([i + 1]) + chunk))
    return frames


def parse_tp_cm_bam(data: bytes):
    """Return (total_size, num_packets, pgn) for a TP.CM BAM frame, else None."""
    if len(data) < 8 or data[0] != TP_BAM:
        return None
    total = data[1] | (data[2] << 8)
    npackets = data[3]
    pgn = data[5] | (data[6] << 8) | (data[7] << 16)
    return total, npackets, pgn


def decode_vin(data: bytes) -> str:
    return data.split(b"*")[0].decode("latin-1", "replace").strip("\x00 ")


def decode_ecu_id(data: bytes) -> dict:
    fields = [f.decode("latin-1", "replace").strip("\x00 ")
              for f in data.split(b"*")]
    keys = ["part_number", "serial", "location", "type", "manufacturer"]
    return {k: (fields[i] if i < len(fields) else "") for i, k in enumerate(keys)}


def decode_software_id(data: bytes) -> List[str]:
    # First byte is the field count; fields are '*'-separated ASCII.
    body = data[1:] if data else b""
    return [f.decode("latin-1", "replace").strip("\x00 ")
            for f in body.split(b"*") if f]


def decode_engine_config(data: bytes) -> dict:
    """Decode Engine Configuration (PGN 65251). Returns reference engine
    torque (SPN 544, 1 N*m/bit at bytes 30-31) and rated speed (SPN 189,
    0.125 rpm/bit at bytes 32-33) when the message is long enough."""
    out: dict = {}
    if len(data) >= 31:
        raw = data[29] | (data[30] << 8)
        if raw not in (0xFFFF, 0xFE00):
            out["reference_torque_nm"] = raw            # 1 N*m/bit
    if len(data) >= 33:
        raw = data[31] | (data[32] << 8)
        if raw not in (0xFFFF, 0xFE00):
            out["rated_speed_rpm"] = raw * 0.125
    return out


# --- Live telemetry (broadcast parameter groups) --------------------------
# PGNs carrying real-time engine parameters. These are broadcast on the bus
# but Cummins ECMs also answer a request for them, which is how we poll.
PGN_EEC1 = 61444            # 0xF004  Electronic Engine Controller 1
PGN_EEC2 = 61443            # 0xF003  Electronic Engine Controller 2
PGN_ET1 = 65262            # 0xFEEE  Engine Temperature 1
PGN_EFLP1 = 65263          # 0xFEEF  Engine Fluid Level/Pressure 1
PGN_IC1 = 65270            # 0xFEF6  Inlet/Exhaust Conditions 1
PGN_LFE1 = 65266           # 0xFEF2  Fuel Economy (liquid)
PGN_VEP1 = 65271           # 0xFEF7  Vehicle Electrical Power 1
PGN_HOURS = 65253          # 0xFEE5  Engine Hours/Revolutions
PGN_VDHR = 65217           # 0xFEC1  High Resolution Vehicle Distance
PGN_CCVS = 65265           # 0xFEF1  Cruise Control/Vehicle Speed
PGN_DD1 = 65276            # 0xFEFC  Dash Display (fuel level)
PGN_AT1T1 = 65110          # 0xFE56  Aftertreatment 1 Tank 1 (DEF level)


def _u16le(data: bytes, i: int):
    """Little-endian 16-bit at ``i``; None if missing or 'not available'."""
    if len(data) < i + 2:
        return None
    raw = data[i] | (data[i + 1] << 8)
    return None if raw >= 0xFE00 else raw


def _u8(data: bytes, i: int):
    if len(data) <= i:
        return None
    raw = data[i]
    return None if raw >= 0xFE else raw


def _u32le(data: bytes, i: int):
    if len(data) < i + 4:
        return None
    raw = int.from_bytes(data[i:i + 4], "little")
    return None if raw >= 0xFAFFFFFF else raw


def decode_live(pgn: int, data: bytes) -> dict:
    """Decode recognised broadcast telemetry PGNs into a {key: value} dict.
    Values are engineering units (rpm, degC, kPa, L/h, V, %, h, km, km/h).
    Missing/not-available signals are simply omitted."""
    out: dict = {}
    if pgn == PGN_EEC1:
        v = _u16le(data, 3)                       # SPN 190, 0.125 rpm/bit
        if v is not None:
            out["engine_rpm"] = round(v * 0.125, 1)
    elif pgn == PGN_EEC2:
        v = _u8(data, 1)                          # SPN 91 accel pedal, 0.4 %/bit
        if v is not None:
            out["accel_pedal_pct"] = round(v * 0.4, 1)
        v = _u8(data, 2)                          # SPN 92 engine load, 1 %/bit
        if v is not None:
            out["engine_load_pct"] = float(v)
    elif pgn == PGN_ET1:
        v = _u8(data, 0)                          # SPN 110, 1 degC/bit, -40
        if v is not None:
            out["coolant_c"] = v - 40
        v = _u8(data, 1)                          # SPN 174 fuel temp, 1 degC, -40
        if v is not None:
            out["fuel_temp_c"] = v - 40
        v = _u16le(data, 2)                       # SPN 175 oil temp, 0.03125, -273
        if v is not None:
            out["oil_temp_c"] = round(v * 0.03125 - 273, 1)
    elif pgn == PGN_EFLP1:
        v = _u8(data, 3)                          # SPN 100 oil pressure, 4 kPa/bit
        if v is not None:
            out["oil_pressure_kpa"] = v * 4
        v = _u8(data, 0)                          # SPN 94 fuel delivery, 4 kPa/bit
        if v is not None:
            out["fuel_pressure_kpa"] = v * 4
    elif pgn == PGN_IC1:
        v = _u8(data, 1)                          # SPN 102 boost, 2 kPa/bit
        if v is not None:
            out["boost_kpa"] = v * 2
        v = _u8(data, 2)                          # SPN 105 intake temp, 1 degC, -40
        if v is not None:
            out["intake_temp_c"] = v - 40
    elif pgn == PGN_LFE1:
        v = _u16le(data, 0)                       # SPN 183 fuel rate, 0.05 L/h
        if v is not None:
            out["fuel_rate_lph"] = round(v * 0.05, 1)
    elif pgn == PGN_VEP1:
        v = _u16le(data, 4)                       # SPN 168 battery, 0.05 V/bit
        if v is not None:
            out["battery_v"] = round(v * 0.05, 2)
    elif pgn == PGN_HOURS:
        v = _u32le(data, 0)                       # SPN 247 total hours, 0.05 h/bit
        if v is not None:
            out["engine_hours"] = round(v * 0.05, 1)
    elif pgn == PGN_VDHR:
        v = _u32le(data, 0)                       # SPN 917 distance, 5 m/bit
        if v is not None:
            out["distance_km"] = round(v * 5 / 1000.0, 1)
    elif pgn == PGN_CCVS:
        v = _u16le(data, 1)                       # SPN 84 speed, 1/256 km/h
        if v is not None:
            out["vehicle_speed_kmh"] = round(v / 256.0, 1)
    elif pgn == PGN_DD1:
        v = _u8(data, 1)                          # SPN 96 fuel level, 0.4 %/bit
        if v is not None:
            out["fuel_level_pct"] = round(v * 0.4, 1)
    elif pgn == PGN_AT1T1:
        v = _u8(data, 0)                          # SPN 1761 DEF level, 0.4 %/bit
        if v is not None:
            out["def_level_pct"] = round(v * 0.4, 1)
    return out
