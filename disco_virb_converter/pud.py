from __future__ import annotations

import gzip
import json
import math
import struct
import zipfile
from pathlib import Path
from typing import Any

from disco_virb_converter.models import ParsedFlight

GPS_INVALID_SENTINEL = 500.0
MAX_TIME_INTERVAL_MS = 10_000


class FlightParseError(ValueError):
    pass


def load_flight(path: str | Path) -> ParsedFlight:
    input_path = Path(path)
    if not input_path.exists():
        raise FlightParseError(f"Input file does not exist: {input_path}")

    suffix = input_path.suffix.lower()
    if suffix == ".zip":
        return _load_zip(input_path)
    if suffix == ".pud":
        return parse_pud_bytes(input_path.read_bytes(), input_path, None)
    if suffix == ".gz":
        text = gzip.decompress(input_path.read_bytes()).decode("utf-8", errors="replace")
        return parse_run_json_text(text, input_path, None, "gzip-json")
    if suffix in {".json", ".txt"}:
        return parse_run_json_text(input_path.read_text(encoding="utf-8"), input_path, None, "json")
    raise FlightParseError(f"Unsupported input type: {input_path.suffix}")


def _load_zip(path: Path) -> ParsedFlight:
    warnings: list[str] = []
    with zipfile.ZipFile(path, "r") as zf:
        names = [info.filename for info in zf.infolist() if not info.is_dir()]
        pud_entries = _sort_entries(names, [".pud"], prefer_prefix="raw/")
        json_entries = _sort_entries(names, [".json", ".txt"], prefer_prefix="details/")
        gz_entries = _sort_entries(names, [".gz"], prefer_prefix="details/")

        if pud_entries:
            pud_entry = pud_entries[0]
            parsed = parse_pud_bytes(zf.read(pud_entry), path, pud_entry)
            parsed.warnings.extend(warnings)
            compare_entry = json_entries[0] if json_entries else (gz_entries[0] if gz_entries else None)
            if compare_entry:
                try:
                    compare = _parse_zip_entry_as_json(zf, path, compare_entry)
                    if len(compare.raw_rows) != len(parsed.raw_rows):
                        parsed.warnings.append(
                            f"PUD row count {len(parsed.raw_rows)} differs from {compare_entry} row count {len(compare.raw_rows)}"
                        )
                except Exception as exc:  # noqa: BLE001 - compare data must not block PUD conversion
                    parsed.warnings.append(f"Could not cross-check converted JSON entry {compare_entry}: {exc}")
            return parsed

        if json_entries:
            return _parse_zip_entry_as_json(zf, path, json_entries[0])
        if gz_entries:
            return _parse_zip_entry_as_json(zf, path, gz_entries[0])

    raise FlightParseError("ZIP did not contain raw/*.pud, *.json, *.txt, or *.gz flight details")


def _sort_entries(names: list[str], suffixes: list[str], prefer_prefix: str) -> list[str]:
    matches = [name for name in names if any(name.lower().endswith(suffix) for suffix in suffixes)]
    return sorted(matches, key=lambda name: (0 if name.lower().startswith(prefer_prefix) else 1, name.lower()))


def _parse_zip_entry_as_json(zf: zipfile.ZipFile, zip_path: Path, entry: str) -> ParsedFlight:
    data = zf.read(entry)
    if entry.lower().endswith(".gz"):
        data = gzip.decompress(data)
        kind = "zip-gzip-json"
    else:
        kind = "zip-json"
    return parse_run_json_text(data.decode("utf-8", errors="replace"), zip_path, entry, kind)


def parse_pud_bytes(data: bytes, source_path: Path, source_entry: str | None) -> ParsedFlight:
    nul_index = data.find(b"\x00")
    if nul_index <= 0:
        raise FlightParseError("PUD JSON header separator was not found")

    try:
        metadata = json.loads(data[:nul_index].decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FlightParseError(f"Malformed PUD JSON header: {exc}") from exc

    descriptors = metadata.get("details_headers")
    if not isinstance(descriptors, list) or not descriptors:
        raise FlightParseError("PUD header has no details_headers descriptors")

    row_size = 0
    for descriptor in descriptors:
        try:
            row_size += int(descriptor["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FlightParseError(f"Invalid PUD column descriptor: {descriptor}") from exc
    if row_size <= 0:
        raise FlightParseError("PUD binary row size is zero")

    warnings: list[str] = []
    raw_rows: list[dict[str, Any]] = []
    offset = nul_index + 1
    binary_len = len(data) - offset
    trailing = binary_len % row_size
    if trailing:
        warnings.append(f"Ignoring {trailing} trailing byte(s) after complete PUD rows")

    last_time: int | None = None
    while offset + row_size <= len(data):
        row: dict[str, Any] = {}
        cursor = offset
        for descriptor in descriptors:
            name = str(descriptor.get("name", ""))
            size = int(descriptor.get("size", 0))
            chunk = data[cursor : cursor + size]
            cursor += size
            row[name] = _decode_field(chunk, str(descriptor.get("type", "")), name)

        time_ms = _coerce_int(row.get("time"))
        if time_ms is not None:
            if last_time is not None and (time_ms < last_time or time_ms > last_time + MAX_TIME_INTERVAL_MS):
                warnings.append(f"Stopped PUD decode at time gap/backtrack: previous={last_time} current={time_ms}")
                break
            last_time = time_ms

        if "speed" not in row:
            speed = _vector_speed(row)
            if speed is not None:
                row["speed"] = speed
        raw_rows.append(row)
        offset += row_size

    headers = [str(descriptor.get("name", "")) for descriptor in descriptors]
    if raw_rows and "speed" in raw_rows[0] and "speed" not in headers:
        headers.append("speed")
    return ParsedFlight(source_path, source_entry, "pud", metadata, headers, raw_rows, warnings)


def parse_run_json_text(text: str, source_path: Path, source_entry: str | None, source_kind: str) -> ParsedFlight:
    try:
        metadata = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FlightParseError(f"Malformed run JSON: {exc}") from exc

    headers_raw = metadata.get("details_headers")
    data_raw = metadata.get("details_data")
    if not isinstance(headers_raw, list) or not isinstance(data_raw, list):
        raise FlightParseError("Run JSON must contain details_headers and details_data arrays")

    headers = [str(header) for header in headers_raw]
    raw_rows: list[dict[str, Any]] = []
    for item in data_raw:
        if isinstance(item, dict):
            raw_rows.append(dict(item))
        elif isinstance(item, list):
            raw_rows.append({headers[index]: value for index, value in enumerate(item[: len(headers)])})
        else:
            raise FlightParseError(f"Unsupported details_data row type: {type(item).__name__}")

    warnings: list[str] = []
    if raw_rows and "speed" not in headers:
        for row in raw_rows:
            speed = _vector_speed(row)
            if speed is not None:
                row["speed"] = speed
        if "speed" in raw_rows[0]:
            headers.append("speed")
    return ParsedFlight(source_path, source_entry, source_kind, metadata, headers, raw_rows, warnings)


def _decode_field(chunk: bytes, field_type: str, field_name: str) -> Any:
    normalized = field_type.strip().lower()
    try:
        if normalized == "string":
            return chunk.decode("utf-8", errors="replace").rstrip("\x00")
        if normalized == "integer":
            if len(chunk) not in {1, 2, 4}:
                raise FlightParseError(f"Integer field {field_name} has unsupported size {len(chunk)}")
            return int.from_bytes(chunk, byteorder="little", signed=True)
        if normalized == "boolean":
            return len(chunk) > 0 and chunk[0] != 0
        if normalized == "float":
            if len(chunk) != 4:
                raise FlightParseError(f"Float field {field_name} has unsupported size {len(chunk)}")
            return struct.unpack("<f", chunk)[0]
        if normalized == "double":
            if len(chunk) != 8:
                raise FlightParseError(f"Double field {field_name} has unsupported size {len(chunk)}")
            return struct.unpack("<d", chunk)[0]
    except struct.error as exc:
        raise FlightParseError(f"Could not decode field {field_name}") from exc
    raise FlightParseError(f"Unsupported PUD field type for {field_name}: {field_type}")


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


def _vector_speed(row: dict[str, Any]) -> float | None:
    parts = [_coerce_float(row.get(name)) for name in ("speed_vx", "speed_vy", "speed_vz")]
    if all(part is None for part in parts):
        return None
    return math.sqrt(sum((part or 0.0) ** 2 for part in parts))
