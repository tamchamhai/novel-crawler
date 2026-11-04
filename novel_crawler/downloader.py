import logging
import os
import random
import re

from .discovery import discover_chapters
from .extractor import extract_chapter, find_next_link
from .net import build_session, fetch, is_allowed_by_robots, polite_sleep
from .utils import sanitize_filename

LOG = logging.getLogger("novel_crawler")

_LAST_NUMBER = re.compile(r"(\d+)(?!.*\d)")


def _save_chapter(out_dir: str, index: int, title: str, content: str) -> str:
    filename = f"{index:04d}_{sanitize_filename(title)}.txt"
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(title + "\n\n" + content)
    return path


def url_for_chapter(url: str, number: int) -> str:
    """Replace the last number in the URL path with `number` (e.g. .../chuong-1/ -> .../chuong-50/).

    Returns the URL unchanged when the path has no number.
    """
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    if not _LAST_NUMBER.search(parsed.path):
        return url
    new_path = _LAST_NUMBER.sub(str(number), parsed.path, count=1)
    return urlunparse(parsed._replace(path=new_path))


def crawl(
    start_url: str,
    out_dir: str,
    delay: float = 1.5,
    max_chapters: int = None,
    timeout: float = 15.0,
    retries: int = 3,
    ignore_robots: bool = False,
    start_chapter: int = 1,
    end_chapter: int = None,
    progress=None,
    should_stop=None,
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
                html = fetch(session, url)
                title, content = extract_chapter(html, url)
            except Exception as exc:
                LOG.warning("Bo qua chuong %d (%s): %s", idx, url, exc)
                continue
            final_title = title if title and title != "Untitled" else list_title
            path = _save_chapter(out_dir, idx, final_title, content)
            LOG.info("[%d/%d] Da luu: %s", idx, last, path)
            saved += 1
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
                break
            visited.add(url)
            try:
                html = fetch(session, url)
            except Exception as exc:
                LOG.warning("Dung lai o chuong %d (%s): %s", idx, url, exc)
                break
            title, content = extract_chapter(html, url)
            path = _save_chapter(out_dir, idx, title, content)
            LOG.info("[%d] Da luu: %s", idx, path)
            saved += 1
            report(saved, total, f"Da luu chuong {idx}: {title}")
            if end_chapter and idx >= end_chapter:
                break
            next_url = find_next_link(html, url)
            if not next_url or next_url == url:
                LOG.info("Khong tim thay lien ket 'chuong sau', dung lai.")
                break
            url = next_url
            idx += 1
            polite_sleep(delay + random.uniform(0, 0.5))

    LOG.info("Hoan tat. Da luu %d chuong vao thu muc: %s", saved, out_dir)
    return saved
