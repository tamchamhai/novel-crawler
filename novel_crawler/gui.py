import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

from .cli import _default_out_dir
from .downloader import crawl, get_highest_chapter, load_checkpoint
from .tts import (
    PipelineTTSWorker,
    convert_chapters,
    engine_labels,
    last_converted_chapter,
    list_chapter_files,
    scan_pending,
    voice_options,
)

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
        self.geometry("760x720")
        self.minsize(650, 580)

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

        # Pipeline Auto-clean and TTS options
        self.auto_clean_var = tk.BooleanVar(value=True)
        self.auto_tts_var = tk.BooleanVar(value=False)
        self.auto_tts_batch_var = tk.StringVar(value="20")
        self._pipe_engines = [
            ("vixtts", "viXTTS (XTTS tieng Viet, dung GPU RTX 5060)"),
            ("edge", "edge-tts (online, giong Microsoft)"),
        ]
        self.auto_tts_engine_var = tk.StringVar(value=self._pipe_engines[0][1])
        self.auto_tts_voice_var = tk.StringVar()

        self._engines = engine_labels()
        self.tts_method_var = tk.StringVar(value=self._engines[0][1])
        self.tts_group_var = tk.StringVar(value="10")
        self.tts_ref_var = tk.StringVar()
        self.tts_voice_var = tk.StringVar()
        self.tts_next_var = tk.StringVar()
        self.tts_dir_var = tk.StringVar()
        self.tts_out_var = tk.StringVar()

        self._build()
        handler = _QueueHandler(self.events)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        self.after(100, self._poll)

    # ---- layout ----
    def _build(self):
        pad = {"padx": 8, "pady": 4}
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=0, column=0, sticky="ew")
        crawl_tab = ttk.Frame(self.notebook, padding=8)
        tts_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(crawl_tab, text="Crawl chuong")
        self.notebook.add(tts_tab, text="Text to Speech")
        self._build_crawl_tab(crawl_tab, pad)
        self._build_tts_tab(tts_tab, pad)

        # shared status area
        bar = ttk.Frame(root)
        bar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        bar.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.stop_btn = ttk.Button(bar, text="Dung", command=self._stop, state="disabled")
        self.stop_btn.grid(row=0, column=1)
        ttk.Label(root, textvariable=self.status_var).grid(row=2, column=0, sticky="w", pady=4)

        log_frame = ttk.Frame(root)
        log_frame.grid(row=3, column=0, sticky="nsew")
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_crawl_tab(self, frm, pad):
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

        # Pipeline Auto-clean and TTS options
        pipe_box = ttk.LabelFrame(frm, text="Che do tu dong (Auto-Clean & Pipeline TTS)", padding=8)
        pipe_box.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(6, 6))
        pipe_box.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            pipe_box,
            text="Tu dong lam sach van ban (loc rac, bo trung tieu de truoc khi luu/TTS)",
            variable=self.auto_clean_var,
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=4, pady=3)

        ttk.Checkbutton(
            pipe_box,
            text="Vua crawl vua tao Audio TTS (tich luy du batch thi xuat file MP3)",
            variable=self.auto_tts_var,
            command=self._toggle_pipeline_tts,
        ).grid(row=1, column=0, columnspan=4, sticky="w", padx=4, pady=3)

        self.pipe_batch_lbl = ttk.Label(pipe_box, text="Batch chuong / 1 file MP3:")
        self.pipe_batch_lbl.grid(row=2, column=0, sticky="w", padx=4, pady=2)
        self.pipe_batch_spin = ttk.Spinbox(pipe_box, from_=1, to=1000, textvariable=self.auto_tts_batch_var, width=8)
        self.pipe_batch_spin.grid(row=2, column=1, sticky="w", padx=4, pady=2)

        self.pipe_eng_lbl = ttk.Label(pipe_box, text="Engine TTS:")
        self.pipe_eng_lbl.grid(row=3, column=0, sticky="w", padx=4, pady=2)
        self.pipe_eng_combo = ttk.Combobox(
            pipe_box, textvariable=self.auto_tts_engine_var, state="readonly", values=[lbl for _k, lbl in self._pipe_engines]
        )
        self.pipe_eng_combo.grid(row=3, column=1, columnspan=3, sticky="ew", padx=4, pady=2)
        self.pipe_eng_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_pipe_engine_changed())

        self.pipe_voice_lbl = ttk.Label(pipe_box, text="Giong doc:")
        self.pipe_voice_lbl.grid(row=4, column=0, sticky="w", padx=4, pady=2)
        self.pipe_voice_combo = ttk.Combobox(pipe_box, textvariable=self.auto_tts_voice_var, state="readonly")
        self.pipe_voice_combo.grid(row=4, column=1, columnspan=3, sticky="ew", padx=4, pady=2)

        self._on_pipe_engine_changed()
        self._toggle_pipeline_tts()

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=4, sticky="w", **pad)
        self.start_btn = ttk.Button(btns, text="Bat dau", command=self._start)
        self.start_btn.pack(side="left", padx=(0, 8))
        self.resume_btn = ttk.Button(btns, text="Tiep tuc (Resume)", command=self._resume)
        self.resume_btn.pack(side="left")

    def _build_tts_tab(self, frm, pad):
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Thu muc chua chuong (.txt):").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.tts_dir_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Chon...", command=self._browse_tts_dir).grid(row=0, column=2, **pad)

        ttk.Label(frm, text="Thu muc luu audio:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.tts_out_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Chon...", command=self._browse_tts_out).grid(row=1, column=2, **pad)

        ttk.Label(frm, text="Phuong phap:").grid(row=2, column=0, sticky="w", **pad)
        self.tts_combo = ttk.Combobox(
            frm, textvariable=self.tts_method_var, state="readonly", values=[lbl for _k, lbl in self._engines]
        )
        self.tts_combo.grid(row=2, column=1, columnspan=2, sticky="ew", **pad)

        ttk.Label(frm, text="So chuong / 1 file mp3:").grid(row=3, column=0, sticky="w", **pad)
        ttk.Spinbox(frm, from_=1, to=1000, textvariable=self.tts_group_var, width=8).grid(
            row=3, column=1, sticky="w", **pad
        )

        # voice selector (edge-tts voices / viXTTS sample voices); hidden for engines without a choice
        self.voice_widgets = [
            ttk.Label(frm, text="Giong doc:"),
            ttk.Combobox(frm, textvariable=self.tts_voice_var, state="readonly"),
        ]
        self.voice_combo = self.voice_widgets[1]
        self.voice_widgets[0].grid(row=4, column=0, sticky="w", **pad)
        self.voice_combo.grid(row=4, column=1, columnspan=2, sticky="ew", **pad)

        self.ref_widgets = [
            ttk.Label(frm, text="Hoac file giong mau .wav rieng:"),
            ttk.Entry(frm, textvariable=self.tts_ref_var),
            ttk.Button(frm, text="Chon...", command=self._browse_ref),
        ]
        self.ref_widgets[0].grid(row=5, column=0, sticky="w", **pad)
        self.ref_widgets[1].grid(row=5, column=1, sticky="ew", **pad)
        self.ref_widgets[2].grid(row=5, column=2, **pad)
        self.tts_combo.bind("<<ComboboxSelected>>", lambda _e: self._toggle_ref())
        self._toggle_ref()

        self.tts_btn = ttk.Button(frm, text="Chuyen sang audio (tu dong tiep tuc)", command=self._start_tts)
        self.tts_btn.grid(row=6, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(frm, textvariable=self.tts_next_var, foreground="#1a5fb4", wraplength=620, justify="left").grid(
            row=7, column=0, columnspan=3, sticky="w", **pad
        )
        ttk.Label(
            frm,
            text="Tu dong bo qua cac chuong da chuyen (theo ten file AAAA-BBBB_*.mp3 trong thu muc audio).",
            foreground="gray",
        ).grid(row=8, column=0, columnspan=3, sticky="w", **pad)
        for entry_var in (self.tts_dir_var, self.tts_out_var):  # re-scan whenever a path changes
            entry_var.trace_add("write", lambda *_a: self._show_tts_progress())

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

    def _current_pipe_engine_key(self) -> str:
        label = self.auto_tts_engine_var.get()
        return next((k for k, lbl in self._pipe_engines if lbl == label), "vixtts")

    def _on_pipe_engine_changed(self):
        key = self._current_pipe_engine_key()
        voices = voice_options(key)
        labels = [lbl for _v, lbl in voices]
        self.pipe_voice_combo.configure(values=labels)
        if labels:
            self.auto_tts_voice_var.set(labels[0])

    def _toggle_pipeline_tts(self):
        enabled = self.auto_tts_var.get()
        state = "normal" if enabled else "disabled"
        self.pipe_batch_spin.configure(state=state)
        self.pipe_eng_combo.configure(state="readonly" if enabled else "disabled")
        self.pipe_voice_combo.configure(state="readonly" if enabled else "disabled")

    def _current_engine_key(self) -> str:
        return next((k for k, lbl in self._engines if lbl == self.tts_method_var.get()), "")

    def _toggle_ref(self):
        key = self._current_engine_key()
        voices = voice_options(key)
        self._voice_values = [v for v, _l in voices]
        self.voice_combo.configure(values=[lbl for _v, lbl in voices])
        if voices:
            self.tts_voice_var.set(voices[0][1])  # default = first entry of the engine's list
        for w in self.voice_widgets:
            w.grid() if voices else w.grid_remove()
        for w in self.ref_widgets:  # custom .wav only makes sense for the cloning engine
            w.grid() if key == "vixtts" else w.grid_remove()

    def _browse_tts_dir(self):
        path = filedialog.askdirectory(title="Chon thu muc chua chuong")
        if path:
            self.tts_dir_var.set(path)
            if not self.tts_out_var.get().strip():
                self.tts_out_var.set(os.path.join(path, "audio"))
            self._show_tts_progress()

    def _browse_tts_out(self):
        path = filedialog.askdirectory(title="Chon thu muc luu audio")
        if path:
            self.tts_out_var.set(path)
            self._show_tts_progress()

    def _scan_text(self, src_dir: str, out_dir: str) -> tuple[str, int | None]:
        last, nxt, pending = scan_pending(src_dir, out_dir)
        if not os.path.isdir(src_dir):
            return "Chua chon thu muc chua chuong.", None
        if nxt is None:
            return (f"Da chuyen toi chuong {last}. Khong con chuong moi." if last else "Khong co file chuong .txt."), None
        if last:
            return f"Da chuyen toi chuong {last} -> se bat dau tu chuong {nxt} ({pending} chuong cho).", nxt
        return f"Chua co audio nao -> se bat dau tu chuong dau tien (chuong {nxt}, {pending} chuong cho).", nxt

    def _show_tts_progress(self):
        src = self.tts_dir_var.get().strip() or self.out_var.get().strip()
        out = self.tts_out_var.get().strip() or (os.path.join(src, "audio") if src else "")
        text, _nxt = self._scan_text(src, out)
        self.tts_next_var.set(text)

    def _browse_ref(self):
        path = filedialog.askopenfilename(title="Chon giong mau", filetypes=[("WAV", "*.wav")])
        if path:
            self.tts_ref_var.set(path)

    def _append_log(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.resume_btn.configure(state="disabled" if running else "normal")
        self.tts_btn.configure(state="disabled" if running else "normal")
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

        clean = self.auto_clean_var.get()
        auto_tts = self.auto_tts_var.get()
        batch_size = 20
        if auto_tts:
            try:
                batch_size = int(self.auto_tts_batch_var.get().strip())
                if batch_size < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Du lieu khong hop le", "Batch chuong / 1 file mp3 phai la so nguyen >= 1.")
                return

        pipe_worker = None
        if auto_tts:
            engine_key = self._current_pipe_engine_key()
            voice_label = self.auto_tts_voice_var.get()
            voices = voice_options(engine_key)
            voice_val = next((v for v, lbl in voices if lbl == voice_label), "")
            audio_dir = os.path.join(out_dir, "audio")
            pipe_worker = PipelineTTSWorker(
                engine_key=engine_key,
                out_dir=audio_dir,
                batch_size=batch_size,
                voice=voice_val,
                progress=lambda d, t, m: self.events.put(("pipe_progress", (d, t, m))),
                log=lambda m: self.events.put(("log", m)),
                should_stop=self.stop_flag.is_set,
            )

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
                if pipe_worker:
                    existing = list_chapter_files(out_dir) if os.path.isdir(out_dir) else []
                    last_done = last_converted_chapter(pipe_worker.out_dir)
                    unconverted = [c for c in existing if c[0] > last_done]
                    pipe_worker.start(initial_chapters=unconverted)

                on_saved = pipe_worker.add_chapter if pipe_worker else None

                saved = crawl(
                    start_url=url,
                    out_dir=out_dir,
                    delay=delay,
                    start_chapter=start,
                    end_chapter=end,
                    ignore_robots=ignore_robots,
                    progress=progress,
                    should_stop=self.stop_flag.is_set,
                    clean=clean,
                    on_chapter_saved=on_saved,
                )

                if pipe_worker:
                    pipe_worker.finish_crawler()
                    self.events.put(("log", "[TTS Pipeline] Crawler da xong, dang doi TTS hoan tat cac chuong con lai..."))
                    pipe_worker.join()
                    audio_count = len(pipe_worker.created_files)
                    self.events.put(("done", (saved, audio_count, pipe_worker.out_dir)))
                else:
                    self.events.put(("done", (saved, 0, out_dir)))

            except Exception as exc:  # surface any failure in the UI instead of a silent thread death
                if pipe_worker:
                    pipe_worker.stop()
                self.events.put(("error", str(exc)))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _start_tts(self):
        src_dir = self.tts_dir_var.get().strip() or self.out_var.get().strip()
        if not src_dir or not os.path.isdir(src_dir):
            messagebox.showerror(
                "Du lieu khong hop le",
                "Thu muc chua chuong khong ton tai. Hay crawl truoc hoac chon thu muc chua file chuong .txt.",
            )
            return
        audio_dir = self.tts_out_var.get().strip() or os.path.join(src_dir, "audio")
        self.tts_dir_var.set(src_dir)
        self.tts_out_var.set(audio_dir)

        # scan the output folder first: where do we continue?
        text, nxt = self._scan_text(src_dir, audio_dir)
        self.tts_next_var.set(text)
        self._append_log(f"[Quet audio] {text}")
        if nxt is None:
            self.status_var.set(text)
            messagebox.showinfo("Khong co chuong moi", text)
            return
        try:
            group = int(self.tts_group_var.get().strip())
            if group < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Du lieu khong hop le", "'So chuong / 1 file mp3' phai la so nguyen >= 1.")
            return
        label = self.tts_method_var.get()
        key = next(k for k, lbl in self._engines if lbl == label)
        ref = self.tts_ref_var.get().strip() if key == "vixtts" else ""
        voice = ""
        labels = [lbl for _v, lbl in voice_options(key)]
        if self.tts_voice_var.get() in labels:
            voice = voice_options(key)[labels.index(self.tts_voice_var.get())][0]

        self.stop_flag.clear()
        self.progress.configure(value=0, maximum=100, mode="indeterminate")
        self.progress.start(15)
        self.status_var.set(f"Dang chuyen sang audio, bat dau tu chuong {nxt}...")
        self._set_running(True)

        def progress(done, total, message):
            self.events.put(("progress", (done, total, message)))

        def run():
            try:
                outs = convert_chapters(
                    key, src_dir, audio_dir, group, reference_wav=ref, voice=voice,
                    progress=progress, should_stop=self.stop_flag.is_set,
                )
                self.events.put(("tts_done", (len(outs), audio_dir)))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_flag.set()
        self.status_var.set("Dang dung sau chuong hien tai...")
        self.stop_btn.configure(state="disabled")

    def _finish(self, status: str, ok: bool = True):
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=100 if ok else 0)
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
                    saved, audio_count, target_dir = data
                    self.tts_dir_var.set(self.out_var.get())
                    self.tts_out_var.set(os.path.join(self.out_var.get(), "audio"))
                    if audio_count:
                        self._finish(f"Hoan tat. Da luu {saved} chuong va tao {audio_count} file MP3 trong: {target_dir}")
                    else:
                        self._finish(f"Hoan tat. Da luu {saved} chuong vao: {self.out_var.get()}")
                elif kind == "pipe_progress":
                    done, total, message = data
                    self.status_var.set(f"[TTS Pipeline] {message}")
                elif kind == "tts_done":
                    self._show_tts_progress()
                    if data[0]:
                        self._finish(f"Hoan tat TTS. Da tao {data[0]} file audio trong: {data[1]}")
                    else:
                        self._finish("Khong co chuong moi can chuyen (hoac da dung truoc khi xong file dau tien).")
                elif kind == "error":
                    self._append_log(f"[LOI] {data}")
                    self._finish(f"Loi: {data}", ok=False)
                    messagebox.showerror("Loi", data)
        except queue.Empty:
            pass
        self.after(100, self._poll)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
