# xcaltool

A small, beginner-friendly desktop app for working with Cummins
Calterm/INSITE calibration files.

## What it does

- **xcal ↔ bin** – convert `.xcal` calibration containers to raw `.bin` flash
  images and back (lossless round-trip via a `.xcalmeta` sidecar, or by picking
  the original `.xcal` as a template).
- **xcal ↔ EFILive `_efi.bin`** – produce EFILive's compacted `_efi.bin` layout
  (the smaller file you flash/edit) and rebuild the `.xcal` from it. The high
  calibration bank (0x840000+) is shifted down by 0x7C0000 so the file stays a
  sensible size instead of a 32 MB flat image. Verified byte-exact on CM24xx.
- **ecfg → xdf/csv** – turn a Cummins `.ecfg` definition into a TunerPro `.xdf`
  or a `.csv` table.
- **DTC catalog** – from an `.ecfg`, list the fault-code / diagnostic parameters,
  classified by subsystem and flagged emissions-vs-config, for diagnostics and
  legitimate hardware swaps (auto→manual, fuel-tank removal, engine swaps). CSV
  is a read-only reference of everything; the editable XDF pack excludes
  emissions monitors by default. It does **not** disable/mask emissions DTCs,
  monitors, or derates.
- **Fault codes** – import the published Cummins service fault-code sheet
  (CES 14602 `.xls`) into a searchable table (Cummins FC ↔ SPN / FMI / P-code /
  lamp / description) and export it as CSV. `.xls` import needs the optional
  `xlrd` package; searching/exporting a saved CSV is standard-library only.
- **ECU (read/write)** – placeholder tab; the interface is stubbed so live ECU
  read/write can be added later without changing the rest of the app.

## Status

- **`xcal ↔ bin`** — fully implemented and validated **byte-exact** against real
  EFILive/Cummins `.xcal` files (CM22xx / CM23xx / CM2450A). The `.xcal` is a
  text `compatibility_header` followed by Intel-HEX records; we decode those to
  the raw flash image and rebuild the exact `.xcal` from a small `.xcalmeta`
  sidecar. See `xcalfmt.py` for the format writeup and the known limitation
  around EFILive's undocumented file-integrity token.
- **`ecfg → xdf/csv`** — fully implemented. Parses the Cummins
  `Engineering_Tool_Config_File` XML (tens of thousands of parameters) into a
  neutral model and exports CSV (every parameter definition) and a TunerPro XDF
  skeleton. XDF element addresses are parameter **ids** (the `.ecfg` addresses by
  id, not raw offset); resolving ids to `.bin` offsets via the module index
  table is a planned follow-up.
- **ECU (read/write)** — interface only (`comms.py`); no hardware backend yet.

## Requirements

- Python 3.8+
- Tkinter (bundled with Python on Windows/macOS; on Debian/Ubuntu:
  `sudo apt-get install python3-tk`)

No third-party packages are required to run the app.

## Run

```bash
python run.py
```

## Test

```bash
python -m pytest -q
```

## Project layout

```
run.py                 # launches the GUI
src/xcaltool/
  xcalfmt.py           # EFILive/Cummins xcal <-> bin conversion (real format)
  codec.py             # generic container helpers + checksums glue
  checksum.py          # checksum algorithms
  ecfg.py              # ecfg XML parser + XDF/CSV exporters
  comms.py             # abstract ECU read/write interface (future)
  gui.py               # Tkinter UI (3 tabs)
tests/                 # unit tests
```

## Legal

This tool is for diagnostics, backups, research, and **legitimate**
calibration work. It does not implement, and will not be extended to implement,
emissions-defeat ("delete") functionality for on-road vehicles. Modifying
emissions controls on vehicles operated on public roads is illegal in many
jurisdictions (e.g. the U.S. Clean Air Act).
