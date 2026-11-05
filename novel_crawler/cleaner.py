"""Clean crawled chapter text files for reading / text-to-speech.

- strips site boilerplate (nav links, author / translator / source credits, ads, book-name echoes)
- keeps ONE chapter title line at the top, drops the repeated copies
- removes stray symbols / emoticons / broken characters, normalises quotes and spacing
- adds sentence-ending periods and a few safe Vietnamese commas
- puts every paragraph and every dialogue line ("- ...") on its own line, separated by a blank line

Usage:  python -m novel_crawler.cleaner <folder> [--dry-run] [--no-backup]
"""
import argparse
import difflib
import glob
import os
import re
import shutil
import sys
import time
import unicodedata

# ---------------------------------------------------------------- boilerplate
# whole-line junk (matched after stripping, case-insensitive)
JUNK_LINE_PATTERNS = [
    r"<<\s*Chương trước",
    r"Chương tiếp\s*>>",
    r"-+\s*o\s*-+",
    r"(Tác giả|Nhóm dịch|Nguồn|Nguồn truyện|Nguồn dịch|Biên tập|Dịch giả|Sưu tầm|Đả tự|Edit|Beta)\s*:.*",
    r"Nguồn\s+metruyen",
    r"Thanks!?",
    r"Friendship",
    r"Share by .*",
    r"Nội dung đã hiển thị.*",
    r"Ta Là Chí Tôn, hài, hay.*",
    r".*đọc truyện mới nhất tại.*",
    # promo titles / names that trail chapters in this particular novel
    r"Thiếu gia bị bỏ rơi",
    r"Diệt Hồng Trần",
    r"Duy Linh",
    r"Toái Càn Khôn",
    r"Bạch Sầu",
    r"Trương gia đại thiếu",
]
_JUNK_RE = re.compile(r"^(?:%s)$" % "|".join(JUNK_LINE_PATTERNS), re.IGNORECASE)

# ads glued inside a sentence
INLINE_JUNK = [
    re.compile(r"\s*Truyện được copy từ[^.\n]*?Com\.?", re.IGNORECASE),
    re.compile(r"\[\s*truyen cua tui[^\]]*\]", re.IGNORECASE),
    re.compile(r"\s*(?:Nguồn:\s*|đăng nhập\s*)?https?:\s*//\S*(?:\s*để đọc\s*truyện)?", re.IGNORECASE),
    re.compile(r"\s*Truyện được share bởi[^\n]*?\(\.\)\s*Com", re.IGNORECASE),
    re.compile(r"\s*Nguồn tại\s+https?://\S+(?:\s+Truyện FULL)?", re.IGNORECASE),
    re.compile(r"\s*Truyện được[^\n.]*?(?:\.\s*Com|\(\.\)\s*Com)", re.IGNORECASE),
    re.compile(r"\s*(?:xem tại\s*)?Truyện FULL(?:\s*-\s*www\.?\s*Truyện FULL)?\.?", re.IGNORECASE),
    re.compile(r"\[/?\w*COLOR[^\]]*\]", re.IGNORECASE),
    re.compile(r"\s*(?:truyện copy từ|xem tại|nguồn\s*\(\.\))\s*\.?\s*$", re.IGNORECASE),
    re.compile(r"\S*class=\w+>"),
    re.compile(r"\S*�\S*"),
]

# stray symbols / emoticons
_STRAY_RE = re.compile(r"\^[_\-.]?\^|[\^~_*=¬#`■❤❊‼°‎​‌‍﻿@]+")

TITLE_RE = re.compile(
    r"^(?:(?:Thiếu\s*Gia\s*Bị\s*Bỏ\s*Rơi)\s*)?(Chương\s*(\d+))\s*[:：\-–.]?\s*(.*?)\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------- punctuation
UPPER = "A-ZÀ-ỸĐ"
LOWER = "a-zà-ỹđ"

# phrases that open a sentence and take a comma ("Sau đó, ...")
OPENERS = [
    "Sau đó", "Lúc này", "Lúc đó", "Ngay lúc này", "Ngay lúc đó", "Ngay sau đó", "Bây giờ", "Cuối cùng",
    "Đột nhiên", "Thế nhưng", "Tuy nhiên", "Hơn nữa", "Cho nên", "Vì vậy", "Vì thế", "Do đó", "Tuy vậy",
    "Dù vậy", "Đương nhiên", "Thực ra", "Thật ra", "Nói thật", "Quả nhiên", "Hiển nhiên", "Ngoài ra",
    "Nói cách khác", "Trước đó", "Tiếp theo", "Mặt khác", "Dĩ nhiên", "Tất nhiên", "Một lát sau",
    "Một lúc sau", "Một lúc lâu sau", "Hồi lâu sau", "Chốc lát sau", "Ngày hôm sau", "Hôm sau", "Sáng hôm sau",
    "Vậy mà", "Thế mà", "Ngược lại", "Trên thực tế", "Thực tế", "Nói chung", "Tóm lại",
]
_OPENER_RE = re.compile(r"(^(?:- )?|[.!?…]\s+|:\s+)(%s)\s+(?![,.;:!?])(?=\S)" % "|".join(sorted(OPENERS, key=len, reverse=True)))

# conjunctions that take a comma in front when mid-sentence ("..., nhưng ...")
CONJ = ["nhưng mà", "nhưng", "thế nhưng", "tuy nhiên", "hơn nữa", "cho nên", "bởi vì", "vì vậy", "vì thế",
        "do đó", "ngược lại", "ngoài ra", "tuy vậy", "vậy mà", "thế mà"]
_CONJ_RE = re.compile(r"(?<=[%s0-9”\)])\s+(%s)(?=\s)" % (LOWER, "|".join(sorted(CONJ, key=len, reverse=True))))
_NO_COMMA_BEFORE = {"không", "chẳng", "mà", "và", "hay", "hoặc", "nhưng"}


def _comma_before(m: "re.Match") -> str:
    prev = m.string[: m.start()].rsplit(None, 1)
    if prev and prev[-1].lower() in _NO_COMMA_BEFORE:
        return m.group(0)
    return ", " + m.group(1)


def _fix_quotes(p: str) -> str:
    """Normalise every quote mark to “ ” (alternating); drop them all if they do not pair up."""
    marks = [i for i, ch in enumerate(p) if ch in "“”‘’'\""]
    if not marks:
        return p
    if len(marks) % 2:
        return re.sub(r"[“”‘’'\"]", "", p)
    out = list(p)
    for n, i in enumerate(marks):
        out[i] = "“" if n % 2 == 0 else "”"
    return "".join(out)


def _fix_spacing(p: str) -> str:
    p = p.replace(" ", " ")
    p = re.sub(r"(?:\.\s*){3,}|…+|\.{2,}", "…", p)  # ... / . . . / …… -> …
    p = re.sub(r"\s+([,;:.!?…])", r"\1", p)  # no space before punctuation
    p = re.sub(r",\s*,+", ",", p)
    p = re.sub(r"([!?…])\.", r"\1", p)
    p = re.sub(r",([.!?])", r"\1", p)
    p = re.sub(r"(?<![\d])([,;:])(?=[^\s\d,”)])", r"\1 ", p)  # space after , ; :
    p = re.sub(r"([%s][.!?])(?=[%s])" % (LOWER, UPPER), r"\1 ", p)  # "xong.Diệp" -> "xong. Diệp"
    p = re.sub(r"…(?=[%s%s])" % (LOWER, UPPER), "… ", p)
    p = re.sub(r"[ \t]{2,}", " ", p)
    return p.strip()


def _ensure_end(p: str) -> str:
    """Make sure a paragraph ends in terminal punctuation."""
    p = p.rstrip()
    if not re.search(r"\w", p):
        return p
    p = re.sub(r"[,;]+$", ".", p)
    if re.search(r"[.!?…:]$", p):
        return p
    m = re.search(r"([.!?…])[”)]+$", p)  # punctuation already inside the closing quote
    if m:
        return p
    return p + "."


def _split_dialogue(p: str) -> list[str]:
    """Split a narration paragraph that has dialogue dashes glued inside it."""
    parts, last = [], 0
    for m in re.finditer(r"(?<=[:.!?…”])\s+-\s+(?=\S)", p):
        prev = p[m.start() - 1]
        nxt = p[m.end()]
        if prev == ":" or (prev in ".!?…" and re.match(r"[%s“]" % UPPER, nxt)):
            parts.append(p[last : m.start()])
            last = m.end()
            parts.append(None)  # marker: next part is dialogue
    parts.append(p[last:])
    out, is_dlg = [], False
    for part in parts:
        if part is None:
            is_dlg = True
            continue
        part = part.strip()
        if part:
            out.append(("- " + part) if is_dlg else part)
        is_dlg = False
    return out


def _punctuate(p: str) -> str:
    p = _fix_quotes(p)
    p = _fix_spacing(p)
    p = _OPENER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}, ", p)
    p = _CONJ_RE.sub(_comma_before, p)
    p = _fix_spacing(p)
    return _ensure_end(p)


# ---------------------------------------------------------------- main cleaning
def _extract_title(lines: list[str], fallback_name: str) -> tuple[str, int | None]:
    for line in lines[:8]:
        m = TITLE_RE.match(line.strip())
        if m:
            num, text = int(m.group(2)), m.group(3).strip().rstrip(".").strip()
            text = _fix_quotes(re.sub(r"\s+", " ", text))
            return (f"Chương {num}: {text}." if text else f"Chương {num}."), num
    base = os.path.splitext(os.path.basename(fallback_name))[0]
    m = TITLE_RE.match(re.sub(r"^\d+_", "", base))
    if m:
        text = m.group(3).strip().rstrip(".")
        return (f"Chương {int(m.group(2))}: {text}." if text else f"Chương {int(m.group(2))}."), int(m.group(2))
    return "", None


def _norm(t: str) -> str:
    return re.sub(r"\W+", "", t.lower())


def _same_title(text: str, title_key: str | None) -> bool:
    if not title_key:
        return False
    k = _norm(text)
    return k.startswith(title_key) or difflib.SequenceMatcher(None, k, title_key).ratio() >= 0.85


def clean_text(raw: str, name: str = "") -> str:
    text = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    lines = [l.strip() for l in text.split("\n")]
    nonempty = [l for l in lines if l]
    title, num = _extract_title(nonempty, name)
    title_key = _norm(title.split(":", 1)[1]) if ":" in title else None

    paragraphs: list[str] = []
    title_seen = False
    for line in nonempty:
        for rx in INLINE_JUNK:
            line = rx.sub("", line)
        line = _STRAY_RE.sub("", line).strip()
        if not line or _JUNK_RE.match(line):
            continue
        tm = TITLE_RE.match(line)
        if tm and len(line) <= len(title) + 40 and (
            (num is not None and abs(int(tm.group(2)) - num) <= 5) or _same_title(tm.group(3), title_key)
        ):
            continue  # any copy of the chapter title, even with a different chapter number
        if not re.search(r"\w", line):  # "…", "***", "- ..." : nothing to read
            continue
        dm = re.match(r"^[-–—−]+\s*(.*)$", line)
        if dm:
            line = "- " + dm.group(1)
        pieces = [line] if line.startswith("- ") and not re.search(r"[:.!?…”]\s+-\s+\S", line) else _split_dialogue(line)
        for piece in pieces:
            piece = _punctuate(piece)
            if re.search(r"\w", piece):
                paragraphs.append(piece)
    body = "\n\n".join(paragraphs)
    return (title + "\n\n" + body if title else body).rstrip() + "\n"


def clean_folder(folder: str, dry_run: bool = False, backup: bool = True) -> dict:
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    if not files:
        raise FileNotFoundError(f"Khong co file .txt trong: {folder}")
    backup_dir = None
    if backup and not dry_run:
        backup_dir = folder.rstrip("\\/") + "_backup_" + time.strftime("%Y%m%d_%H%M%S")
        os.makedirs(backup_dir)
        for f in files:
            shutil.copy2(f, backup_dir)
    changed = 0
    before = after = 0
    for f in files:
        raw = open(f, encoding="utf-8").read()
        new = clean_text(raw, f)
        before += len(raw)
        after += len(new)
        if new != raw:
            changed += 1
            if not dry_run:
                with open(f, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(new)
    return {"files": len(files), "changed": changed, "chars_before": before, "chars_after": after, "backup": backup_dir}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lam sach file chuong .txt de doc / TTS.")
    ap.add_argument("folder")
    ap.add_argument("--dry-run", action="store_true", help="Chi dem, khong ghi file.")
    ap.add_argument("--no-backup", action="store_true", help="Khong sao luu truoc khi ghi de.")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(clean_folder(a.folder, dry_run=a.dry_run, backup=not a.no_backup))


if __name__ == "__main__":
    main()
