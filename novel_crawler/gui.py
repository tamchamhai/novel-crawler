import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

from .cli import _default_out_dir
from .downloader import crawl, get_highest_chapter, load_checkpoint

LOG = logging.getLogger("novel_crawler")


class _QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(("log", self.format(record)))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Novel Crawler")
        self.geometry("740x540")
        self.minsize(620, 440)

        self.events: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.worker = None

        self.url_var = tk.StringVar()
        self.from_var = tk.StringVar(value="1")
        self.to_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.delay_var = tk.StringVar(value="1.5")
        self.ignore_robots_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="San sang.")

        self._build()
        handler = _QueueHandler(self.events)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        self.after(100, self._poll)

    # ---- layout ----
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self, padding=8)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="URL truyen / chuong:").grid(row=0, column=0, sticky="w", **pad)
        url_entry = ttk.Entry(frm, textvariable=self.url_var)
        url_entry.grid(row=0, column=1, columnspan=3, sticky="ew", **pad)
        url_entry.bind("<FocusOut>", lambda _e: self._suggest_out())

        ttk.Label(frm, text="Tu chuong:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.from_var, width=10).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(frm, text="Den chuong (de trong = het truyen):").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.to_var, width=10).grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(frm, text="Thu muc luu:").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.out_var).grid(row=3, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Button(frm, text="Chon...", command=self._browse).grid(row=3, column=3, **pad)

        ttk.Label(frm, text="Delay (giay):").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.delay_var, width=10).grid(row=4, column=1, sticky="w", **pad)
        ttk.Checkbutton(frm, text="Bo qua robots.txt (tu chiu trach nhiem)", variable=self.ignore_robots_var).grid(
            row=4, column=2, columnspan=2, sticky="w", **pad
        )

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=4, sticky="w", **pad)
        self.start_btn = ttk.Button(btns, text="Bat dau", command=self._start)
        self.start_btn.pack(side="left", padx=(0, 8))
        self.resume_btn = ttk.Button(btns, text="Tiep tuc (Resume)", command=self._resume)
        self.resume_btn.pack(side="left", padx=(0, 8))
        self.stop_btn = ttk.Button(btns, text="Dung", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left")

        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.grid(row=6, column=0, columnspan=4, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.status_var).grid(row=7, column=0, columnspan=4, sticky="w", **pad)

        log_frame = ttk.Frame(frm)
        log_frame.grid(row=8, column=0, columnspan=4, sticky="nsew", **pad)
        frm.rowconfigure(8, weight=1)
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # ---- helpers ----
    def _check_and_apply_checkpoint(self, out_dir: str) -> bool:
        if not out_dir or not os.path.isdir(out_dir):
            return False
        cp = load_checkpoint(out_dir)
        if cp and "next_url" in cp and "next_chapter" in cp:
            self.url_var.set(cp["next_url"])
            self.from_var.set(str(cp["next_chapter"]))
            msg = f"Da khoi phuc tien do tu checkpoint: Chuong {cp['next_chapter']}"
            self.status_var.set(msg)
            self._append_log(f"[Checkpoint] {msg} -> {cp['next_url']}")
            return True
        highest = get_highest_chapter(out_dir)
        if highest > 0:
            next_chap = highest + 1
            self.from_var.set(str(next_chap))
            msg = f"Phat hien {highest} chuong da luu. San sang tai tu chuong {next_chap}."
            self.status_var.set(msg)
            self._append_log(f"[Resume] {msg}")
            return True
        return False

    def _resume(self):
        out_dir = self.out_var.get().strip()
        if not out_dir:
            url = self.url_var.get().strip()
            if url and urlparse(url).netloc:
                out_dir = os.path.abspath(_default_out_dir(url))
                self.out_var.set(out_dir)
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showinfo("Thong bao", "Vui long chon 'Thu muc luu' truoc de kiem tra tien do.")
            return
        found = self._check_and_apply_checkpoint(out_dir)
        if found:
            messagebox.showinfo(
                "Khoi phuc tien do",
                f"Da nap thong tin chuong tiep theo ({self.from_var.get()}).\nBam 'Bat dau' de tiep tuc!",
            )
        else:
            messagebox.showinfo(
                "Khong tim thay checkpoint",
                f"Khong tim thay checkpoint hoac file chuong nao trong thu muc:\n{out_dir}",
            )

    def _suggest_out(self):
        url = self.url_var.get().strip()
        if url and not self.out_var.get().strip() and urlparse(url).netloc:
            suggested = os.path.abspath(_default_out_dir(url))
            self.out_var.set(suggested)
            self._check_and_apply_checkpoint(suggested)

    def _browse(self):
        path = filedialog.askdirectory(title="Chon thu muc luu")
        if path:
            self.out_var.set(path)
            self._check_and_apply_checkpoint(path)

    def _append_log(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.resume_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def _read_inputs(self):
        url = self.url_var.get().strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("URL khong hop le (can bat dau bang http:// hoac https://).")
        try:
            start = int(self.from_var.get().strip() or "1")
            end_text = self.to_var.get().strip()
            end = int(end_text) if end_text else None
        except ValueError:
            raise ValueError("'Tu chuong' va 'Den chuong' phai la so nguyen.")
        if start < 1:
            raise ValueError("'Tu chuong' phai >= 1.")
        if end is not None and end < start:
            raise ValueError("'Den chuong' phai >= 'Tu chuong'.")
        try:
            delay = float(self.delay_var.get().strip())
        except ValueError:
            raise ValueError("Delay phai la so.")
        if delay < 0:
            raise ValueError("Delay phai >= 0.")
        out_dir = self.out_var.get().strip() or os.path.abspath(_default_out_dir(url))
        return url, start, end, delay, out_dir

    # ---- actions ----
    def _start(self):
        try:
            url, start, end, delay, out_dir = self._read_inputs()
        except ValueError as exc:
            messagebox.showerror("Du lieu khong hop le", str(exc))
            return
        self.out_var.set(out_dir)
        self.stop_flag.clear()
        self.progress.configure(value=0, maximum=100, mode="indeterminate")
        self.progress.start(15)
        self.status_var.set("Dang chay...")
        self._set_running(True)

        ignore_robots = self.ignore_robots_var.get()

        def progress(done, total, message):
            self.events.put(("progress", (done, total, message)))

        def run():
            try:
                saved = crawl(
                    start_url=url,
                    out_dir=out_dir,
                    delay=delay,
                    start_chapter=start,
                    end_chapter=end,
                    ignore_robots=ignore_robots,
                    progress=progress,
                    should_stop=self.stop_flag.is_set,
                )
                self.events.put(("done", saved))
            except Exception as exc:  # surface any failure in the UI instead of a silent thread death
                self.events.put(("error", str(exc)))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_flag.set()
        self.status_var.set("Dang dung sau chuong hien tai...")
        self.stop_btn.configure(state="disabled")

    def _finish(self, status: str):
        self.progress.stop()
        self._set_running(False)
        self.status_var.set(status)

    def _poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self._append_log(data)
                elif kind == "progress":
                    done, total, message = data
                    if total:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", maximum=total, value=done)
                    self.status_var.set(message)
                elif kind == "done":
                    self._finish(f"Hoan tat. Da luu {data} chuong vao: {self.out_var.get()}")
                elif kind == "error":
                    self._finish("Loi.")
                    messagebox.showerror("Loi", data)
        except queue.Empty:
            pass
        self.after(100, self._poll)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
