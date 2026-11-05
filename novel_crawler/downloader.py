import json
import logging
import os
import random
import re
import time

from .discovery import discover_chapters
from .extractor import extract_chapter, find_next_link
from .net import build_session, fetch, is_allowed_by_robots, polite_sleep
from .utils import sanitize_filename

LOG = logging.getLogger("novel_crawler")

_CHAPTER_PATTERN = re.compile(r"(chuong|chap|chapter)[-_](\d+)", re.IGNORECASE)
_LAST_NUMBER = re.compile(r"(\d+)(?!.*\d)")
CHECKPOINT_FILE = ".checkpoint.json"


def save_checkpoint(out_dir: str, next_url: str, next_idx: int) -> None:
    """Save progress so crawling can be resumed later."""
    try:
        os.makedirs(out_dir, exist_ok=True)
        cp_path = os.path.join(out_dir, CHECKPOINT_FILE)
        data = {
            "next_url": next_url,
            "next_chapter": next_idx,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(cp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        LOG.debug("Khong the ghi checkpoint: %s", exc)


def load_checkpoint(out_dir: str) -> dict | None:
    """Load saved checkpoint data if available."""
    try:
        cp_path = os.path.join(out_dir, CHECKPOINT_FILE)
        if os.path.isfile(cp_path):
            with open(cp_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        LOG.debug("Khong the doc checkpoint: %s", exc)
    return None


def clear_checkpoint(out_dir: str) -> None:
    """Remove checkpoint once downloading is completely finished."""
    try:
        cp_path = os.path.join(out_dir, CHECKPOINT_FILE)
        if os.path.isfile(cp_path):
            os.remove(cp_path)
    except Exception:
        pass


def get_highest_chapter(out_dir: str) -> int:
    """Return the highest chapter number from saved text files in out_dir."""
    if not os.path.isdir(out_dir):
        return 0
    highest = 0
    for name in os.listdir(out_dir):
        if name.endswith(".txt") and len(name) >= 4 and name[:4].isdigit():
            highest = max(highest, int(name[:4]))
    return highest


def fetch_with_retry(
    session,
    url: str,
    max_retries: int = 5,
    initial_delay: float = 3.0,
    stopped=None,
    report_func=None,
) -> str:
    """Fetch URL with automatic exponential backoff retry on network errors or timeouts."""
    delay = initial_delay
    for attempt in range(1, max_retries + 1):
        if stopped and stopped():
            raise InterruptedError("Da dung theo yeu cau.")
        try:
            return fetch(session, url)
        except Exception as exc:
            if stopped and stopped():
                raise InterruptedError("Da dung theo yeu cau.")
            if attempt >= max_retries:
                raise exc
            msg = f"Loi mang ({type(exc).__name__}). Tu dong thu lai ({attempt}/{max_retries}) sau {delay:.1f}s..."
            LOG.warning(msg)
            if report_func:
                report_func(msg)
            elapsed = 0.0
            while elapsed < delay:
                if stopped and stopped():
                    raise InterruptedError("Da dung theo yeu cau.")
                time.sleep(0.5)
                elapsed += 0.5
            delay = min(delay * 1.5, 30.0)


def _save_chapter(out_dir: str, index: int, title: str, content: str, clean: bool = False) -> str:
    filename = f"{index:04d}_{sanitize_filename(title)}.txt"
    path = os.path.join(out_dir, filename)
    raw = title + "\n\n" + content
    if clean:
        try:
            from .cleaner import clean_text
            text = clean_text(raw, filename)
        except Exception as exc:
            LOG.warning("Khong the lam sach van ban chuong %d: %s", index, exc)
            text = raw
    else:
        text = raw
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def url_for_chapter(url: str, number: int) -> str:
    """Replace the chapter number in the URL path with `number` (e.g. .../chuong-1/ -> .../chuong-50/).

    Returns the URL unchanged if it already points to chapter `number`, or when the path has no number.
    """
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)

    match = _CHAPTER_PATTERN.search(parsed.path)
    if match:
        _prefix, existing_num = match.groups()
        if int(existing_num) == number:
            return url
        new_path = _CHAPTER_PATTERN.sub(rf"\g<1>-{number}", parsed.path, count=1)
        return urlunparse(parsed._replace(path=new_path))

    if not _LAST_NUMBER.search(parsed.path):
        return url
    new_path = _LAST_NUMBER.sub(str(number), parsed.path, count=1)
    return urlunparse(parsed._replace(path=new_path))


def crawl(
    start_url: str,
    out_dir: str,
    delay: float = 1.5,
    max_chapters: int = None,
    timeout: float = 20.0,
    retries: int = 5,
    ignore_robots: bool = False,
    start_chapter: int = 1,
    end_chapter: int = None,
    progress=None,
    should_stop=None,
    clean: bool = False,
    on_chapter_saved=None,
):
    """Crawl chapters [start_chapter, end_chapter] (1-based, inclusive).

    max_chapters is kept for backwards compatibility: it limits the number of
    chapters counted from start_chapter. progress(done, total_or_None, message)
    and should_stop() -> bool are optional hooks used by the GUI.
    """
    start_chapter = max(1, start_chapter or 1)
    if max_chapters:
        limit = start_chapter + max_chapters - 1
        end_chapter = min(end_chapter, limit) if end_chapter else limit
    if end_chapter and end_chapter < start_chapter:
        raise ValueError("end_chapter phai >= start_chapter")

    def report(done, total, message):
        if progress:
            progress(done, total, message)

    def stopped():
        return bool(should_stop and should_stop())

    os.makedirs(out_dir, exist_ok=True)

    if not ignore_robots and not is_allowed_by_robots(start_url):
        raise PermissionError(
            f"robots.txt tren site nay khong cho phep crawl URL: {start_url}. "
            "Dung --ignore-robots neu ban chac chan muon tiep tuc (tu chiu trach nhiem)."
        )

    session = build_session(timeout=timeout, retries=retries)

    LOG.info("Dang do tim danh sach chuong tu: %s", start_url)
    report(0, None, "Dang do tim danh sach chuong...")
    chapters = discover_chapters(session, start_url)

    saved = 0
    if chapters:
        LOG.info("Tim thay trang muc luc voi %d chuong.", len(chapters))
        last = min(end_chapter, len(chapters)) if end_chapter else len(chapters)
        total = max(0, last - start_chapter + 1)
        for idx in range(start_chapter, last + 1):
            if stopped():
                LOG.info("Da dung theo yeu cau.")
                break
            list_title, url = chapters[idx - 1]
            try:
                html = fetch_with_retry(
                    session,
                    url,
                    max_retries=retries,
                    stopped=stopped,
                    report_func=lambda msg: report(saved, total, msg),
                )
                title, content = extract_chapter(html, url)
            except InterruptedError:
                LOG.info("Da dung theo yeu cau.")
                break
            except Exception as exc:
                LOG.warning("Bo qua chuong %d (%s) sau khi thu lai that bai: %s", idx, url, exc)
                continue
            final_title = title if title and title != "Untitled" else list_title
            path = _save_chapter(out_dir, idx, final_title, content, clean=clean)
            LOG.info("[%d/%d] Da luu: %s", idx, last, path)
            saved += 1
            if on_chapter_saved:
                try:
                    on_chapter_saved(idx, path)
                except Exception as exc:
                    LOG.warning("Loi on_chapter_saved chuong %d: %s", idx, exc)
            report(saved, total, f"Da luu chuong {idx}: {final_title}")
            polite_sleep(delay + random.uniform(0, 0.5))
    else:
        LOG.info("Khong phat hien trang muc luc, chuyen sang che do crawl tuan tu theo 'chuong ke tiep'.")
        idx = start_chapter
        url = url_for_chapter(start_url, start_chapter) if start_chapter > 1 else start_url
        total = (end_chapter - start_chapter + 1) if end_chapter else None
        visited = set()
        while url and url not in visited:
            if stopped():
                LOG.info("Da dung theo yeu cau.")
                save_checkpoint(out_dir, url, idx)
                break
            visited.add(url)
            try:
                html = fetch_with_retry(
                    session,
                    url,
                    max_retries=retries,
                    stopped=stopped,
                    report_func=lambda msg: report(saved, total, msg),
                )
            except InterruptedError:
                LOG.info("Da dung theo yeu cau.")
                save_checkpoint(out_dir, url, idx)
                break
            except Exception as exc:
                LOG.warning("Dung lai o chuong %d (%s) sau %d lan thu lai: %s", idx, url, retries, exc)
                save_checkpoint(out_dir, url, idx)
                report(saved, total, f"Tam dung o chuong {idx}. Da luu tien do vao checkpoint.")
                break

            title, content = extract_chapter(html, url)
            path = _save_chapter(out_dir, idx, title, content, clean=clean)
            LOG.info("[%d] Da luu: %s", idx, path)
            saved += 1
            if on_chapter_saved:
                try:
                    on_chapter_saved(idx, path)
                except Exception as exc:
                    LOG.warning("Loi on_chapter_saved chuong %d: %s", idx, exc)
            report(saved, total, f"Da luu chuong {idx}: {title}")
            if end_chapter and idx >= end_chapter:
                clear_checkpoint(out_dir)
                break
            next_url = find_next_link(html, url)
            if not next_url or next_url == url:
                LOG.info("Khong tim thay lien ket 'chuong sau', dung lai.")
                clear_checkpoint(out_dir)
                break
            if next_url in visited:
                LOG.warning("Lien ket 'chuong sau' tro ve trang da crawl (%s), dung lai.", next_url)
                clear_checkpoint(out_dir)
                break
            url = next_url
            idx += 1
            save_checkpoint(out_dir, url, idx)
            polite_sleep(delay + random.uniform(0, 0.5))

    LOG.info("Hoan tat. Da luu %d chuong vao thu muc: %s", saved, out_dir)
    return saved
