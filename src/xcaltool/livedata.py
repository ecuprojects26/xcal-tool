"""Live engine telemetry over J1939.

Polls the broadcast parameter groups decoded in :mod:`j1939` and presents the
signals in a stable, display-friendly order with labels and units. No hardware
logic lives here -- it drives whatever :class:`~xcaltool.comms.DiagnosticLink`
is connected (including the simulator).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from . import j1939


@dataclass(frozen=True)
class LiveSignal:
    key: str
    label: str
    unit: str
    pgn: int


# Display order for the dashboard / CSV columns.
SIGNALS: List[LiveSignal] = [
    LiveSignal("engine_rpm", "Engine speed", "rpm", j1939.PGN_EEC1),
    LiveSignal("engine_load_pct", "Engine load", "%", j1939.PGN_EEC2),
    LiveSignal("accel_pedal_pct", "Accelerator", "%", j1939.PGN_EEC2),
    LiveSignal("coolant_c", "Coolant temp", "\u00b0C", j1939.PGN_ET1),
    LiveSignal("oil_temp_c", "Oil temp", "\u00b0C", j1939.PGN_ET1),
    LiveSignal("fuel_temp_c", "Fuel temp", "\u00b0C", j1939.PGN_ET1),
    LiveSignal("oil_pressure_kpa", "Oil pressure", "kPa", j1939.PGN_EFLP1),
    LiveSignal("fuel_pressure_kpa", "Fuel pressure", "kPa", j1939.PGN_EFLP1),
    LiveSignal("boost_kpa", "Boost", "kPa", j1939.PGN_IC1),
    LiveSignal("intake_temp_c", "Intake temp", "\u00b0C", j1939.PGN_IC1),
    LiveSignal("fuel_rate_lph", "Fuel rate", "L/h", j1939.PGN_LFE1),
    LiveSignal("battery_v", "Battery", "V", j1939.PGN_VEP1),
    LiveSignal("vehicle_speed_kmh", "Vehicle speed", "km/h", j1939.PGN_CCVS),
    LiveSignal("fuel_level_pct", "Fuel level", "%", j1939.PGN_DD1),
    LiveSignal("def_level_pct", "DEF level", "%", j1939.PGN_AT1T1),
    LiveSignal("engine_hours", "Engine hours", "h", j1939.PGN_HOURS),
    LiveSignal("distance_km", "Total distance", "km", j1939.PGN_VDHR),
]

# PGNs to poll (deduplicated, preserving first-seen order).
POLL_PGNS: List[int] = list(dict.fromkeys(s.pgn for s in SIGNALS))


class LiveDataReader:
    """Polls telemetry PGNs from a connected diagnostic link."""

    def __init__(self, link, pgns: Optional[List[int]] = None):
        self.link = link
        self.pgns = pgns if pgns is not None else POLL_PGNS

    def poll(self, timeout: float = 0.2) -> Dict[str, float]:
        """Request each telemetry PGN once and return the decoded signals.

        Returns only signals the ECM actually reported this cycle."""
        values: Dict[str, float] = {}
        for pgn in self.pgns:
            data = self.link._request_pgn(pgn, timeout)
            if data:
                values.update(j1939.decode_live(pgn, data))
        return values


def format_value(value: float) -> str:
    """Render a numeric signal without a trailing ``.0`` on whole numbers."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
