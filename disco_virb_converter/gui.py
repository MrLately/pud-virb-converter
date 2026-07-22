from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from disco_virb_converter.outputs import convert_file


def run_gui() -> None:
    root = tk.Tk()
    root.title("Disco PUD to Garmin/VIRB Converter")
    root.geometry("760x460")
    App(root)
    root.mainloop()


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.offset_var = tk.StringVar(value="0")
        self.last_output: Path | None = None

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

        ttk.Label(frame, text="Input").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(frame, text="Browse", command=self.pick_input).grid(row=0, column=2)

        ttk.Label(frame, text="Output").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(frame, text="Browse", command=self.pick_output).grid(row=1, column=2)

        ttk.Label(frame, text="Offset seconds").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.offset_var, width=12).grid(row=2, column=1, sticky="w", padx=6)

        controls = ttk.Frame(frame)
        controls.grid(row=3, column=0, columnspan=3, sticky="ew", pady=8)
        self.convert_button = ttk.Button(controls, text="Convert", command=self.convert)
        self.convert_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(controls, text="Open Output Folder", command=self.open_output, state=tk.DISABLED)
        self.open_button.pack(side=tk.LEFT, padx=8)

        self.log = tk.Text(frame, height=16, wrap=tk.WORD)
        self.log.grid(row=4, column=0, columnspan=3, sticky="nsew")
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        scroll.grid(row=4, column=3, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    def pick_input(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select FreeFlight export or PUD",
            filetypes=[
                ("Flight exports", "*.zip *.pud *.json *.txt *.gz"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            self.input_var.set(selected)

    def pick_output(self) -> None:
        selected = filedialog.askdirectory(title="Select output folder")
        if selected:
            self.output_var.set(selected)

    def convert(self) -> None:
        input_path = self.input_var.get().strip()
        if not input_path:
            messagebox.showerror("Missing input", "Choose a FreeFlight export, PUD, or JSON file.")
            return
        try:
            offset = float(self.offset_var.get().strip() or "0")
        except ValueError:
            messagebox.showerror("Bad offset", "Offset seconds must be a number.")
            return

        output = self.output_var.get().strip() or None
        self.convert_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)
        self.log_delete()
        self.log_write("Converting...\n")

        def worker() -> None:
            try:
                result = convert_file(input_path, out_dir=output, offset_seconds=offset)
            except Exception as exc:  # noqa: BLE001 - GUI should show concise failure
                self.root.after(0, lambda: self.fail(exc))
                return
            self.root.after(0, lambda: self.done(result))

        threading.Thread(target=worker, daemon=True).start()

    def done(self, result) -> None:
        self.last_output = result.output_dir
        self.output_var.set(str(result.output_dir))
        self.log_write(f"Output folder: {result.output_dir}\n")
        self.log_write(f"FIT: {result.fit_path.name}\n")
        self.log_write(f"GPX: {result.gpx_path.name}\n")
        self.log_write(f"CSV: {result.csv_path.name}\n")
        self.log_write(f"Samples: {len(result.rows)}  Skipped: {result.skipped_rows}\n")
        if result.warnings:
            self.log_write("\nWarnings:\n")
            for warning in result.warnings:
                self.log_write(f"- {warning}\n")
        self.convert_button.configure(state=tk.NORMAL)
        self.open_button.configure(state=tk.NORMAL)

    def fail(self, exc: Exception) -> None:
        self.log_write(f"ERROR: {exc}\n")
        self.convert_button.configure(state=tk.NORMAL)
        messagebox.showerror("Conversion failed", str(exc))

    def open_output(self) -> None:
        if self.last_output and self.last_output.exists():
            os.startfile(str(self.last_output))

    def log_delete(self) -> None:
        self.log.delete("1.0", tk.END)

    def log_write(self, text: str) -> None:
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
