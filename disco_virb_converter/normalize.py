from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from disco_virb_converter.models import ParsedFlight, TelemetryRow

GPS_INVALID_SENTINEL = 500.0
CORE_FIELDS = {
    "time",
    "product_gps_available",
    "product_gps_longitude",
    "product_gps_latitude",
    "controller_gps_longitude",
    "controller_gps_latitude",
    "altitude",
    "speed",
}


def normalize_flight(parsed: ParsedFlight, offset_seconds: float = 0.0) -> tuple[list[TelemetryRow], list[str], int]:
    warnings = list(parsed.warnings)
    start_time = _parse_start_time(parsed.metadata.get("date"), warnings)
    rows: list[TelemetryRow] = []
    skipped = 0
    fallback_warned = False
    previous: TelemetryRow | None = None

    for raw in parsed.raw_rows:
        time_ms = _coerce_int(raw.get("time"))
        if time_ms is None:
            skipped += 1
            continue

        position = _pick_position(raw)
        if position is None:
            skipped += 1
            continue
        lat, lon, position_source = position
        if position_source == "controller" and not fallback_warned:
            warnings.append("Falling back to controller GPS for at least one row because product GPS was unavailable")
            fallback_warned = True

        timestamp = start_time + timedelta(milliseconds=time_ms, seconds=offset_seconds)
        altitude = _coerce_float(raw.get("altitude"))
        speed = _coerce_float(raw.get("speed"))
        if speed is None:
            speed = _vector_speed(raw)

        distance = previous.distance_m if previous else 0.0
        if previous is not None:
            step = haversine_m(previous.latitude, previous.longitude, lat, lon)
            if step >= 0:
                distance += step
            if speed is None:
                delta_s = (timestamp - previous.timestamp_utc).total_seconds()
                if delta_s > 0:
                    speed = step / delta_s

        extras = {key: value for key, value in raw.items() if key not in CORE_FIELDS}
        row = TelemetryRow(
            time_ms=time_ms,
            timestamp_utc=timestamp.astimezone(timezone.utc),
            latitude=lat,
            longitude=lon,
            altitude_m=altitude,
            speed_m_s=speed,
            distance_m=distance,
            position_source=position_source,
            extras=extras,
        )
        rows.append(row)
        previous = row

    if not rows:
        warnings.append("No rows with valid timestamps and GPS coordinates were found")
    return rows, warnings, skipped


def flight_name(parsed: ParsedFlight) -> str:
    candidate = parsed.metadata.get("uuid") or parsed.metadata.get("product_name") or parsed.source_path.stem
    if parsed.source_path.suffix.lower() == ".zip" and str(candidate).lower().endswith(".json"):
        candidate = str(candidate)[:-5]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(candidate)).strip("._-")
    return name or "disco_flight"


def default_output_dir(input_path: Path, name: str) -> Path:
    return input_path.parent / f"{name}_virb_export"


def _parse_start_time(value: Any, warnings: list[str]) -> datetime:
    if not value:
        warnings.append("Flight date missing; using current UTC time for output timestamps")
        return datetime.now(timezone.utc).replace(microsecond=0)

    text = str(value).strip()
    formats = [
        "%Y-%m-%dT%H%M%S%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H%M%SZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S%z",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue

    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            warnings.append(f"Flight date has no timezone; treating as UTC: {text}")
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        warnings.append(f"Could not parse flight date {text!r}; using current UTC time")
        return datetime.now(timezone.utc).replace(microsecond=0)


def _pick_position(raw: dict[str, Any]) -> tuple[float, float, str] | None:
    product_available = raw.get("product_gps_available")
    product_lat = _coerce_float(raw.get("product_gps_latitude"))
    product_lon = _coerce_float(raw.get("product_gps_longitude"))
    if (product_available is True or product_available is None) and _valid_coord(product_lat, product_lon):
        return product_lat, product_lon, "product"
    if _valid_coord(product_lat, product_lon):
        return product_lat, product_lon, "product"

    controller_lat = _coerce_float(raw.get("controller_gps_latitude"))
    controller_lon = _coerce_float(raw.get("controller_gps_longitude"))
    if _valid_coord(controller_lat, controller_lon):
        return controller_lat, controller_lon, "controller"
    return None


def _valid_coord(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    if lat == GPS_INVALID_SENTINEL or lon == GPS_INVALID_SENTINEL:
        return False
    if lat == 0.0 and lon == 0.0:
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _vector_speed(raw: dict[str, Any]) -> float | None:
    parts = [_coerce_float(raw.get(name)) for name in ("speed_vx", "speed_vy", "speed_vz")]
    if all(part is None for part in parts):
        return None
    return math.sqrt(sum((part or 0.0) ** 2 for part in parts))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return radius * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
