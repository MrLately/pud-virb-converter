from __future__ import annotations

import gzip
import json
import struct
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from garmin_fit_sdk import Decoder, Stream

from disco_virb_converter.outputs import convert_file
from disco_virb_converter.pud import parse_pud_bytes


class ConverterTests(unittest.TestCase):
    def test_synthetic_pud_decodes_field_types(self) -> None:
        parsed = parse_pud_bytes(make_pud([(0, True, -81.0, 41.0, 3.0, 4.0, 12.0, 300, 97)]), Path("synthetic.pud"), None)
        self.assertEqual(parsed.source_kind, "pud")
        self.assertEqual(len(parsed.raw_rows), 1)
        row = parsed.raw_rows[0]
        self.assertEqual(row["time"], 0)
        self.assertTrue(row["product_gps_available"])
        self.assertAlmostEqual(row["product_gps_longitude"], -81.0)
        self.assertAlmostEqual(row["speed"], 13.0)

    def test_time_gap_stops_pud_decode(self) -> None:
        parsed = parse_pud_bytes(
            make_pud(
                [
                    (0, True, -81.0, 41.0, 0.0, 0.0, 0.0, 300, 97),
                    (100, True, -80.999, 41.0, 0.0, 0.0, 0.0, 301, 96),
                    (20_500, True, -80.998, 41.0, 0.0, 0.0, 0.0, 302, 95),
                ]
            ),
            Path("gap.pud"),
            None,
        )
        self.assertEqual(len(parsed.raw_rows), 2)
        self.assertTrue(any("Stopped PUD decode" in warning for warning in parsed.warnings))

    def test_conversion_outputs_gpx_csv_fit_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "flight.pud"
            input_path.write_bytes(
                make_pud(
                    [
                        (0, True, -81.0, 41.0, 10.0, 0.0, 0.0, 300, 97),
                        (1000, True, -80.999, 41.0, 10.0, 0.0, 0.0, 301, 96),
                        (2000, True, -80.998, 41.0, 10.0, 0.0, 0.0, 302, 95),
                    ]
                )
            )
            out = Path(tmp) / "out"
            result = convert_file(input_path, out)
            self.assertTrue(result.fit_path.exists())
            self.assertTrue(result.gpx_path.exists())
            self.assertTrue(result.csv_path.exists())
            self.assertTrue(result.manifest_path.exists())
            self.assertEqual(len(result.rows), 3)

            root = ET.parse(result.gpx_path).getroot()
            self.assertEqual(root.attrib["version"], "1.1")
            ns = {"g": "http://www.topografix.com/GPX/1/1"}
            times = root.findall(".//g:trkpt/g:time", ns)
            self.assertEqual(len(times), 3)
            self.assertTrue(times[0].text.endswith("Z"))

            messages, errors = Decoder(Stream.from_file(str(result.fit_path))).read()
            self.assertEqual(errors, [])
            self.assertEqual(len(messages.get("record_mesgs", [])), 3)

    def test_long_flight_exceeds_65km_and_100min(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "long.json.gz"
            payload = make_json_payload(point_count=151, seconds_step=60, lon_step=0.006)
            input_path.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
            result = convert_file(input_path, Path(tmp) / "out")
            self.assertGreater(result.rows[-1].distance_m, 65_000)
            self.assertGreater((result.rows[-1].timestamp_utc - result.rows[0].timestamp_utc).total_seconds(), 6_000)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertGreater(manifest["total_distance_m"], 65_000)

    def test_existing_stock_json_zips_if_present(self) -> None:
        samples = [
            Path(r"C:\Users\autog\Downloads\080A25A41416A8866B289A9B1FE0B9FD.json.zip"),
            Path(r"C:\Users\autog\Downloads\A323B42570F31A03BD59D6EC8FC37BC4.json.zip"),
            Path(r"C:\Users\autog\Downloads\E9FDCB343335AB65FF460AC02B715E5D.json.zip"),
        ]
        present = [sample for sample in samples if sample.exists()]
        if not present:
            self.skipTest("No downloaded stock JSON ZIP samples are available")
        with tempfile.TemporaryDirectory() as tmp:
            for sample in present:
                result = convert_file(sample, Path(tmp) / sample.stem)
                self.assertGreater(len(result.rows), 100)
                self.assertTrue(result.fit_path.exists())
                self.assertTrue(result.gpx_path.exists())
                self.assertTrue(result.csv_path.exists())


def make_pud(rows: list[tuple[int, bool, float, float, float, float, float, int, int]]) -> bytes:
    header = {
        "version": "1",
        "software_version": "5.2.7",
        "hardware_version": "Disco",
        "uuid": "SYNTHETIC",
        "product_name": "Disco",
        "product_id": 42,
        "date": "2026-05-27T134941-0400",
        "details_headers": [
            {"name": "time", "type": "integer", "size": 4},
            {"name": "product_gps_available", "type": "boolean", "size": 1},
            {"name": "product_gps_longitude", "type": "double", "size": 8},
            {"name": "product_gps_latitude", "type": "double", "size": 8},
            {"name": "speed_vx", "type": "float", "size": 4},
            {"name": "speed_vy", "type": "float", "size": 4},
            {"name": "speed_vz", "type": "float", "size": 4},
            {"name": "altitude", "type": "integer", "size": 4},
            {"name": "battery_level", "type": "integer", "size": 1},
        ],
    }
    binary = bytearray()
    for time_ms, gps_ok, lon, lat, vx, vy, vz, altitude, battery in rows:
        binary.extend(struct.pack("<i", time_ms))
        binary.extend(b"\x01" if gps_ok else b"\x00")
        binary.extend(struct.pack("<d", lon))
        binary.extend(struct.pack("<d", lat))
        binary.extend(struct.pack("<f", vx))
        binary.extend(struct.pack("<f", vy))
        binary.extend(struct.pack("<f", vz))
        binary.extend(struct.pack("<i", altitude))
        binary.extend(int(battery).to_bytes(1, "little", signed=True))
    return json.dumps(header).encode("utf-8") + b"\x00" + bytes(binary)


def make_json_payload(point_count: int, seconds_step: int, lon_step: float) -> dict:
    headers = [
        "time",
        "battery_level",
        "product_gps_available",
        "product_gps_longitude",
        "product_gps_latitude",
        "speed_vx",
        "speed_vy",
        "speed_vz",
        "altitude",
        "speed",
    ]
    data = []
    for index in range(point_count):
        data.append(
            [
                index * seconds_step * 1000,
                max(0, 100 - index // 2),
                True,
                -81.0 + index * lon_step,
                41.0,
                20.0,
                0.0,
                0.0,
                300 + index,
                20.0,
            ]
        )
    return {
        "version": "1",
        "uuid": "LONGFLIGHT",
        "product_name": "Disco",
        "date": "2026-05-27T134941-0400",
        "details_headers": headers,
        "details_data": data,
    }


if __name__ == "__main__":
    unittest.main()
