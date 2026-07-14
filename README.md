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
- **Batch convert** – point at a folder and convert every `.xcal` in it to both
  the flat `.bin` and EFILive `_efi.bin` in one pass.
- **Compare** – diff two calibration files (`.bin` or `.xcal`); nearby changed
  bytes are grouped into diff runs so you can see exactly what a tune changed,
  and save a diff report.
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
- **ECU diagnostics + read/write** – connect over J1939 (CAN) or J1587 (J1708)
  and **Identify** (VIN, ESN, ECFG/calibration version, part no.), **read** /
  **clear** DTCs, and **read / write the flash image** via J1939 memory access
  (DM14/DM15/DM16, with BAM multi-packet transfers). Adapters are auto-detected
  into a dropdown (python-can / RP1210 / J2534 / SocketCAN) plus a built-in
  **Simulation** ECU. Module profiles: CM870, CM871, CM2250, CM2350, CM2450.
  A flash read routes straight into the bin / EFILive `_efi.bin` / xcal
  converters; a write is **backup-first** and **verified by read-back**.
  Unlocking a real ECU needs an authorized seed/key `SecurityProvider` you
  supply — **no Cummins security is bypassed or shipped**.
- **Live data** – stream broadcast J1939 telemetry (engine speed, coolant/oil/
  fuel/intake temps, oil & fuel pressure, boost, fuel rate, battery, vehicle
  speed, fuel & DEF level, engine hours, odometer) into a live readout and log
  it to CSV.
- **Service report** – save a one-page text report (ECU data tag, decoded VIN,
  active/previously-active codes, image CRC32/SHA-256 integrity).

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
- **ECU diagnostics + read/write** — J1939/J1587 diagnostics, identify, DTC
  read/clear, and DM14/15/16 flash read/write are implemented and unit-tested
  end-to-end against the built-in simulated ECU (read → modify → write →
  verify, plus a locked-without-key failure). Hardware transports
  (RP1210/J2534/SocketCAN/python-can) are structurally complete but not yet
  validated against a physical ECU, and the older module memory maps
  (CM870/871/2250/2350) need per-ECU verification before writing. Real ECUs
  require an operator-supplied seed/key module.

## Requirements

- Python 3.8+
- Tkinter (bundled with Python on Windows/macOS; on Debian/Ubuntu:
  `sudo apt-get install python3-tk`)

No third-party packages are required to run the app.

## Run

```bash
python run.py
```

## Build a Windows .exe (no Python needed on the target PC)

From the repo root on Windows:

```bat
packaging\build_exe.bat
```

This produces a single self-contained `dist\xcaltool.exe`. It uses 32-bit
Python (`py -3-32`) so the Nexiq RP1210 driver (`NULN2R32.dll`, which is
32-bit) can load at runtime.

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
  comms.py             # diagnostic link + J1939 flasher + simulated ECU
  j1939.py             # J1939 PGNs, DTCs, BAM, DM14/15/16 memory access
  j1587.py             # J1587/J1708 messaging + DTC decode
  transport.py         # Simulation / python-can / RP1210 / J2534 / SocketCAN
  modules.py           # Cummins ECM module profiles (memory maps)
  faultcodes.py        # Cummins service fault-code import/search
  dtc.py               # DTC catalog / classifier
  livedata.py          # J1939 broadcast telemetry poller
  calcompare.py        # calibration image diff
  batch.py             # batch folder conversion
  report.py            # VIN decode + integrity hashes + service report
  gui.py               # Tkinter UI (tabs)
packaging/             # PyInstaller spec + Windows build_exe.bat
tests/                 # unit tests
```

## Legal

This tool is for diagnostics, backups, research, and **legitimate**
calibration work. It does not implement, and will not be extended to implement,
emissions-defeat ("delete") functionality for on-road vehicles. Modifying
emissions controls on vehicles operated on public roads is illegal in many
jurisdictions (e.g. the U.S. Clean Air Act).
