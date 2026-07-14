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

from . import __version__, codec, ecfg
from .comms import NotConnectedBackend


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
    """Tab for xcal <-> bin conversion."""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self._data = b""
        self._path = ""

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Open file...", command=self.open_file).pack(side="left")
        self.file_lbl = ttk.Label(top, text="No file loaded")
        self.file_lbl.pack(side="left", padx=10)

        # Container layout controls
        opts = ttk.LabelFrame(self, text="Container layout", padding=8)
        opts.pack(fill="x", pady=8)
        ttk.Label(opts, text="Header bytes:").grid(row=0, column=0, sticky="w")
        self.header_var = tk.IntVar(value=0)
        ttk.Spinbox(opts, from_=0, to=1_000_000, textvariable=self.header_var,
                    width=10).grid(row=0, column=1, padx=6)
        ttk.Label(opts, text="Trailer bytes:").grid(row=0, column=2, sticky="w")
        self.trailer_var = tk.IntVar(value=0)
        ttk.Spinbox(opts, from_=0, to=1_000_000, textvariable=self.trailer_var,
                    width=10).grid(row=0, column=3, padx=6)
        ttk.Label(opts, text="Checksum:").grid(row=0, column=4, sticky="w")
        self.checksum_var = tk.StringVar(value="none")
        ttk.Combobox(
            opts, textvariable=self.checksum_var, width=12, state="readonly",
            values=["none", "sum8", "sum16", "sum32", "crc16_ccitt", "crc32"],
        ).grid(row=0, column=5, padx=6)

        # Action buttons
        actions = ttk.Frame(self)
        actions.pack(fill="x")
        ttk.Button(actions, text="xcal -> bin", command=self.xcal_to_bin).pack(side="left")
        ttk.Button(actions, text="bin -> xcal", command=self.bin_to_xcal).pack(side="left", padx=6)
        ttk.Button(actions, text="Auto-detect", command=self.auto_detect).pack(side="left")

        # Report + hex preview
        self.report = tk.Text(self, height=6, wrap="none")
        self.report.pack(fill="x", pady=8)
        self.hex = tk.Text(self, height=16, wrap="none", font=("Courier", 9))
        self.hex.pack(fill="both", expand=True)

    # -- helpers -----------------------------------------------------------
    def _spec(self):
        cs = self.checksum_var.get()
        return codec.ContainerSpec(
            header_len=self.header_var.get(),
            trailer_len=self.trailer_var.get(),
            checksum=None if cs == "none" else cs,
        )

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
        self.auto_detect()
        self._set_hex(self._data)

    def auto_detect(self):
        if not self._data:
            return
        info = codec.analyze(self._data)
        spec = codec.guess_spec(self._data)
        self.header_var.set(spec.header_len)
        self.trailer_var.set(spec.trailer_len)
        self._set_report(
            "\n".join(f"{k}: {v}" for k, v in info.items())
            + f"\n\nGuessed header bytes: {spec.header_len} "
            "(adjust above if wrong)"
        )

    def xcal_to_bin(self):
        if not self._data:
            messagebox.showinfo("xcaltool", "Open a file first.")
            return
        try:
            result = codec.extract_bin(self._data, self._spec())
        except codec.ConversionError as exc:
            messagebox.showerror("Conversion failed", str(exc))
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".bin", filetypes=[("Binary image", "*.bin")]
        )
        if not out:
            return
        with open(out, "wb") as fh:
            fh.write(result.payload)
        # Save a sidecar so bin -> xcal can rebuild the exact original.
        codec.write_sidecar(out + ".xcalmeta", result)
        messagebox.showinfo(
            "Done",
            f"Wrote {len(result.payload)} bytes to\n{out}\n\n"
            "A .xcalmeta sidecar was saved so you can rebuild the exact "
            "original .xcal later.",
        )
        self._set_hex(result.payload)

    def bin_to_xcal(self):
        if not self._data:
            messagebox.showinfo("xcaltool", "Open a file first.")
            return
        sidecar_path = self._path + ".xcalmeta"
        try:
            if os.path.exists(sidecar_path):
                sidecar = codec.read_sidecar(sidecar_path)
                blob = codec.rebuild_from_sidecar(self._data, sidecar)
            else:
                blob = codec.build_xcal(self._data, spec=self._spec())
        except codec.ConversionError as exc:
            messagebox.showerror("Conversion failed", str(exc))
            return
        out = filedialog.asksaveasfilename(
            defaultextension=".xcal", filetypes=[("xcal file", "*.xcal")]
        )
        if not out:
            return
        with open(out, "wb") as fh:
            fh.write(blob)
        messagebox.showinfo("Done", f"Wrote {len(blob)} bytes to\n{out}")


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


class EcuTab(ttk.Frame):
    """Placeholder tab for future ECU read/write."""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self.backend = NotConnectedBackend()
        ttk.Label(
            self,
            text="Live ECU read/write is planned. The interface is stubbed so "
                 "it can be added without changing the rest of the app.",
            wraplength=520, justify="left",
        ).pack(anchor="w", pady=(0, 10))
        ttk.Button(self, text="Read from ECU", command=self._todo).pack(anchor="w")
        ttk.Button(self, text="Write to ECU", command=self._todo).pack(anchor="w", pady=6)

    def _todo(self):
        try:
            self.backend.connect()
        except NotImplementedError as exc:
            messagebox.showinfo("Coming soon", str(exc))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"xcaltool {__version__}")
        self.geometry("760x620")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        nb.add(XcalBinTab(nb), text="xcal <-> bin")
        nb.add(EcfgTab(nb), text="ecfg -> xdf/csv")
        nb.add(EcuTab(nb), text="ECU (read/write)")


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
