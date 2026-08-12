# Disco PUD to Garmin/VIRB Converter

Windows utility for converting Parrot Disco flight exports into files that Garmin VIRB Edit can import for telemetry overlays.

Current version: `0.2.0`

The preferred input is the patched FreeFlight Pro Send Piloting ZIP that contains `raw/*.pud`. The tool also accepts standalone `.pud`, old stock `.json.zip`, `.json`, `.txt`, and `.gz` run-detail files.

## What It Does

The converter reads Parrot Disco telemetry and writes a Garmin/VIRB-friendly export set. It uses the aircraft/product GPS track from the PUD file, skips invalid `500.0` GPS sentinel rows, scales Disco altitude from millimeters to meters, removes duplicate timestamps, and computes cumulative distance with normal double-precision math so long flights continue past the old `65 km` / `100 minute` limit seen in older Flight Data Manager workflows.

## Garmin VIRB / FDM Compatibility

The primary FIT output includes standard Garmin activity fields plus Flight Data Manager-style carrier fields used by Parrot Disco VIRB templates.

Included compatibility data:

- Battery level
- Wi-Fi signal
- GPS satellite count
- Pitot / airspeed data
- Recorded 3D speed
- Pitch, roll, and track
- Altitude
- Distance from home
- Cumulative route distance

Important distance note:

- The standard FIT `distance` field is the authoritative long-distance value.
- Legacy FDM-style carrier fields such as `power` and OBD PID `12` have FIT/range limits and are saturated to prevent wrap/reset behavior.
- If a VIRB template reads one of the old compatibility carriers for distance, it may stop increasing at that carrier limit. The corrected full route distance remains available in the standard FIT distance field.

## Outputs

Each conversion writes:

- `<flight>.fit` - primary Garmin/VIRB import file with FDM-compatible Parrot Disco fields.
- `<flight>.gpx` - GPX 1.1 fallback with Garmin trackpoint speed extension.
- `<flight>.csv` - inspectable telemetry table.
- `manifest.json` and `README.txt` - source, counts, warnings, and output notes.

## GUI

Run the executable without arguments to open the Windows GUI.

The GUI provides:

- Input file picker
- Output folder picker
- Video sync offset field
- Convert button
- Status log
- Open Output Folder button

## CLI

```powershell
python -m disco_virb_converter "C:\path\to\flight.zip" --out "C:\path\to\out" --offset-seconds 0
```

When packaged:

```powershell
disco-virb-converter.exe "C:\path\to\flight.zip" --out "C:\path\to\out" --offset-seconds 0
```

If `--out` is omitted, the converter writes beside the input as `<flight>_virb_export`.

## Build

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests
.\build_exe.ps1
```

The executable is written to `dist\disco-virb-converter.exe`.

## Validation Status

Automated tests cover PUD parsing, invalid GPS skipping, duplicate timestamp handling, altitude scaling, long flights past `65 km`, FIT decode validation, and FDM-compatible FIT carrier fields.

The generated FIT has been decoded successfully with Garmin's FIT SDK and checked for:

- Standard record messages
- Standard distance beyond `75 km`
- Aviation attitude messages
- OBD-II compatibility messages
- Timestamp correlation
- FDM-style battery, Wi-Fi, speed, altitude, attitude, and distance carrier fields

Final acceptance should still be checked manually in Garmin VIRB Edit because individual VIRB templates may choose different FIT fields for overlay widgets.
