from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ParsedFlight:
    source_path: Path
    source_entry: str | None
    source_kind: str
    metadata: dict[str, Any]
    headers: list[str]
    raw_rows: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


@dataclass
class TelemetryRow:
    time_ms: int
    timestamp_utc: datetime
    latitude: float
    longitude: float
    altitude_m: float | None
    speed_m_s: float | None
    distance_m: float
    position_source: str
    distance_from_home_m: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversionResult:
    input_path: Path
    output_dir: Path
    flight_name: str
    fit_path: Path
    gpx_path: Path
    csv_path: Path
    manifest_path: Path
    readme_path: Path
    rows: list[TelemetryRow]
    warnings: list[str]
    skipped_rows: int
    source_kind: str
    source_entry: str | None
