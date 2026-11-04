import logging
import re
from collections import defaultdict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .net import fetch
from .utils import strip_accents

LOG = logging.getLogger("novel_crawler")

KNOWN_TOC_SELECTORS = [
    "#list-chapter a",
    ".list-chapter a",
    ".chapter-list a",
    "ul.chapters a",
    ".chapters a",
    "#chapters a",
    ".list-chapters a",
]

NEXT_PAGE_KEYWORDS = [strip_accents(k) for k in ["trang sau", "next page", "next", "sau"]]

_DIGITS = re.compile(r"\d+")


def _normalize_href_pattern(href: str) -> str:
    path = urlparse(href).path
    return _DIGITS.sub("N", path)


def _same_domain(url_a: str, url_b: str) -> bool:
    return urlparse(url_a).netloc == urlparse(url_b).netloc


def _links_from_selector(soup: BeautifulSoup, base_url: str, selector: str):
    nodes = soup.select(selector)
    links = []
    for node in nodes:
        href = node.get("href")
        if not href:
            continue
        text = node.get_text(strip=True)
        links.append((text, urljoin(base_url, href)))
    return links


def _looks_like_pagination_group(links) -> bool:
    numeric_only = sum(1 for text, _ in links if text and _DIGITS.fullmatch(text.strip()))
    return len(links) > 0 and numeric_only / len(links) > 0.8


def _discover_by_pattern(soup: BeautifulSoup, base_url: str, min_group_size: int = 8):
    groups = defaultdict(list)
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if not _same_domain(href, base_url):
            continue
        text = a.get_text(strip=True)
        if not text:
            continue
        pattern = _normalize_href_pattern(href)
        if pattern in ("", "/", "N"):
            continue
        groups[pattern].append((text, href))

    candidates = [g for g in groups.values() if len(g) >= min_group_size and not _looks_like_pagination_group(g)]
    if not candidates:
        return None
    best = max(candidates, key=len)
    # Dedup while preserving order of first appearance.
    seen = set()
    unique = []
    for text, href in best:
        if href in seen:
            continue
        seen.add(href)
        unique.append((text, href))
    return unique


def _find_toc_next_page(soup: BeautifulSoup, base_url: str):
    rel_next = soup.find("a", rel=lambda v: v and "next" in [r.lower() for r in (v if isinstance(v, list) else [v])])
    if rel_next and rel_next.get("href"):
        return urljoin(base_url, rel_next["href"])
    for a in soup.find_all("a", href=True):
        text = strip_accents(a.get_text(strip=True).lower())
        if any(k == text or k in text for k in NEXT_PAGE_KEYWORDS):
            return urljoin(base_url, a["href"])
    return None


def discover_chapters(session, start_url: str, max_toc_pages: int = 20):
    """Try to treat start_url as a table-of-contents page.

    Returns a list of (title, url) tuples in reading order, or None if the
    page doesn't look like a chapter list.
    """
    all_links = []
    seen_hrefs = set()
    url = start_url
    pages_visited = 0

    while url and pages_visited < max_toc_pages:
        html = fetch(session, url)
        soup = BeautifulSoup(html, "lxml")
        pages_visited += 1

        links = None
        for selector in KNOWN_TOC_SELECTORS:
            found = _links_from_selector(soup, url, selector)
            if len(found) >= 5:
                links = found
                break
        if links is None:
            links = _discover_by_pattern(soup, url)

        if not links:
            break

        for text, href in links:
            if href not in seen_hrefs:
                seen_hrefs.add(href)
                all_links.append((text, href))

        url = _find_toc_next_page(soup, url)

    if url and pages_visited >= max_toc_pages:
        LOG.warning(
            "Da dat gioi han %d trang muc luc, co the con chuong chua duoc liet ke. "
            "Tang max_toc_pages neu truyen qua dai.",
            max_toc_pages,
        )

    if len(all_links) < 5:
        return None
    return all_links
