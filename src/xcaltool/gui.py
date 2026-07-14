"""Tkinter GUI for xcaltool.

Tabs:
  1. xcal <-> bin      -- convert calibration containers to raw images and back
  2. Batch convert     -- convert a whole folder of .xcal to bin + EFILive bin
  3. Compare           -- diff two calibration images
  4. ecfg -> xdf/csv   -- turn a Cummins ECFG definition into TunerPro XDF / CSV
  5. DTC catalog       -- classify ecfg diagnostic parameters
  6. Fault codes       -- searchable Cummins service fault-code table
  7. ECU diagnostics   -- connect / identify / DTCs / flash / live data / report

The GUI only handles user interaction; all real work lives in the library
modules so it stays easy to follow.
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib.util
import json
import os
import struct
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import (__version__, batch, calcompare, comms, dtc, ecfg, faultcodes,
               livedata, modules, report, transport, xcalfmt)


def _hex_preview(data: bytes, max_rows: int = 24) -> str:
    """Return a classic hex+ASCII dump of the first bytes of ``data``."""
    out = []
    for row in range(min(max_rows, (len(data) + 15) // 16)):
        chunk = data[row * 16: row * 16 + 16]
        hexs = " ".join(f"{b:02X}" for b in chunk).ljust(47)
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        out.append(f"{row * 16:08X}  {hexs}  {text}")
    if len(data) > max_rows * 16:
        out.append("...")
    return "\n".join(out)


class XcalBinTab(ttk.Frame):
    """Tab for xcal <-> bin conversion (EFILive/Cummins format)."""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self._data = b""
        self._path = ""

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Open .xcal or .bin...", command=self.open_file).pack(side="left")
        self.file_lbl = ttk.Label(top, text="No file loaded")
        self.file_lbl.pack(side="left", padx=10)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="xcal -> bin", command=self.xcal_to_bin).pack(side="left")
        ttk.Button(actions, text="bin -> xcal", command=self.bin_to_xcal).pack(side="left", padx=6)
        ttk.Button(actions, text="bin -> xcal (use .xcal template)",
                   command=self.bin_to_xcal_template).pack(side="left")

        efi = ttk.Frame(self)
        efi.pack(fill="x")
        ttk.Button(efi, text="xcal -> EFILive _efi.bin",
                   command=self.xcal_to_efi).pack(side="left")
        ttk.Button(efi, text="_efi.bin -> xcal (use .xcal template)",
                   command=self.efi_to_xcal).pack(side="left", padx=6)

        ttk.Label(
            self,
            text="'xcal -> bin' extracts the flat flash image. 'xcal -> EFILive "
                 "_efi.bin' matches EFILive's compacted layout (the smaller file "
                 "you flash/edit). bin -> xcal needs the .xcalmeta sidecar, or use "
                 "a 'template' button and pick the matching original .xcal.",
            foreground="#555", wraplength=720, justify="left",
        ).pack(anchor="w")

        # Optional matching .ecfg for this calibration.
        self._ecfg_defn = None
        ecfg_row = ttk.LabelFrame(self, text="Matching .ecfg (optional)", padding=6)
        ecfg_row.pack(fill="x", pady=(8, 0))
        ttk.Button(ecfg_row, text="Open .ecfg...",
                   command=self.open_ecfg).pack(side="left")
        ttk.Button(ecfg_row, text="Export XDF...",
                   command=lambda: self.export_ecfg("xdf")).pack(side="left", padx=6)
        ttk.Button(ecfg_row, text="Export CSV...",
                   command=lambda: self.export_ecfg("csv")).pack(side="left")
        self.ecfg_lbl = ttk.Label(ecfg_row, text="no .ecfg loaded")
        self.ecfg_lbl.pack(side="left", padx=10)

        self.report = tk.Text(self, height=8, wrap="none")
        self.report.pack(fill="x", pady=8)
        self.hex = tk.Text(self, height=14, wrap="none", font=("Courier", 9))
        self.hex.pack(fill="both", expand=True)

    # -- helpers -----------------------------------------------------------
    def _set_report(self, text: str):
        self.report.delete("1.0", "end")
        self.report.insert("1.0", text)

    def _set_hex(self, data: bytes):
        self.hex.delete("1.0", "end")
        self.hex.insert("1.0", _hex_preview(data))

    # -- actions -----------------------------------------------------------
    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open .xcal or .bin",
            filetypes=[("Calibration files", "*.xcal *.bin"), ("All files", "*.*")],
        )
        if not path:
            return
        with open(path, "rb") as fh:
            self._data = fh.read()
        self._path = path
        self.file_lbl.config(text=os.path.basename(path))
        self._describe()
        self._set_hex(self._data)

    def _describe(self):
        if not self._data:
            return
        if xcalfmt.is_xcal(self._data):
            try:
                x = xcalfmt.parse(self._data)
            except xcalfmt.XcalError as exc:
                self._set_report(f"Looks like an .xcal but failed to parse:\n{exc}")
                return
            f = x.fields
            lines = [
                "Detected: EFILive/Cummins .xcal",
                f"  module      : {f.get('module_name', '?')}",
                f"  calibration : {f.get('calibration_version', '?')}",
                f"  product_id  : {f.get('product_id', '?')}",
                f"  byte_order  : {f.get('byte_order', '?')}",
                f"  token       : {x.token}",
                f"  image size  : {len(x.image):,} bytes (0x{len(x.image):X})",
                f"  hex runs    : {len(x.runs)}",
                self._hash_line(x.image),
                "",
                "Click 'xcal -> bin' to extract the raw flash image.",
            ]
            self._set_report("\n".join(lines))
        else:
            sidecar = self._path + ".xcalmeta"
            has = os.path.exists(sidecar)
            self._set_report(
                f"Detected: raw .bin ({len(self._data):,} bytes)\n"
                f"{self._hash_line(self._data)}\n"
                f"Sidecar {'found' if has else 'NOT found'}: "
                f"{os.path.basename(sidecar)}\n\n"
                + ("Click 'bin -> xcal' to rebuild the .xcal."
                   if has else
                   "Extract this bin from its .xcal first so a .xcalmeta sidecar "
                   "exists, then bin -> xcal can rebuild it.")
            )

    @staticmethod
    def _hash_line(image: bytes) -> str:
        h = report.image_hashes(image)
        return f"  CRC32={h['crc32']}  SHA256={h['sha256']}"

    def xcal_to_bin(self):
        if not self._data:
            messagebox.showinfo("xcaltool", "Open a file first.")
            return
        try:
            image, meta = xcalfmt.xcal_to_bin(self._data)
        except xcalfmt.XcalError as exc:
            messagebox.showerror("Conversion failed", str(exc))
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".bin", filetypes=[("Binary image", "*.bin")]
        )
        if not out:
            return
        with open(out, "wb") as fh:
            fh.write(image)
        with open(out + ".xcalmeta", "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        messagebox.showinfo(
            "Done",
            f"Wrote {len(image):,} bytes to\n{out}\n\n"
            "Saved a .xcalmeta sidecar so 'bin -> xcal' can rebuild the exact "
            "original .xcal.",
        )
        self._set_hex(image)

    def bin_to_xcal(self):
        if not self._data:
            messagebox.showinfo("xcaltool", "Open a file first.")
            return
        sidecar_path = self._path + ".xcalmeta"
        if not os.path.exists(sidecar_path):
            messagebox.showerror(
                "Missing sidecar",
                "No .xcalmeta found next to this .bin. Extract the bin from its "
                ".xcal first (that saves the sidecar), then rebuild.",
            )
            return
        try:
            with open(sidecar_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            blob = xcalfmt.bin_to_xcal(self._data, meta)
        except (xcalfmt.XcalError, ValueError, KeyError) as exc:
            messagebox.showerror("Conversion failed", str(exc))
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".xcal", filetypes=[("xcal file", "*.xcal")]
        )
        if not out:
            return
        with open(out, "wb") as fh:
            fh.write(blob)
        messagebox.showinfo("Done", f"Wrote {len(blob):,} bytes to\n{out}")

    def xcal_to_efi(self):
        if not self._data:
            messagebox.showinfo("xcaltool", "Open the .xcal first.")
            return
        if not xcalfmt.is_xcal(self._data):
            messagebox.showinfo("xcaltool", "Open an .xcal (not a .bin) first.")
            return
        try:
            x = xcalfmt.parse(self._data)
            efi = xcalfmt.to_efi_bin(x)
        except xcalfmt.XcalError as exc:
            messagebox.showerror("Conversion failed", str(exc))
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".bin",
            initialfile="output_efi.bin",
            filetypes=[("bin file", "*.bin")],
        )
        if not out:
            return
        with open(out, "wb") as fh:
            fh.write(efi)
        messagebox.showinfo("Done", f"Wrote {len(efi):,} bytes (EFILive layout) to\n{out}")

    def efi_to_xcal(self):
        if not self._data:
            messagebox.showinfo("xcaltool", "Open the _efi.bin first (Open button).")
            return
        if xcalfmt.is_xcal(self._data):
            messagebox.showinfo(
                "xcaltool",
                "The open file is already an .xcal. Open the _efi.bin, then use "
                "this and pick the matching template .xcal.",
            )
            return
        tpl_path = filedialog.askopenfilename(
            title="Pick the matching original .xcal (template)",
            filetypes=[("xcal file", "*.xcal"), ("All files", "*.*")],
        )
        if not tpl_path:
            return
        try:
            with open(tpl_path, "rb") as fh:
                template = fh.read()
            blob = xcalfmt.efi_bin_to_xcal(self._data, template)
        except (xcalfmt.XcalError, ValueError) as exc:
            messagebox.showerror("Conversion failed", str(exc))
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".xcal", filetypes=[("xcal file", "*.xcal")]
        )
        if not out:
            return
        with open(out, "wb") as fh:
            fh.write(blob)
        messagebox.showinfo(
            "Done",
            f"Wrote {len(blob):,} bytes to\n{out}\n\n"
            "Note: the template's 4-char token was reused as-is; if you changed "
            "bytes EFILive may need to re-accept the file.",
        )

    def open_ecfg(self):
        path = filedialog.askopenfilename(
            title="Open matching .ecfg",
            filetypes=[("ECFG", "*.ecfg"), ("All files", "*.*")],
        )
        if not path:
            return
        with open(path, "rb") as fh:
            data = fh.read()
        try:
            self._ecfg_defn = ecfg.parse(data)
        except ecfg.EcfgError as exc:
            messagebox.showerror("Parse failed", str(exc))
            return
        d = self._ecfg_defn
        self.ecfg_lbl.config(
            text=f"{os.path.basename(path)}  ({d.ecm} {d.version}, "
                 f"{len(d.parameters):,} params)"
        )

    def export_ecfg(self, kind: str):
        if self._ecfg_defn is None:
            messagebox.showinfo("xcaltool", "Open an .ecfg first.")
            return
        text = (ecfg.to_xdf(self._ecfg_defn) if kind == "xdf"
                else ecfg.to_csv(self._ecfg_defn))
        ext = ".xdf" if kind == "xdf" else ".csv"
        out = filedialog.asksaveasfilename(defaultextension=ext)
        if not out:
            return
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        messagebox.showinfo("Done", f"Wrote {out}")

    def bin_to_xcal_template(self):
        if not self._data:
            messagebox.showinfo("xcaltool", "Open the .bin first (Open button).")
            return
        if xcalfmt.is_xcal(self._data):
            messagebox.showinfo(
                "xcaltool",
                "The open file is already an .xcal. Open the .bin you want to "
                "wrap, then use this button and pick the template .xcal.",
            )
            return
        tpl_path = filedialog.askopenfilename(
            title="Pick the matching original .xcal (template)",
            filetypes=[("xcal file", "*.xcal"), ("All files", "*.*")],
        )
        if not tpl_path:
            return
        try:
            with open(tpl_path, "rb") as fh:
                template = fh.read()
            blob = xcalfmt.build_from_template(self._data, template)
        except (xcalfmt.XcalError, ValueError) as exc:
            messagebox.showerror("Conversion failed", str(exc))
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".xcal", filetypes=[("xcal file", "*.xcal")]
        )
        if not out:
            return
        with open(out, "wb") as fh:
            fh.write(blob)
        messagebox.showinfo(
            "Done",
            f"Wrote {len(blob):,} bytes to\n{out}\n\n"
            "Note: the template's 4-char token was reused as-is (it can't be "
            "recomputed), so if you changed bytes EFILive may need to re-accept "
            "the file.",
        )


class EcfgTab(ttk.Frame):
    """Tab for ecfg -> xdf / csv export."""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self._data = b""

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Open .ecfg...", command=self.open_file).pack(side="left")
        self.file_lbl = ttk.Label(top, text="No file loaded")
        self.file_lbl.pack(side="left", padx=10)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Export XDF...", command=lambda: self.export("xdf")).pack(side="left")
        ttk.Button(actions, text="Export CSV...", command=lambda: self.export("csv")).pack(side="left", padx=6)

        self.report = tk.Text(self, height=12, wrap="word")
        self.report.pack(fill="both", expand=True, pady=8)

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open .ecfg", filetypes=[("ECFG", "*.ecfg"), ("All files", "*.*")]
        )
        if not path:
            return
        with open(path, "rb") as fh:
            self._data = fh.read()
        self.file_lbl.config(text=os.path.basename(path))
        self.report.delete("1.0", "end")
        self.report.insert("1.0", f"Loaded {len(self._data)} bytes. Detected encoding: {ecfg.sniff(self._data)}")

    def export(self, kind: str):
        if not self._data:
            messagebox.showinfo("xcaltool", "Open an .ecfg first.")
            return
        try:
            defn = ecfg.parse(self._data)
        except ecfg.EcfgError as exc:
            messagebox.showerror("Not supported yet", str(exc))
            return
        text = ecfg.to_xdf(defn) if kind == "xdf" else ecfg.to_csv(defn)
        ext = ".xdf" if kind == "xdf" else ".csv"
        out = filedialog.asksaveasfilename(defaultextension=ext)
        if not out:
            return
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        messagebox.showinfo("Done", f"Wrote {out}")


class DtcTab(ttk.Frame):
    """Build a DTC catalog from an .ecfg for diagnostics / hardware swaps."""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self._defn = None
        self._entries = []

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Open .ecfg...", command=self.open_file).pack(side="left")
        self.file_lbl = ttk.Label(top, text="No file loaded")
        self.file_lbl.pack(side="left", padx=10)

        self.incl_emis = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self,
            text="Include emissions-related DTCs in the editable XDF pack "
                 "(off = swap/config codes only)",
            variable=self.incl_emis,
        ).pack(anchor="w", pady=(6, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Export catalog CSV...",
                   command=self.export_csv).pack(side="left")
        ttk.Button(actions, text="Export DTC map pack (XDF)...",
                   command=self.export_xdf).pack(side="left", padx=6)

        self.report = tk.Text(self, height=16, wrap="word")
        self.report.pack(fill="both", expand=True, pady=8)

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open .ecfg", filetypes=[("ECFG", "*.ecfg"), ("All files", "*.*")]
        )
        if not path:
            return
        with open(path, "rb") as fh:
            data = fh.read()
        try:
            self._defn = ecfg.parse(data)
        except ecfg.EcfgError as exc:
            messagebox.showerror("Parse failed", str(exc))
            return
        self._entries = dtc.build_catalog(self._defn)
        self.file_lbl.config(text=os.path.basename(path))
        self.report.delete("1.0", "end")
        self.report.insert("1.0", dtc.summary(self._entries))

    def export_csv(self):
        if not self._entries:
            messagebox.showinfo("xcaltool", "Open an .ecfg first.")
            return
        out = filedialog.asksaveasfilename(defaultextension=".csv")
        if not out:
            return
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(dtc.to_csv(self._entries))
        messagebox.showinfo("Done", f"Wrote {out}")

    def export_xdf(self):
        if not self._entries:
            messagebox.showinfo("xcaltool", "Open an .ecfg first.")
            return
        out = filedialog.asksaveasfilename(defaultextension=".xdf")
        if not out:
            return
        xdf = dtc.to_xdf(self._defn, self._entries,
                         include_emissions=self.incl_emis.get())
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(xdf)
        messagebox.showinfo("Done", f"Wrote {out}")


class FaultCodeTab(ttk.Frame):
    """Import the Cummins service fault-code .xls and search/export it."""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self._records = []

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Import fault-code .xls...",
                   command=self.import_xls).pack(side="left")
        ttk.Button(top, text="Open fault-code .csv...",
                   command=self.open_csv).pack(side="left", padx=6)
        ttk.Button(top, text="Export CSV...",
                   command=self.export_csv).pack(side="left")
        self.file_lbl = ttk.Label(top, text="No fault-code table loaded")
        self.file_lbl.pack(side="left", padx=10)

        search = ttk.Frame(self)
        search.pack(fill="x", pady=8)
        ttk.Label(search, text="Find (fault code / SPN / text):").pack(side="left")
        self.query = tk.StringVar()
        ent = ttk.Entry(search, textvariable=self.query, width=30)
        ent.pack(side="left", padx=6)
        ent.bind("<Return>", lambda _e: self.search())
        ttk.Button(search, text="Search", command=self.search).pack(side="left")

        self.report = tk.Text(self, height=18, wrap="none", font=("Courier", 9))
        self.report.pack(fill="both", expand=True, pady=8)

    def _loaded(self, n):
        self.file_lbl.config(text=f"{n:,} fault codes loaded")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", "Loaded. Type a fault code, SPN, or text and Search.")

    def import_xls(self):
        path = filedialog.askopenfilename(
            title="Import Cummins service fault-code .xls",
            filetypes=[("Excel", "*.xls *.xlsx"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self._records = faultcodes.import_xls(path)
        except RuntimeError as exc:
            messagebox.showerror("Import failed", str(exc))
            return
        self._loaded(len(self._records))

    def open_csv(self):
        path = filedialog.askopenfilename(
            title="Open fault-code .csv", filetypes=[("CSV", "*.csv")]
        )
        if not path:
            return
        self._records = faultcodes.load_csv(path)
        self._loaded(len(self._records))

    def export_csv(self):
        if not self._records:
            messagebox.showinfo("xcaltool", "Import an .xls or open a .csv first.")
            return
        out = filedialog.asksaveasfilename(defaultextension=".csv")
        if not out:
            return
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(faultcodes.to_csv(self._records))
        messagebox.showinfo("Done", f"Wrote {out}")

    def search(self):
        if not self._records:
            messagebox.showinfo("xcaltool", "Import an .xls or open a .csv first.")
            return
        q = self.query.get().strip().lower()
        hits = [
            r for r in self._records
            if not q or q in r.fault_code.lower() or q in r.spn.lower()
            or q in r.description.lower()
        ][:500]
        lines = [f"{'FC':>6} {'SPN':>6} {'FMI':>4} {'Pcode':>6} {'Lamp':6} Description",
                 "-" * 90]
        for r in hits:
            lines.append(f"{r.fault_code:>6} {r.spn:>6} {r.j1939_fmi:>4} "
                         f"{r.pcode:>6} {r.lamp_color:6} {r.description[:60]}")
        lines.append("")
        lines.append(f"{len(hits)} match(es)" + (" (showing first 500)" if len(hits) == 500 else ""))
        self.report.delete("1.0", "end")
        self.report.insert("1.0", "\n".join(lines))


class EcuTab(ttk.Frame):
    """Diagnostics: connect, identify, read & clear fault codes."""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self.link = None
        self._faults = []
        self._info = None
        self._active = []
        self._prev = []
        self._live_reader = None
        self._live_job = None
        self._live_csv = None

        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="Adapter:").pack(side="left")
        self._adapters = []
        self.backend = tk.StringVar()
        self.adapter_box = ttk.Combobox(row, textvariable=self.backend, width=32,
                                        state="readonly")
        self.adapter_box.pack(side="left", padx=6)
        ttk.Button(row, text="Rescan", command=self.rescan).pack(side="left")
        ttk.Label(row, text="Protocol:").pack(side="left", padx=(12, 0))
        self.proto = tk.StringVar(value="j1939")
        ttk.Combobox(row, textvariable=self.proto, width=8, state="readonly",
                     values=["j1939", "j1587"]).pack(side="left", padx=6)
        self.conn_lbl = ttk.Label(row, text="disconnected", foreground="#a00")
        self.conn_lbl.pack(side="left", padx=10)

        row2 = ttk.Frame(self)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="Module:").pack(side="left")
        self.module = tk.StringVar()
        self._module_keys = [k for k, _ in modules.profile_labels()]
        ttk.Combobox(row2, textvariable=self.module, width=34, state="readonly",
                     values=[n for _, n in modules.profile_labels()]).pack(side="left", padx=6)
        ttk.Label(row2, text="Security:").pack(side="left", padx=(12, 0))
        self.security = tk.StringVar(value="None")
        ttk.Combobox(row2, textvariable=self.security, width=22, state="readonly",
                     values=["None", "Demo (simulation only)",
                             "Custom module (.py)"]).pack(side="left", padx=6)
        if self._module_keys:
            self.module.set(modules.profile_labels()[-1][1])   # default CM2450

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Connect", command=self.connect).pack(side="left")
        ttk.Button(btns, text="Disconnect", command=self.disconnect).pack(side="left", padx=6)
        ttk.Button(btns, text="Identify", command=self.identify).pack(side="left")
        ttk.Button(btns, text="Read codes", command=self.read_codes).pack(side="left", padx=6)
        ttk.Button(btns, text="Clear codes", command=self.clear_codes).pack(side="left")
        ttk.Button(btns, text="Load fault-code CSV",
                   command=self.load_faults).pack(side="left", padx=6)

        fbtns = ttk.Frame(self)
        fbtns.pack(fill="x")
        ttk.Label(fbtns, text="Flash:").pack(side="left")
        ttk.Button(fbtns, text="Read image -> file",
                   command=self.read_image).pack(side="left", padx=6)
        ttk.Button(fbtns, text="Write image (backup+verify)",
                   command=self.write_image).pack(side="left")
        self.prog = ttk.Progressbar(fbtns, length=220, mode="determinate")
        self.prog.pack(side="left", padx=10)

        lrow = ttk.Frame(self)
        lrow.pack(fill="x", pady=(4, 0))
        ttk.Label(lrow, text="Live data:").pack(side="left")
        self.live_btn = ttk.Button(lrow, text="Start stream",
                                   command=self.toggle_live)
        self.live_btn.pack(side="left", padx=6)
        ttk.Button(lrow, text="Log to CSV...",
                   command=self.start_live_csv).pack(side="left")
        ttk.Button(lrow, text="Save report...",
                   command=self.save_report).pack(side="left", padx=(12, 0))

        # Two-column grid (row, col = divmod(index, 2)); ECU CODE at index 3
        # sits directly under ESN (index 1).
        self._id_fields = [
            ("VIN", "vin"), ("ESN", "serial"),
            ("SW VERSION", "calibration_id"), ("ECU CODE", "ecm_code"),
            ("ECU PN", "part_number"), ("ENGINE CPL", "cpl"),
            ("ENGINE HP", "rated_hp"), ("ENGINE TQ", "rated_torque"),
        ]
        self._id_vars = {}
        tag = ttk.LabelFrame(self, text="ECU data tag", padding=8)
        tag.pack(fill="x", pady=(4, 0))
        for i, (label, key) in enumerate(self._id_fields):
            r, c = divmod(i, 2)
            ttk.Label(tag, text=label + ":", width=12,
                      anchor="e").grid(row=r, column=c * 2, sticky="e", padx=(4, 6), pady=2)
            var = tk.StringVar(value="—")
            self._id_vars[key] = var
            ttk.Label(tag, textvariable=var, width=30, anchor="w",
                      font=("Courier", 9)).grid(row=r, column=c * 2 + 1, sticky="w")

        self._live_vars = {}
        live = ttk.LabelFrame(self, text="Live data", padding=8)
        live.pack(fill="x", pady=(4, 0))
        for i, sig in enumerate(livedata.SIGNALS):
            r, c = divmod(i, 3)
            ttk.Label(live, text=sig.label + ":", width=13,
                      anchor="e").grid(row=r, column=c * 2, sticky="e",
                                       padx=(4, 4), pady=1)
            var = tk.StringVar(value="—")
            self._live_vars[sig.key] = var
            ttk.Label(live, textvariable=var, width=10, anchor="w",
                      font=("Courier", 9)).grid(row=r, column=c * 2 + 1,
                                                sticky="w")

        ttk.Label(
            self,
            text="Connect auto-identifies and fills the data tag above. "
                 "Simulation runs with no hardware (J1939). RP1210/J2534/"
                 "SocketCAN need a real adapter. Flash read/write use J1939 "
                 "DM14/15/16 and need an authorized seed/key module for real "
                 "ECUs; 'Demo' unlocks only the simulator. Writes back up + "
                 "verify first. (CPL/ECU CODE come from the ECM's ID fields and "
                 "may be blank on some modules.)",
            foreground="#555", wraplength=720, justify="left",
        ).pack(anchor="w", pady=(6, 0))

        self.out = tk.Text(self, height=12, wrap="none", font=("Courier", 9))
        self.out.pack(fill="both", expand=True, pady=8)
        self.rescan()

    def _set_datatag(self, info):
        values = dataclasses.asdict(info)
        for key, var in self._id_vars.items():
            var.set(values.get(key) or "—")

    def _clear_datatag(self):
        for var in self._id_vars.values():
            var.set("—")

    def _log(self, text):
        self.out.insert("end", text + "\n")
        self.out.see("end")

    def rescan(self):
        self._adapters = transport.discover_adapters()
        labels = [a.label for a in self._adapters]
        self.adapter_box.config(values=labels)
        if labels:
            self.backend.set(labels[0])
        extra = len(self._adapters) - 1
        self._log(f"Found {extra} hardware adapter(s) + simulation." if extra
                  else "No hardware adapters found; simulation available.")

    def _selected_adapter(self):
        label = self.backend.get()
        for a in self._adapters:
            if a.label == label:
                return a
        return None

    def _build_link(self):
        adapter = self._selected_adapter()
        if adapter is None:
            messagebox.showinfo("xcaltool", "Pick an adapter (or press Rescan).")
            return None
        proto = self.proto.get()
        if adapter.kind == "simulation" and proto != "j1939":
            messagebox.showinfo("xcaltool", "Simulation currently models a "
                                "J1939 ECM. Switch protocol to j1939.")
            return None
        if adapter.kind == "rp1210" and struct.calcsize("P") == 8:
            messagebox.showwarning(
                "32-bit driver",
                "RP1210 driver DLLs (e.g. Nexiq NULN2R32.dll) are 32-bit and "
                "will not load into 64-bit Python. Run this app with 32-bit "
                "Python on Windows to use the USB-Link 2.")
        adapter.protocol = proto
        t = adapter.make()
        t.protocol = proto
        return comms.DiagnosticLink(t)

    def connect(self):
        self.link = self._build_link()
        if self.link is None:
            return
        try:
            self.link.connect()
        except Exception as exc:                       # hardware/driver errors
            self.link = None
            messagebox.showerror("Connect failed", str(exc))
            return
        self.conn_lbl.config(text=f"connected ({self.backend.get()})", foreground="#080")
        self._log(f"Connected via {self.backend.get()} [{self.proto.get()}].")
        self.identify()                                # auto-ID + fill data tag

    def disconnect(self):
        self._live_reader = None
        self._stop_live()
        if self.link:
            self.link.disconnect()
            self.link = None
        self.conn_lbl.config(text="disconnected", foreground="#a00")
        self._clear_datatag()

    def _need_link(self):
        if self.link is None:
            messagebox.showinfo("xcaltool", "Connect first.")
            return False
        return True

    def identify(self):
        if not self._need_link():
            return
        try:
            info = self.link.identify()
        except Exception as exc:
            messagebox.showerror("Identify failed", str(exc))
            return
        self._info = info
        self._set_datatag(info)
        self._log("-- ECU identity --")
        for label, key in self._id_fields:
            self._log(f"  {label:<11}: {self._id_vars[key].get()}")
        if info.make or info.model:
            self._log(f"  MAKE/MODEL : {info.make} {info.model}".rstrip())
        if info.software:
            self._log(f"  SOFTWARE   : {', '.join(info.software)}")
        vin = report.decode_vin(info.vin)
        if vin:
            chk = "ok" if vin["valid_check_digit"] == "yes" else "invalid"
            self._log(f"  VIN DECODE : WMI {vin['wmi']} · year {vin['model_year']}"
                      f" · plant {vin['plant']} · serial {vin['serial']}"
                      f" · check digit {chk}")

    def read_codes(self):
        if not self._need_link():
            return
        try:
            active = self.link.read_dtcs(active=True)
            prev = self.link.read_dtcs(active=False)
        except Exception as exc:
            messagebox.showerror("Read failed", str(exc))
            return
        if self._faults:
            comms.annotate_descriptions(active, self._faults)
            comms.annotate_descriptions(prev, self._faults)
        self._active, self._prev = active, prev
        self._log(f"-- Active codes ({len(active)}) --")
        for d in active:
            self._log("  " + d.label())
        self._log(f"-- Previously active ({len(prev)}) --")
        for d in prev:
            self._log("  " + d.label())

    def clear_codes(self):
        if not self._need_link():
            return
        if not messagebox.askyesno("Clear codes", "Clear active and previously "
                                   "active fault codes on the ECU?"):
            return
        try:
            self.link.clear_dtcs(active=True)
            self.link.clear_dtcs(active=False)
        except Exception as exc:
            messagebox.showerror("Clear failed", str(exc))
            return
        self._log("Cleared active and previously-active codes.")

    def load_faults(self):
        path = filedialog.askopenfilename(
            title="Load Cummins fault-code CSV (for descriptions)",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        self._faults = faultcodes.load_csv(path)
        self._log(f"Loaded {len(self._faults):,} fault-code descriptions.")

    # -- live data ---------------------------------------------------------
    def toggle_live(self):
        if self._live_job is not None:
            self._stop_live()
            return
        if not self._need_link():
            return
        self._live_reader = livedata.LiveDataReader(self.link)
        self.live_btn.config(text="Stop stream")
        self._log("-- live data started --")
        self._poll_live()

    def _stop_live(self):
        if self._live_job is not None:
            self.after_cancel(self._live_job)
            self._live_job = None
        if self._live_csv is not None:
            self._live_csv.close()
            self._live_csv = None
            self._log("Live CSV log closed.")
        self.live_btn.config(text="Start stream")

    def _poll_live(self):
        if self._live_reader is None:
            return
        try:
            values = self._live_reader.poll()
        except Exception as exc:
            self._log(f"Live data stopped: {exc}")
            self._stop_live()
            return
        for sig in livedata.SIGNALS:
            if sig.key in values:
                self._live_vars[sig.key].set(
                    f"{livedata.format_value(values[sig.key])} {sig.unit}")
        if self._live_csv is not None:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            cols = [ts] + [livedata.format_value(values.get(s.key, ""))
                           if s.key in values else ""
                           for s in livedata.SIGNALS]
            self._live_csv.write(",".join(str(c) for c in cols) + "\n")
            self._live_csv.flush()
        self._live_job = self.after(500, self._poll_live)

    def start_live_csv(self):
        path = filedialog.asksaveasfilename(
            title="Log live data to CSV", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        self._live_csv = open(path, "w", encoding="utf-8")
        header = ["time"] + [f"{s.label} ({s.unit})" for s in livedata.SIGNALS]
        self._live_csv.write(",".join(header) + "\n")
        self._log(f"Logging live data -> {path}")
        if self._live_job is None:
            self.toggle_live()

    # -- report ------------------------------------------------------------
    def save_report(self):
        if self._info is None:
            messagebox.showinfo("xcaltool", "Connect/Identify first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save service report", defaultextension=".txt",
            filetypes=[("Text", "*.txt")])
        if not path:
            return
        text = report.build_report(self._info, self._active, self._prev)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self._log(f"Saved service report -> {path}")

    # -- flash read/write --------------------------------------------------
    def _selected_profile(self):
        idx = [n for _, n in modules.profile_labels()].index(self.module.get())
        return modules.get_profile(self._module_keys[idx])

    def _build_security(self):
        choice = self.security.get()
        if choice.startswith("Demo"):
            return comms.DemoSecurityProvider()
        if choice.startswith("Custom"):
            path = filedialog.askopenfilename(
                title="Seed/key module (.py exposing key_from_seed(seed, level))",
                filetypes=[("Python", "*.py")])
            if not path:
                return None
            return _load_security_module(path)
        return None

    def _build_flasher(self):
        if not self._need_link():
            return None
        try:
            security = self._build_security()
        except Exception as exc:
            messagebox.showerror("Security module", str(exc))
            return None
        return comms.J1939Flasher(self.link, self._selected_profile(),
                                  security=security)

    def _progress(self, done, total):
        self.prog["maximum"] = total
        self.prog["value"] = done
        self.update_idletasks()

    def read_image(self):
        flasher = self._build_flasher()
        if flasher is None:
            return
        prof = flasher.profile
        self._log(f"Reading {prof.name} ({prof.total_bytes():,} bytes)...")
        try:
            image = flasher.read_image(self._progress)
        except Exception as exc:
            messagebox.showerror("Read failed", str(exc))
            return
        path = filedialog.asksaveasfilename(
            title="Save raw image", defaultextension=".bin",
            filetypes=[("Raw image", "*.bin")])
        if not path:
            return
        with open(path, "wb") as fh:
            fh.write(image)
        self._log(f"Saved raw image -> {path} ({len(image):,} bytes)")
        self._log_hashes(image)
        self._offer_convert(image, path)

    def _log_hashes(self, image, label="image"):
        h = report.image_hashes(image)
        self._log(f"  {label} integrity: size={h['size']} bytes  "
                  f"CRC32={h['crc32']}  SHA256={h['sha256']}")

    def _offer_convert(self, image, bin_path):
        if not messagebox.askyesno(
                "Convert",
                "Also convert this raw image to .xcal and EFILive _efi.bin?\n"
                "(You'll pick the matching original .xcal as a template for the "
                "header/token/layout.)"):
            return
        tpl = filedialog.askopenfilename(
            title="Matching original .xcal (template)",
            filetypes=[("xcal", "*.xcal"), ("all files", "*.*")])
        if not tpl:
            return
        try:
            with open(tpl, "rb") as fh:
                template = fh.read()
            base = os.path.splitext(bin_path)[0]
            xcal = xcalfmt.build_from_template(image, template)
            xpath = base + ".xcal"
            with open(xpath, "wb") as fh:
                fh.write(xcal)
            self._log(f"Wrote .xcal -> {xpath} ({len(xcal):,} bytes)")
            efi = xcalfmt.to_efi_bin(xcalfmt.parse(xcal))
            epath = base + "_efi.bin"
            with open(epath, "wb") as fh:
                fh.write(efi)
            self._log(f"Wrote EFILive _efi.bin -> {epath} ({len(efi):,} bytes)")
            messagebox.showinfo(
                "Done",
                "Wrote .xcal and EFILive _efi.bin next to the raw image.\n\n"
                "Note: the template's 4-char token was reused as-is (it can't be "
                "recomputed), so if you changed bytes EFILive may need to "
                "re-accept the file.")
        except Exception as exc:
            messagebox.showerror("Convert failed", str(exc))

    def write_image(self):
        flasher = self._build_flasher()
        if flasher is None:
            return
        path = filedialog.askopenfilename(
            title="Image to write (.bin)", filetypes=[("Raw image", "*.bin")])
        if not path:
            return
        with open(path, "rb") as fh:
            image = fh.read()
        prof = flasher.profile
        if len(image) != prof.image_size:
            if not messagebox.askyesno(
                    "Size mismatch",
                    f"Image is {len(image):,} bytes but {prof.name} expects "
                    f"{prof.image_size:,}. Continue anyway?"):
                return
        if not messagebox.askyesno(
                "Confirm write",
                f"WRITE this image to the ECU via {self.backend.get()}?\n\n"
                "A backup is read first and the write is verified. This changes "
                "the ECU. Make sure power is stable."):
            return
        self._log(f"Writing {prof.name}... (backup first)")
        try:
            backup = flasher.write_image(image, self._progress, verify=True)
        except Exception as exc:
            messagebox.showerror("Write failed", str(exc))
            return
        bpath = os.path.splitext(path)[0] + ".backup.bin"
        with open(bpath, "wb") as fh:
            fh.write(backup)
        self._log(f"Write verified OK. Pre-write backup saved -> {bpath}")
        self._log_hashes(backup, label="backup")
        self._log_hashes(image, label="written")


def _load_security_module(path):
    """Load a user seed/key module exposing key_from_seed(seed, level)."""
    spec = importlib.util.spec_from_file_location("user_security", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "key_from_seed"):
        raise ValueError("module has no key_from_seed(seed, level) function")

    class _Provider(comms.SecurityProvider):
        def key_from_seed(self, seed, level=1):
            return mod.key_from_seed(seed, level)

    return _Provider()


class CompareTab(ttk.Frame):
    """Diff two calibration images (.bin/.xcal) and show what changed."""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self._path_a = ""
        self._path_b = ""

        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Button(row, text="File A...",
                   command=lambda: self._pick("a")).pack(side="left")
        self.lbl_a = ttk.Label(row, text="(none)")
        self.lbl_a.pack(side="left", padx=8)
        row2 = ttk.Frame(self)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Button(row2, text="File B...",
                   command=lambda: self._pick("b")).pack(side="left")
        self.lbl_b = ttk.Label(row2, text="(none)")
        self.lbl_b.pack(side="left", padx=8)

        act = ttk.Frame(self)
        act.pack(fill="x", pady=8)
        ttk.Button(act, text="Compare", command=self.compare).pack(side="left")
        ttk.Button(act, text="Save diff report...",
                   command=self.save_report).pack(side="left", padx=6)
        ttk.Label(
            self,
            text="Accepts .xcal or .bin on either side (each is reduced to its "
                 "raw flash image first). Nearby changed bytes are grouped into "
                 "one diff run so a changed value/table shows as a single entry.",
            foreground="#555", wraplength=720, justify="left").pack(anchor="w")

        self.out = tk.Text(self, height=26, wrap="none", font=("Courier", 9))
        self.out.pack(fill="both", expand=True, pady=8)
        self._result = None

    def _pick(self, which):
        path = filedialog.askopenfilename(
            title=f"Calibration file {which.upper()}",
            filetypes=[("xcal/bin", "*.xcal *.bin"), ("all files", "*.*")])
        if not path:
            return
        if which == "a":
            self._path_a = path
            self.lbl_a.config(text=os.path.basename(path))
        else:
            self._path_b = path
            self.lbl_b.config(text=os.path.basename(path))

    def compare(self):
        if not (self._path_a and self._path_b):
            messagebox.showinfo("xcaltool", "Pick both File A and File B.")
            return
        try:
            with open(self._path_a, "rb") as fh:
                a = calcompare.load_image(fh.read())
            with open(self._path_b, "rb") as fh:
                b = calcompare.load_image(fh.read())
        except Exception as exc:
            messagebox.showerror("Compare failed", str(exc))
            return
        self._result = calcompare.compare_images(a, b)
        self.out.delete("1.0", "end")
        self.out.insert("end", calcompare.format_report(self._result))

    def save_report(self):
        if self._result is None:
            messagebox.showinfo("xcaltool", "Run a compare first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save diff report", defaultextension=".txt",
            filetypes=[("Text", "*.txt")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(calcompare.format_report(self._result, limit=100000))
        messagebox.showinfo("xcaltool", f"Saved -> {path}")


class BatchTab(ttk.Frame):
    """Batch-convert every .xcal in a folder to .bin + EFILive _efi.bin."""

    def __init__(self, master):
        super().__init__(master, padding=10)
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Button(row, text="Choose folder & convert...",
                   command=self.run).pack(side="left")
        self.prog = ttk.Progressbar(row, length=240, mode="determinate")
        self.prog.pack(side="left", padx=10)
        ttk.Label(
            self,
            text="Finds every .xcal in the chosen folder (non-recursive) and "
                 "writes <name>.bin (flat) and <name>_efi.bin (EFILive compact) "
                 "next to each one.",
            foreground="#555", wraplength=720, justify="left").pack(anchor="w")
        self.out = tk.Text(self, height=26, wrap="none", font=("Courier", 9))
        self.out.pack(fill="both", expand=True, pady=8)

    def run(self):
        folder = filedialog.askdirectory(title="Folder of .xcal files")
        if not folder:
            return
        self.out.delete("1.0", "end")

        def progress(done, total, name):
            self.prog["maximum"] = max(total, 1)
            self.prog["value"] = done
            self.out.insert("end", f"[{done}/{total}] {name}\n")
            self.out.see("end")
            self.update_idletasks()

        items = batch.convert_folder(folder, progress)
        ok = sum(1 for it in items if it.ok)
        self.out.insert("end", f"\nDone: {ok}/{len(items)} converted.\n")
        for it in items:
            if not it.ok:
                self.out.insert("end", f"  FAILED {os.path.basename(it.source)}: "
                                       f"{it.error}\n")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"xcaltool {__version__}")
        self.geometry("820x680")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        nb.add(XcalBinTab(nb), text="xcal <-> bin")
        nb.add(BatchTab(nb), text="Batch convert")
        nb.add(CompareTab(nb), text="Compare")
        nb.add(EcfgTab(nb), text="ecfg -> xdf/csv")
        nb.add(DtcTab(nb), text="DTC catalog")
        nb.add(FaultCodeTab(nb), text="Fault codes")
        nb.add(EcuTab(nb), text="ECU diagnostics")


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
