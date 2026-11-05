"""Text-to-speech for crawled chapters (free / offline engines).

Every engine renders text to a WAV file. Chapters are chunked, rendered, concatenated
in groups of N chapters and encoded to one MP3 per group (needs ``lameenc``; falls back
to WAV when it is not installed).
"""
import logging
import os
import re
import tempfile
import wave

LOG = logging.getLogger("novel_crawler")

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "novel_crawler_tts")
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/vi/vi_VN/vais1000/medium/"
PIPER_MODEL = "vi_VN-vais1000-medium"
MMS_MODEL = "facebook/mms-tts-vie"
VIXTTS_REPO = "capleaf/viXTTS"
PAUSE_SECONDS = 0.6  # silence inserted between chapters


class TTSUnavailable(RuntimeError):
    """Raised when an engine's dependencies / models are missing."""


class Cancelled(Exception):
    pass


class TTSFatal(RuntimeError):
    """An engine gave up on a chunk; stop instead of silently dropping text."""


# --------------------------------------------------------------------------- helpers
def split_text(text: str, max_chars: int, merge: bool = False) -> list[str]:
    """Split text into chunks <= max_chars, preferring sentence / line boundaries."""
    chunks: list[str] = []
    for para in re.split(r"\n+", text):
        para = para.strip()
        if not para:
            continue
        sentences = re.split(r"(?<=[.!?…。？！])\s+", para)
        buf = ""
        for s in sentences:
            while len(s) > max_chars:  # very long sentence: cut at a space
                cut = s.rfind(" ", 0, max_chars)
                cut = cut if cut > 0 else max_chars
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.append(s[:cut].strip())
                s = s[cut:].strip()
            if buf and len(buf) + 1 + len(s) > max_chars:
                chunks.append(buf)
                buf = s
            else:
                buf = f"{buf} {s}".strip()
        if buf:
            chunks.append(buf)
    # drop chunks with nothing to read (e.g. '***', '...') - engines emit no audio for them
    chunks = [c for c in chunks if re.search(r"\w", c)]
    if not merge:
        return chunks
    merged: list[str] = []  # pack neighbouring paragraphs into as few requests as possible
    for c in chunks:
        if merged and len(merged[-1]) + 1 + len(c) <= max_chars:
            merged[-1] += " " + c
        else:
            merged.append(c)
    return merged


def _write_wav(path: str, samples_int16: bytes, rate: int, channels: int = 1) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples_int16)


def _float_to_pcm16(wav) -> bytes:
    import numpy as np

    arr = np.asarray(wav, dtype="float32").reshape(-1)
    return (np.clip(arr, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def _download(url: str, dest: str) -> None:
    import requests

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    LOG.info("Dang tai %s", url)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            for block in r.iter_content(1 << 20):
                f.write(block)
    os.replace(tmp, dest)


# --------------------------------------------------------------------------- engines
class Engine:
    key = ""
    label = ""
    max_chars = 400
    install_hint = ""
    merge_chunks = False  # True: pack paragraphs together up to max_chars (fewer requests)

    def stop_check(self) -> bool:  # replaced by convert_chapters so long waits can be cancelled
        return False

    def sleep(self, seconds: float) -> None:
        import time

        end = time.time() + seconds
        while time.time() < end:
            if self.stop_check():
                raise Cancelled
            time.sleep(min(0.5, max(0.0, end - time.time())))

    def load(self) -> None:  # import deps + load models; raise TTSUnavailable
        raise NotImplementedError

    def synth(self, text: str, wav_path: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class Pyttsx3Engine(Engine):
    key = "pyttsx3"
    label = "pyttsx3 (giong Windows SAPI5)"
    install_hint = "pip install pyttsx3"
    max_chars = 1000

    def load(self):
        try:
            import pyttsx3
        except ImportError as exc:
            raise TTSUnavailable(f"Thieu pyttsx3. Chay: {self.install_hint}") from exc
        self.engine = pyttsx3.init()
        chosen = None
        for v in self.engine.getProperty("voices"):
            blob = f"{v.id} {v.name} {getattr(v, 'languages', '')}".lower()
            if "vi-vn" in blob or "vietnam" in blob or "an (" in blob or "vi_vn" in blob:
                chosen = v.id
                break
        if chosen:
            self.engine.setProperty("voice", chosen)
            LOG.info("pyttsx3: dung voice %s", chosen)
        else:
            LOG.warning("pyttsx3: KHONG tim thay voice tieng Viet. Cai Vietnamese trong Windows Settings > Speech.")

    def synth(self, text, wav_path):
        self.engine.save_to_file(text, wav_path)
        self.engine.runAndWait()

    def close(self):
        try:
            self.engine.stop()
        except Exception:
            pass


class PiperEngine(Engine):
    key = "piper"
    label = "Piper TTS (offline, vi_VN-vais1000)"
    install_hint = "pip install piper-tts"
    max_chars = 500

    def load(self):
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise TTSUnavailable(f"Thieu piper. Chay: {self.install_hint}") from exc
        model_dir = os.path.join(CACHE_DIR, "piper")
        onnx = os.path.join(model_dir, PIPER_MODEL + ".onnx")
        cfg = onnx + ".json"
        for path in (onnx, cfg):
            if not os.path.isfile(path):
                _download(PIPER_BASE + os.path.basename(path), path)
        self.voice = PiperVoice.load(onnx, config_path=cfg)

    def synth(self, text, wav_path):
        with wave.open(wav_path, "wb") as wf:
            # preset the format so an empty synthesis still closes cleanly
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(self.voice.config.sample_rate))
            if hasattr(self.voice, "synthesize_wav"):  # piper-tts >= 1.3
                self.voice.synthesize_wav(text, wf)
            else:  # older API
                self.voice.synthesize(text, wf)


class VitsEngine(Engine):
    key = "vits"
    label = "Coqui/VITS (model VITS tieng Viet, MMS-TTS)"
    install_hint = "pip install torch transformers numpy"
    max_chars = 300

    def load(self):
        try:
            import torch
            from transformers import AutoTokenizer, VitsModel
        except ImportError as exc:
            raise TTSUnavailable(f"Thieu thu vien. Chay: {self.install_hint}") from exc
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(MMS_MODEL)
        self.model = VitsModel.from_pretrained(MMS_MODEL)
        self.model.eval()
        self.rate = int(self.model.config.sampling_rate)

    def synth(self, text, wav_path):
        inputs = self.tok(text.lower(), return_tensors="pt")
        with self.torch.no_grad():
            out = self.model(**inputs).waveform
        _write_wav(wav_path, _float_to_pcm16(out.cpu().numpy()), self.rate)


class ViXttsEngine(Engine):
    key = "vixtts"
    label = "viXTTS (XTTS tieng Viet, nen co GPU)"
    install_hint = "pip install coqui-tts huggingface_hub torch"
    max_chars = 220

    def __init__(self, reference_wav: str = "", voice: str = ""):
        self.reference_wav = reference_wav  # custom .wav path, wins over the preset voice
        self.voice = voice  # sample path inside the model repo, e.g. "samples/nu-calm.wav"

    def load(self):
        try:
            import torch
            from huggingface_hub import snapshot_download
            from TTS.tts.configs.xtts_config import XttsConfig
            from TTS.tts.models.xtts import Xtts
        except ImportError as exc:
            raise TTSUnavailable(f"Thieu thu vien. Chay: {self.install_hint}") from exc
        model_dir = snapshot_download(VIXTTS_REPO, local_dir=os.path.join(CACHE_DIR, "vixtts"))
        ref = self.reference_wav
        if not ref and self.voice and os.path.isfile(os.path.join(model_dir, self.voice)):
            ref = os.path.join(model_dir, self.voice)
        if not ref and os.path.isfile(os.path.join(model_dir, "vi_sample.wav")):
            ref = os.path.join(model_dir, "vi_sample.wav")
        if not ref:
            for root, _dirs, files in os.walk(model_dir):
                wavs = [f for f in files if f.lower().endswith(".wav")]
                if wavs:
                    ref = os.path.join(root, wavs[0])
                    break
        if not ref or not os.path.isfile(ref):
            raise TTSUnavailable("viXTTS can 1 file giong mau .wav. Hay nhap duong dan 'Giong mau'.")
        self._patch_audio_loader()
        self._patch_vietnamese()
        config = XttsConfig()
        config.load_json(os.path.join(model_dir, "config.json"))
        self.model = Xtts.init_from_config(config)
        self.model.load_checkpoint(config, checkpoint_dir=model_dir, use_deepspeed=False)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        LOG.info("viXTTS: thiet bi=%s, giong mau=%s", self.device, ref)
        self.latent, self.speaker = self.model.get_conditioning_latents(audio_path=[ref])

    @staticmethod
    def _patch_vietnamese() -> None:
        """The stock coqui-tts tokenizer rejects lang='vi' (viXTTS ships a patched fork). The model and its
        vocab already know Vietnamese, only the text pre-processing branch is missing: add it here."""
        import unicodedata

        from TTS.tts.layers.xtts import tokenizer as tk

        if getattr(tk.VoiceBpeTokenizer, "_vi_patched", False):
            return
        original = tk.VoiceBpeTokenizer.preprocess_text

        def _numbers_to_words(text: str) -> str:
            try:
                from num2words import num2words
            except ImportError:
                return text
            return re.sub(r"\d+", lambda m: num2words(int(m.group(0)), lang="vi"), text)

        def preprocess_text(self, txt, lang):
            if lang != "vi":
                return original(self, txt, lang)
            txt = unicodedata.normalize("NFC", txt).lower()
            txt = _numbers_to_words(txt.replace("%", " phần trăm"))
            txt = txt.replace("…", ".")
            txt = re.sub(r"[“”\"'‘’()\[\]*_~^<>/\\|#@=+]", " ", txt)
            return re.sub(r"\s+", " ", txt).strip()

        tk.VoiceBpeTokenizer.preprocess_text = preprocess_text
        tk.VoiceBpeTokenizer._vi_patched = True

    @staticmethod
    def _patch_audio_loader() -> None:
        """XTTS reads the voice sample with torchaudio.load, which now needs torchcodec + FFmpeg DLLs
        (not installed on Windows by default). Read the wav with soundfile instead."""
        import soundfile as sf
        import torch
        import torchaudio
        import TTS.tts.models.xtts as xtts_mod

        def load_audio(audiopath, sampling_rate):
            data, rate = sf.read(audiopath, dtype="float32", always_2d=True)  # (samples, channels)
            audio = torch.from_numpy(data.T.copy())
            if audio.size(0) != 1:
                audio = audio.mean(dim=0, keepdim=True)
            if rate != sampling_rate:
                audio = torchaudio.functional.resample(audio, rate, sampling_rate)
            return audio.clamp_(-1, 1)

        xtts_mod.load_audio = load_audio

    def synth(self, text, wav_path):
        out = self.model.inference(text, "vi", self.latent, self.speaker, temperature=0.3)
        _write_wav(wav_path, _float_to_pcm16(out["wav"]), 24000)


class EdgeEngine(Engine):
    """Microsoft Edge neural voices via the free edge-tts service (needs internet)."""

    key = "edge"
    label = "edge-tts (online, giong Microsoft vi-VN-HoaiMyNeural)"
    install_hint = "pip install edge-tts soundfile"
    max_chars = 400  # small requests fail less often than long ones
    merge_chunks = True
    voice = "vi-VN-HoaiMyNeural"
    min_interval = 1.0  # seconds between requests
    backoff = (1, 1, 2, 2, 3)  # short waits: the failures depend on the payload, not on the time

    def __init__(self, voice: str = ""):
        if voice:
            self.voice = voice
        self._last_call = 0.0

    def load(self):
        try:
            import edge_tts
            import soundfile
        except ImportError as exc:
            raise TTSUnavailable(f"Thieu thu vien. Chay: {self.install_hint}") from exc
        self.edge_tts = edge_tts
        self.sf = soundfile

    def _request(self, text: str, mp3_path: str) -> None:
        import asyncio
        import time

        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            self.sleep(wait)
        try:
            # explicit prosody values: with the defaults the service rejects far more requests
            comm = self.edge_tts.Communicate(text, self.voice, rate="+0%", volume="+0%", pitch="+0Hz")
            asyncio.run(comm.save(mp3_path))
        finally:
            self._last_call = time.time()

    # payload variants that do not change what is spoken. The service refuses some exact strings at random
    # (~60% of tries for the worst ones) but almost any re-formatting of the same text gets through.
    VARIANTS = ("{}", "\n{}", "{}\n", " {} ", "\n{}\n", " {}", "{} ", "\n\n{}")

    def _attempt(self, text: str, wav_path: str, tries: int) -> bool:
        """Try one piece of text up to ``tries`` times, re-formatting the payload on every retry."""
        mp3_path = wav_path + ".mp3"
        for attempt in range(tries):
            try:
                self._request(self.VARIANTS[attempt % len(self.VARIANTS)].format(text), mp3_path)
            except Cancelled:
                raise
            except Exception as exc:
                if attempt < tries - 1:
                    wait = self.backoff[min(attempt, len(self.backoff) - 1)]
                    LOG.info("edge-tts bi tu choi (%s), thu lai bang dinh dang khac (lan %s/%s)", type(exc).__name__,
                             attempt + 1, tries - 1)
                    self.sleep(wait)
                continue
            try:
                data, rate = self.sf.read(mp3_path, dtype="int16")
                _write_wav(wav_path, data.tobytes(), rate, 1 if data.ndim == 1 else data.shape[1])
                return True
            finally:
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
        return False

    def _render(self, text: str, wav_path: str, level: int = 0) -> None:
        """Whole text first; if the service keeps refusing, split into sentences, then clauses.
        No text is ever skipped - if even a clause fails we stop with TTSFatal."""
        tries = 3  # per level: ~40% of tries succeed even for the worst strings, so 3 tries x 3 levels is plenty
        if self._attempt(text, wav_path, tries):
            return
        splitters = (r"(?<=[.!?…])\s+", r"(?<=[,;:])\s+")
        if level < len(splitters):
            parts = [x for x in re.split(splitters[level], text) if x.strip()]
            if len(parts) > 1:
                LOG.info("edge-tts: tach doan thanh %s %s de thu lai", len(parts), "cau" if level == 0 else "ve")
                wavs = []
                for n, part in enumerate(parts):
                    pw = f"{wav_path}.part{level}_{n}.wav"
                    self._render(part, pw, level + 1)
                    wavs.append(pw)
                try:
                    _concat_wavs(wavs, wav_path)
                finally:
                    for w in wavs:
                        if os.path.exists(w):
                            os.remove(w)
                return
            return self._render(text, wav_path, level + 1)  # nothing to split at this level
        # the service refuses this exact piece whatever we do: let Piper read it so no text is lost
        if self._fallback_piper(text, wav_path):
            return
        raise TTSFatal(
            f"edge-tts tu choi lien tuc doan: {text[:60]!r}... va khong co Piper de doc thay (pip install piper-tts)."
        )

    EDGE_RATE = 24000  # sample rate of edge-tts audio; fallback audio is resampled to match

    def _fallback_piper(self, text: str, wav_path: str) -> bool:
        import wave

        try:
            if not hasattr(self, "_piper"):
                self._piper = PiperEngine()
                self._piper.load()
            self._piper.synth(text, wav_path + ".piper.wav")
            import numpy as np

            with wave.open(wav_path + ".piper.wav", "rb") as w:
                src_rate = w.getframerate()
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            n_out = int(len(pcm) * self.EDGE_RATE / src_rate)
            out = np.interp(np.linspace(0, len(pcm) - 1, n_out), np.arange(len(pcm)), pcm).astype("<i2")
            _write_wav(wav_path, out.tobytes(), self.EDGE_RATE)
            LOG.warning("edge-tts tu choi doan nay, da dung Piper doc thay: %.70r", text)
            return True
        except Exception as exc:
            LOG.warning("Khong dung duoc Piper de du phong (%s)", exc)
            return False
        finally:
            if os.path.exists(wav_path + ".piper.wav"):
                os.remove(wav_path + ".piper.wav")

    def synth(self, text, wav_path):
        text = re.sub(r"(^|\s)-\s+", r"\1", text).strip()  # a dialogue dash is not read, drop it (keep the space)
        self._render(text, wav_path)


ENGINES = {
    cls.key: cls for cls in (Pyttsx3Engine, PiperEngine, VitsEngine, ViXttsEngine, EdgeEngine)
}


def engine_labels() -> list[tuple[str, str]]:
    return [(k, c.label) for k, c in ENGINES.items()]


# selectable voices per engine: (value, label). First entry is the default.
VOICE_OPTIONS = {
    "edge": [
        ("vi-VN-HoaiMyNeural", "Hoai My (nu)"),
        ("vi-VN-NamMinhNeural", "Nam Minh (nam)"),
    ],
    "vixtts": [
        ("vi_sample.wav", "Mac dinh (vi_sample)"),
        ("samples/nam-calm.wav", "Nam - diem tinh"),
        ("samples/nam-cham.wav", "Nam - cham"),
        ("samples/nam-nhanh.wav", "Nam - nhanh"),
        ("samples/nam-truyen-cam.wav", "Nam - truyen cam"),
        ("samples/nu-calm.wav", "Nu - diem tinh"),
        ("samples/nu-cham.wav", "Nu - cham"),
        ("samples/nu-luu-loat.wav", "Nu - luu loat"),
        ("samples/nu-nhan-nha.wav", "Nu - nhan nha"),
        ("samples/nu-nhe-nhang.wav", "Nu - nhe nhang"),
    ],
}


def voice_options(key: str) -> list[tuple[str, str]]:
    return VOICE_OPTIONS.get(key, [])


def create_engine(key: str, reference_wav: str = "", voice: str = "") -> Engine:
    cls = ENGINES[key]
    if cls is ViXttsEngine:
        return cls(reference_wav, voice)
    if cls is EdgeEngine:
        return cls(voice)
    return cls()


# --------------------------------------------------------------------------- audio assembly
def _concat_wavs(parts: list[str], out_wav: str, pause: float = 0.0) -> None:
    params = None
    with wave.open(out_wav, "wb") as out:
        for p in parts:
            with wave.open(p, "rb") as w:
                if params is None:
                    params = w.getparams()
                    out.setparams(params)
                elif (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (
                    params.framerate, params.nchannels, params.sampwidth
                ):
                    raise RuntimeError("WAV chunk co dinh dang khac nhau, khong the ghep.")
                out.writeframes(w.readframes(w.getnframes()))
        if params and pause:
            silence = b"\x00" * int(params.framerate * pause) * params.nchannels * params.sampwidth
            out.writeframes(silence)


def _wav_to_mp3(wav_path: str, mp3_path: str) -> bool:
    try:
        import lameenc
    except ImportError:
        return False
    with wave.open(wav_path, "rb") as w:
        enc = lameenc.Encoder()
        enc.set_bit_rate(96)
        enc.set_in_sample_rate(w.getframerate())
        enc.set_channels(w.getnchannels())
        enc.set_quality(2)
        with open(mp3_path, "wb") as f:
            while True:
                block = w.readframes(w.getframerate() * 10)
                if not block:
                    break
                f.write(enc.encode(block))
            f.write(enc.flush())
    return True


def list_chapter_files(src_dir: str, start: int = 1, end: int | None = None) -> list[tuple[int, str]]:
    found = []
    for name in sorted(os.listdir(src_dir)):
        if name.endswith(".txt") and name[:4].isdigit():
            idx = int(name[:4])
            if idx >= start and (end is None or idx <= end):
                found.append((idx, os.path.join(src_dir, name)))
    return found


_OUT_RE = re.compile(r"^(\d{4})-(\d{4})_.*\.(mp3|wav)$", re.IGNORECASE)


def last_converted_chapter(out_dir: str) -> int:
    """Highest chapter number already covered by audio files in out_dir (0 if none)."""
    if not os.path.isdir(out_dir):
        return 0
    last = 0
    for name in os.listdir(out_dir):
        m = _OUT_RE.match(name)
        if m:
            last = max(last, int(m.group(2)))
    return last


def scan_pending(src_dir: str, out_dir: str) -> tuple[int, int | None, int]:
    """Scan the output folder and return (last_converted, next_chapter, pending_count).

    Empty / missing output folder -> last_converted 0, so we start from the first chapter.
    next_chapter is the first chapter file after the last converted one (None if nothing is left).
    """
    last_done = last_converted_chapter(out_dir)
    pending = [idx for idx, _p in list_chapter_files(src_dir) if idx > last_done] if os.path.isdir(src_dir) else []
    return last_done, (pending[0] if pending else None), len(pending)


def convert_chapters(
    engine_key: str,
    src_dir: str,
    out_dir: str,
    group_size: int,
    reference_wav: str = "",
    voice: str = "",
    progress=None,
    should_stop=None,
) -> list[str]:
    """Render chapters to audio, ``group_size`` chapters per output file. Returns output paths.

    Chapters already covered by files in ``out_dir`` (named ``AAAA-BBBB_<engine>.mp3``) are
    skipped, so each run continues with the chapters after the last converted one.
    """
    if group_size < 1:
        raise ValueError("So chuong moi file phai >= 1.")
    report = progress or (lambda d, t, m: None)
    stopped = should_stop or (lambda: False)

    all_chapters = list_chapter_files(src_dir)
    if not all_chapters:
        raise FileNotFoundError(f"Khong co file chuong .txt nao trong: {src_dir}")
    last_done = last_converted_chapter(out_dir)
    chapters = [c for c in all_chapters if c[0] > last_done]
    if last_done:
        LOG.info("Da chuyen toi chuong %s. Tiep tuc tu chuong %s.", last_done, last_done + 1)
    if not chapters:
        LOG.info("Khong co chuong moi can chuyen.")
        return []
    os.makedirs(out_dir, exist_ok=True)

    engine = create_engine(engine_key, reference_wav, voice)
    engine.stop_check = stopped
    report(0, 0, f"Dang nap engine {engine.label}...")
    engine.load()

    groups = [chapters[i:i + group_size] for i in range(0, len(chapters), group_size)]
    outputs: list[str] = []
    done_chapters = 0
    last_error = None
    total = len(chapters)
    try:
        with tempfile.TemporaryDirectory(prefix="novel_tts_") as tmp:
            for group in groups:
                if stopped():
                    break
                first, last = group[0][0], group[-1][0]
                chapter_wavs: list[str] = []
                for idx, path in group:
                    if stopped():
                        raise Cancelled
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                    chunks = split_text(text, engine.max_chars, engine.merge_chunks)
                    chunk_wavs = []
                    for n, chunk in enumerate(chunks):
                        if stopped():
                            raise Cancelled
                        wp = os.path.join(tmp, f"{idx:04d}_{n:05d}.wav")
                        try:
                            engine.synth(chunk, wp)
                        except (Cancelled, TTSFatal):
                            raise
                        except Exception as exc:  # one bad chunk must not kill the whole run
                            last_error = exc
                            LOG.warning("Chuong %s doan %s loi (%s), bo qua: %.40r", idx, n + 1, exc, chunk)
                        if os.path.isfile(wp) and os.path.getsize(wp) > 44:
                            chunk_wavs.append(wp)
                        report(done_chapters, total, f"Chuong {idx}: doan {n + 1}/{len(chunks)}")
                    if not chunk_wavs:
                        LOG.warning("Chuong %s khong tao duoc audio, bo qua.", idx)
                        continue
                    cw = os.path.join(tmp, f"{idx:04d}.wav")
                    _concat_wavs(chunk_wavs, cw, pause=PAUSE_SECONDS)
                    for p in chunk_wavs:
                        os.remove(p)
                    chapter_wavs.append(cw)
                    done_chapters += 1
                    report(done_chapters, total, f"Xong chuong {idx}")
                if not chapter_wavs:
                    continue
                base = os.path.join(out_dir, f"{first:04d}-{last:04d}_{engine.key}")
                combined = os.path.join(tmp, f"{first:04d}-{last:04d}.wav")
                _concat_wavs(chapter_wavs, combined)
                if _wav_to_mp3(combined, base + ".mp3"):
                    outputs.append(base + ".mp3")
                else:
                    LOG.warning("Chua cai lameenc (pip install lameenc) -> luu .wav thay vi .mp3.")
                    os.replace(combined, base + ".wav")
                    outputs.append(base + ".wav")
                LOG.info("Da tao %s", outputs[-1])
                for p in chapter_wavs + [combined]:
                    if os.path.exists(p):
                        os.remove(p)
    except Cancelled:
        LOG.info("Da dung TTS theo yeu cau.")
    finally:
        engine.close()
    if not outputs and last_error is not None:
        raise RuntimeError(f"Khong tao duoc audio nao. Loi cuoi: {last_error}")
    return outputs
