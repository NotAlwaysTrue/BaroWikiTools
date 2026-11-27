"""Folder selection and export status for the sprite exporter."""

from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


def show_window(content, output, all_sprites, export):
    root = tk.Tk()
    root.title("Barotrauma Sprite Exporter")
    root.minsize(640, 360)
    panel = ttk.Frame(root, padding=18)
    panel.pack(fill="both", expand=True)
    panel.columnconfigure(1, weight=1)
    panel.rowconfigure(4, weight=1)

    messages = Queue()
    controls = []
    running = False

    def folder_field(row, title, initial, must_exist):
        value = tk.StringVar(value="" if initial is None else str(initial))
        ttk.Label(panel, text=title).grid(row=row, column=0, sticky="w", pady=5)
        entry = ttk.Entry(panel, textvariable=value)
        entry.grid(row=row, column=1, sticky="ew", padx=10, pady=5)

        def browse():
            current = Path(value.get()).expanduser()
            initial_dir = current if current.is_dir() else current.parent
            selected = filedialog.askdirectory(
                parent=root,
                title=f"Select {title.lower()}",
                initialdir=str(initial_dir) if initial_dir.is_dir() else str(Path.cwd()),
                mustexist=must_exist,
            )
            if selected:
                value.set(selected)

        button = ttk.Button(panel, text="Browse...", command=browse)
        button.grid(row=row, column=2, sticky="ew", pady=5)
        controls.extend((entry, button))
        return value

    source_value = folder_field(0, "Content folder", content, True)
    output_value = folder_field(1, "Output folder", output, False)
    include_all = tk.BooleanVar(value=all_sprites)
    include_control = ttk.Checkbutton(panel, text="Include additional sprites", variable=include_all)
    include_control.grid(row=2, column=1, sticky="w", padx=10, pady=5)
    status = tk.StringVar(value="Ready")
    ttk.Label(panel, textvariable=status).grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 6))
    log = ScrolledText(panel, state="disabled", wrap="word", height=8, width=70)
    log.grid(row=4, column=0, columnspan=3, sticky="nsew")

    def write_log(message, *, clear=False):
        log.configure(state="normal")
        if clear:
            log.delete("1.0", "end")
        else:
            log.insert("end", message + "\n")
            log.see("end")
        log.configure(state="disabled")

    def set_running(active):
        nonlocal running
        running = active
        for widget in controls:
            widget.state(["disabled" if active else "!disabled"])

    def poll():
        lines = []
        for _ in range(200):
            try:
                kind, value = messages.get_nowait()
            except Empty:
                break
            if kind == "message":
                lines.append(value)
            elif kind == "result":
                lines.append(value.summary())
                status.set("Export finished with errors" if value.errors else "Export complete")
                set_running(False)
            else:
                lines.append(f"Error: {value}")
                status.set("Export failed")
                set_running(False)
        if lines:
            write_log("\n".join(lines))
        if running:
            root.after(75, poll)

    def start_export():
        source = source_value.get().strip()
        destination = output_value.get().strip()
        if not source or not destination:
            messagebox.showerror("Missing folder", "Select both a Content folder and an output folder.", parent=root)
            return
        selected_content = Path(source).expanduser()
        selected_output = Path(destination).expanduser()
        selected_mode = include_all.get()

        def log_message(message):
            messages.put(("message", message))

        def worker():
            try:
                result = export(
                    selected_content,
                    selected_output,
                    all_sprites=selected_mode,
                    report=log_message,
                    log=log_message,
                )
                messages.put(("result", result))
            except Exception as error:
                messages.put(("error", str(error)))

        write_log("", clear=True)
        status.set("Exporting sprites...")
        set_running(True)
        Thread(target=worker, daemon=True).start()
        root.after(75, poll)

    start = ttk.Button(panel, text="Export Sprites", command=start_export)
    start.grid(row=5, column=2, sticky="e", pady=(12, 0))
    controls.extend((include_control, start))

    def close_window():
        if not running or messagebox.askokcancel(
            "Exit exporter", "An export is running. Close the exporter and stop the current export?", parent=root
        ):
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_window)
    root.mainloop()
