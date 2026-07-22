# Disco PUD to Garmin/VIRB Converter

Windows utility for converting Parrot Disco flight exports into files that Garmin VIRB Edit can import for telemetry overlays.

Preferred input is the patched FreeFlight Pro Send Piloting ZIP that contains `raw/*.pud`. The tool also accepts standalone `.pud`, old stock `.json.zip`, `.json`, `.txt`, and `.gz` run-detail files.

## Outputs

Each conversion writes:

- `<flight>.fit` - primary Garmin/VIRB import file.
- `<flight>.gpx` - GPX 1.1 fallback with Garmin trackpoint speed extension.
- `<flight>.csv` - inspectable telemetry table.
- `manifest.json` and `README.txt` - source, counts, warnings, and output notes.

## CLI

```powershell
python -m disco_virb_converter "C:\path\to\flight.zip" --out "C:\path\to\out" --offset-seconds 0
```

When packaged:

```powershell
disco-virb-converter.exe "C:\path\to\flight.zip" --out "C:\path\to\out" --offset-seconds 0
```

Run without arguments to open the GUI.

## Build

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests
.\build_exe.ps1
```

The executable is written to `dist\disco-virb-converter.exe`.
