"""Tkinter GUI for xcaltool.

Three tabs:
  1. xcal <-> bin   -- convert calibration containers to raw images and back
  2. ecfg -> xdf/csv -- turn a Cummins ECFG definition into TunerPro XDF / CSV
  3. ECU (read/write) -- placeholder for future live ECU support

The GUI only handles user interaction; all real work lives in the codec / ecfg
/ comms modules so it stays easy to follow.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import json

from . import __version__, comms, dtc, ecfg, faultcodes, transport, xcalfmt


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
                "",
                "Click 'xcal -> bin' to extract the raw flash image.",
            ]
            self._set_report("\n".join(lines))
        else:
            sidecar = self._path + ".xcalmeta"
            has = os.path.exists(sidecar)
            self._set_report(
                f"Detected: raw .bin ({len(self._data):,} bytes)\n"
                f"Sidecar {'found' if has else 'NOT found'}: "
                f"{os.path.basename(sidecar)}\n\n"
                + ("Click 'bin -> xcal' to rebuild the .xcal."
                   if has else
                   "Extract this bin from its .xcal first so a .xcalmeta sidecar "
                   "exists, then bin -> xcal can rebuild it.")
            )

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

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Connect", command=self.connect).pack(side="left")
        ttk.Button(btns, text="Disconnect", command=self.disconnect).pack(side="left", padx=6)
        ttk.Button(btns, text="Identify", command=self.identify).pack(side="left")
        ttk.Button(btns, text="Read codes", command=self.read_codes).pack(side="left", padx=6)
        ttk.Button(btns, text="Clear codes", command=self.clear_codes).pack(side="left")
        ttk.Button(btns, text="Load fault-code CSV",
                   command=self.load_faults).pack(side="left", padx=6)

        ttk.Label(
            self,
            text="Simulation runs with no hardware (J1939). RP1210/J2534/"
                 "SocketCAN need a real adapter. Reading/clearing codes is safe "
                 "diagnostics; it does not modify the tune.",
            foreground="#555", wraplength=720, justify="left",
        ).pack(anchor="w")

        self.out = tk.Text(self, height=20, wrap="none", font=("Courier", 9))
        self.out.pack(fill="both", expand=True, pady=8)
        self.rescan()

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

    def disconnect(self):
        if self.link:
            self.link.disconnect()
            self.link = None
        self.conn_lbl.config(text="disconnected", foreground="#a00")

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
        self._log("-- ECU identity --")
        self._log(f"  VIN            : {info.vin}")
        self._log(f"  ESN (serial)   : {info.serial}")
        self._log(f"  ECFG/cal version: {info.calibration_id}")
        self._log(f"  make/model     : {info.make} {info.model}".rstrip())
        if info.part_number:
            self._log(f"  ECU part no.   : {info.part_number}")
        if info.software:
            self._log(f"  software       : {', '.join(info.software)}")

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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"xcaltool {__version__}")
        self.geometry("760x620")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        nb.add(XcalBinTab(nb), text="xcal <-> bin")
        nb.add(EcfgTab(nb), text="ecfg -> xdf/csv")
        nb.add(DtcTab(nb), text="DTC catalog")
        nb.add(FaultCodeTab(nb), text="Fault codes")
        nb.add(EcuTab(nb), text="ECU diagnostics")


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
