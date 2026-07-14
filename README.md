# xcaltool

A small, beginner-friendly desktop app for working with Cummins
Calterm/INSITE calibration files.

## What it does

- **xcal ↔ bin** – convert `.xcal` calibration containers to raw `.bin` flash
  images and back (lossless round-trip via a `.xcalmeta` sidecar).
- **ecfg → xdf/csv** – turn a Cummins `.ecfg` definition into a TunerPro `.xdf`
  or a `.csv` table.
- **ECU (read/write)** – placeholder tab; the interface is stubbed so live ECU
  read/write can be added later without changing the rest of the app.

## Status / honesty note

The `.xcal` and `.ecfg` formats are **not publicly documented**. The app is
built around configurable/neutral models so it's useful today, but the exact
byte layouts still need to be confirmed against **real sample files**:

- `xcal ↔ bin` works as a generic container tool now: it auto-detects a likely
  header and lets you adjust header/trailer offsets and pick a checksum. Once a
  real `.xcal` sample is available, the correct defaults get locked in.
- `ecfg → xdf/csv`: the XDF and CSV **writers are complete and tested**; the
  `.ecfg` **parser** is a stub that waits for a sample file.

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
  codec.py             # xcal <-> bin conversion
  checksum.py          # checksum algorithms
  ecfg.py              # ecfg parsing (stub) + XDF/CSV writers
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
