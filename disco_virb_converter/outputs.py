from __future__ import annotations

import csv
import json
import math
from datetime import timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from garmin_fit_sdk import Decoder, Encoder, Profile, Stream

from disco_virb_converter.models import ConversionResult, TelemetryRow
from disco_virb_converter.normalize import default_output_dir, flight_name, normalize_flight
from disco_virb_converter.pud import load_flight

GPX_NS = "http://www.topografix.com/GPX/1/1"
GPXTPX_NS = "http://www.garmin.com/xmlschemas/TrackPointExtension/v2"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"


def convert_file(input_path: str | Path, out_dir: str | Path | None = None, offset_seconds: float = 0.0) -> ConversionResult:
    source = Path(input_path)
    parsed = load_flight(source)
    name = flight_name(parsed)
    rows, warnings, skipped = normalize_flight(parsed, offset_seconds=offset_seconds)
    if not rows:
        raise ValueError("No valid telemetry rows available for export")

    output_dir = Path(out_dir) if out_dir else default_output_dir(source, name)
    output_dir.mkdir(parents=True, exist_ok=True)

    fit_path = output_dir / f"{name}.fit"
    gpx_path = output_dir / f"{name}.gpx"
    csv_path = output_dir / f"{name}.csv"
    manifest_path = output_dir / "manifest.json"
    readme_path = output_dir / "README.txt"

    write_fit(rows, fit_path, parsed.metadata, name, warnings)
    write_gpx(rows, gpx_path, parsed.metadata, name)
    write_csv(rows, csv_path)
    write_manifest(
        manifest_path,
        readme_path,
        input_path=source,
        parsed_kind=parsed.source_kind,
        source_entry=parsed.source_entry,
        flight_name=name,
        metadata=parsed.metadata,
        rows=rows,
        warnings=warnings,
        skipped_rows=skipped,
        output_files=[fit_path, gpx_path, csv_path],
        offset_seconds=offset_seconds,
    )

    return ConversionResult(
        input_path=source,
        output_dir=output_dir,
        flight_name=name,
        fit_path=fit_path,
        gpx_path=gpx_path,
        csv_path=csv_path,
        manifest_path=manifest_path,
        readme_path=readme_path,
        rows=rows,
        warnings=warnings,
        skipped_rows=skipped,
        source_kind=parsed.source_kind,
        source_entry=parsed.source_entry,
    )


def write_csv(rows: list[TelemetryRow], path: Path) -> None:
    extra_keys = sorted({key for row in rows for key in row.extras})
    fields = [
        "time_ms",
        "timestamp_utc",
        "latitude",
        "longitude",
        "altitude_m",
        "altitude_ft",
        "speed_m_s",
        "speed_mph",
        "distance_m",
        "distance_mi",
        "position_source",
    ] + extra_keys
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            record: dict[str, Any] = {
                "time_ms": row.time_ms,
                "timestamp_utc": iso_z(row.timestamp_utc),
                "latitude": f"{row.latitude:.8f}",
                "longitude": f"{row.longitude:.8f}",
                "altitude_m": "" if row.altitude_m is None else f"{row.altitude_m:.3f}",
                "altitude_ft": "" if row.altitude_m is None else f"{row.altitude_m * 3.280839895:.3f}",
                "speed_m_s": "" if row.speed_m_s is None else f"{row.speed_m_s:.3f}",
                "speed_mph": "" if row.speed_m_s is None else f"{row.speed_m_s * 2.236936292:.3f}",
                "distance_m": f"{row.distance_m:.3f}",
                "distance_mi": f"{row.distance_m / 1609.344:.6f}",
                "position_source": row.position_source,
            }
            for key in extra_keys:
                value = row.extras.get(key)
                record[key] = "" if value is None else value
            writer.writerow(record)


def write_gpx(rows: list[TelemetryRow], path: Path, metadata: dict[str, Any], name: str) -> None:
    ET.register_namespace("", GPX_NS)
    ET.register_namespace("gpxtpx", GPXTPX_NS)
    ET.register_namespace("xsi", XSI_NS)
    root = ET.Element(
        f"{{{GPX_NS}}}gpx",
        {
            "version": "1.1",
            "creator": "Disco PUD to Garmin/VIRB Converter",
            f"{{{XSI_NS}}}schemaLocation": (
                "http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd "
                "http://www.garmin.com/xmlschemas/TrackPointExtension/v2 "
                "http://www8.garmin.com/xmlschemas/TrackPointExtensionv2.xsd"
            ),
        },
    )
    meta = ET.SubElement(root, f"{{{GPX_NS}}}metadata")
    ET.SubElement(meta, f"{{{GPX_NS}}}name").text = name
    if metadata.get("product_name"):
        ET.SubElement(meta, f"{{{GPX_NS}}}desc").text = f"Parrot {metadata.get('product_name')} flight telemetry"

    trk = ET.SubElement(root, f"{{{GPX_NS}}}trk")
    ET.SubElement(trk, f"{{{GPX_NS}}}name").text = name
    trkseg = ET.SubElement(trk, f"{{{GPX_NS}}}trkseg")
    for row in rows:
        trkpt = ET.SubElement(
            trkseg,
            f"{{{GPX_NS}}}trkpt",
            {"lat": f"{row.latitude:.8f}", "lon": f"{row.longitude:.8f}"},
        )
        if row.altitude_m is not None:
            ET.SubElement(trkpt, f"{{{GPX_NS}}}ele").text = f"{row.altitude_m:.3f}"
        ET.SubElement(trkpt, f"{{{GPX_NS}}}time").text = iso_z(row.timestamp_utc)
        if row.speed_m_s is not None:
            extensions = ET.SubElement(trkpt, f"{{{GPX_NS}}}extensions")
            tpe = ET.SubElement(extensions, f"{{{GPXTPX_NS}}}TrackPointExtension")
            ET.SubElement(tpe, f"{{{GPXTPX_NS}}}speed").text = f"{row.speed_m_s:.3f}"

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_fit(rows: list[TelemetryRow], path: Path, metadata: dict[str, Any], name: str, warnings: list[str]) -> None:
    nums = Profile["mesg_num"]
    encoder = Encoder()
    start = rows[0].timestamp_utc
    end = rows[-1].timestamp_utc
    duration_s = max(0.0, (end - start).total_seconds())
    total_distance = rows[-1].distance_m
    speeds = [row.speed_m_s for row in rows if row.speed_m_s is not None and row.speed_m_s >= 0]
    avg_speed = total_distance / duration_s if duration_s > 0 else (sum(speeds) / len(speeds) if speeds else 0.0)
    max_speed = max(speeds) if speeds else 0.0
    ascent, descent = total_ascent_descent(rows)

    encoder.on_mesg(
        nums["FILE_ID"],
        {
            "type": "activity",
            "manufacturer": "development",
            "product": 4440,
            "serial_number": stable_serial(metadata.get("serial_number") or metadata.get("uuid") or name),
            "time_created": start,
        },
    )
    encoder.on_mesg(nums["EVENT"], {"timestamp": start, "event": "timer", "event_type": "start"})
    for row in rows:
        record = {
            "timestamp": row.timestamp_utc,
            "position_lat": degrees_to_semicircles(row.latitude),
            "position_long": degrees_to_semicircles(row.longitude),
            "distance": row.distance_m,
            "speed": fit_speed(row.speed_m_s),
            "enhanced_speed": nonnegative(row.speed_m_s),
            "altitude": fit_altitude(row.altitude_m),
            "enhanced_altitude": fit_altitude(row.altitude_m),
        }
        encoder.on_mesg(nums["RECORD"], record)
    encoder.on_mesg(nums["EVENT"], {"timestamp": end, "event": "timer", "event_type": "stop_all"})

    common_summary = {
        "timestamp": end,
        "event": "lap",
        "event_type": "stop",
        "start_time": start,
        "total_elapsed_time": duration_s,
        "total_timer_time": duration_s,
        "total_distance": total_distance,
        "avg_speed": fit_speed(avg_speed),
        "max_speed": fit_speed(max_speed),
        "total_ascent": ascent,
        "total_descent": descent,
        "sport": "flying",
        "sub_sport": "generic",
    }
    encoder.on_mesg(nums["LAP"], {"message_index": 0, **common_summary})
    encoder.on_mesg(
        nums["SESSION"],
        {
            "message_index": 0,
            **common_summary,
            "event": "session",
            "first_lap_index": 0,
            "num_laps": 1,
        },
    )
    encoder.on_mesg(
        nums["ACTIVITY"],
        {
            "timestamp": end,
            "total_timer_time": duration_s,
            "num_sessions": 1,
            "type": "manual",
            "event": "activity",
            "event_type": "stop",
        },
    )

    path.write_bytes(encoder.close())
    validate_fit(path, rows, warnings)


def validate_fit(path: Path, rows: list[TelemetryRow], warnings: list[str]) -> None:
    try:
        messages, errors = Decoder(Stream.from_file(str(path))).read()
    except Exception as exc:  # noqa: BLE001 - validation should report, not hide generated outputs
        warnings.append(f"FIT decode validation failed: {exc}")
        return
    if errors:
        warnings.append(f"FIT decode validation reported {len(errors)} error(s): {errors[:3]}")
    records = messages.get("record_mesgs", [])
    if len(records) != len(rows):
        warnings.append(f"FIT record count {len(records)} differs from telemetry row count {len(rows)}")
    if records:
        first_expected = degrees_to_semicircles(rows[0].latitude)
        last_expected = degrees_to_semicircles(rows[-1].latitude)
        if records[0].get("position_lat") != first_expected or records[-1].get("position_lat") != last_expected:
            warnings.append("FIT position validation did not match expected semicircle coordinates")


def write_manifest(
    manifest_path: Path,
    readme_path: Path,
    *,
    input_path: Path,
    parsed_kind: str,
    source_entry: str | None,
    flight_name: str,
    metadata: dict[str, Any],
    rows: list[TelemetryRow],
    warnings: list[str],
    skipped_rows: int,
    output_files: list[Path],
    offset_seconds: float,
) -> None:
    manifest = {
        "tool": "disco-pud-to-garmin-virb-converter",
        "flight_name": flight_name,
        "input_path": str(input_path),
        "source_kind": parsed_kind,
        "source_entry": source_entry,
        "offset_seconds": offset_seconds,
        "product_name": metadata.get("product_name"),
        "uuid": metadata.get("uuid"),
        "date": metadata.get("date"),
        "sample_count": len(rows),
        "skipped_rows": skipped_rows,
        "start_time_utc": iso_z(rows[0].timestamp_utc),
        "end_time_utc": iso_z(rows[-1].timestamp_utc),
        "total_distance_m": rows[-1].distance_m,
        "total_distance_mi": rows[-1].distance_m / 1609.344,
        "outputs": [str(path) for path in output_files],
        "warnings": warnings,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    readme_lines = [
        "Disco Garmin/VIRB export",
        "",
        f"Input: {input_path}",
        f"Source: {parsed_kind}" + (f" ({source_entry})" if source_entry else ""),
        f"Samples exported: {len(rows)}",
        f"Rows skipped: {skipped_rows}",
        f"Distance: {rows[-1].distance_m:.1f} m / {rows[-1].distance_m / 1609.344:.3f} mi",
        "",
        "Files:",
    ]
    readme_lines.extend(f"- {path.name}" for path in output_files)
    if warnings:
        readme_lines.extend(["", "Warnings:"])
        readme_lines.extend(f"- {warning}" for warning in warnings)
    readme_path.write_text("\n".join(readme_lines) + "\n", encoding="utf-8")


def degrees_to_semicircles(degrees: float) -> int:
    return int(round(degrees * (2**31) / 180.0))


def fit_speed(value: float | None) -> float | None:
    value = nonnegative(value)
    if value is None or value > 65.535:
        return None
    return value


def fit_altitude(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    if value < -500.0:
        return None
    return value


def nonnegative(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value) or value < 0:
        return None
    return value


def total_ascent_descent(rows: list[TelemetryRow]) -> tuple[int, int]:
    ascent = 0.0
    descent = 0.0
    previous: float | None = None
    for row in rows:
        if row.altitude_m is None:
            continue
        if previous is not None:
            delta = row.altitude_m - previous
            if delta > 0:
                ascent += delta
            elif delta < 0:
                descent += abs(delta)
        previous = row.altitude_m
    return int(round(ascent)), int(round(descent))


def stable_serial(value: Any) -> int:
    text = str(value)
    result = 2166136261
    for char in text:
        result ^= ord(char)
        result = (result * 16777619) & 0xFFFFFFFF
    return result or 1


def iso_z(value: Any) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
