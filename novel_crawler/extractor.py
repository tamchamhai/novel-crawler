import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .utils import strip_accents

# Content container selectors seen on common VN/EN novel reading sites.
# Tried first, in order, before falling back to the generic density heuristic.
KNOWN_CONTENT_SELECTORS = [
    "#chapter-c",
    ".chapter-c",
    "#chapter-content",
    ".chapter-content",
    ".content-chapter",
    "#arraytext",
    ".reading-content .text-left",
    ".reading-content",
    ".chr-c",
    "#chr-content",
    ".chapter-reading-content",
    ".box-chap",
    ".entry-content",
    "article",
]

NOISE_TAGS = ["script", "style", "noscript", "iframe", "header", "footer", "nav", "form", "button", "aside"]

NEXT_KEYWORDS = [
    "chuong sau",
    "chuong tiep",
    "chuong ke tiep",
    "chap tiep",
    "tiep theo",
    "next chapter",
    "next",
    "sau",
]
NEXT_KEYWORDS = [strip_accents(k) for k in NEXT_KEYWORDS]
# Whole class/id tokens that mark a "next chapter" link.
PREV_ATTR_TOKENS = {"prev", "previous", "back", "prev-chap", "prev_chap", "btn-prev"}
PREV_TEXT_WORDS = {"truoc", "prev", "previous", "back"}
NEXT_ATTR_TOKENS = {"next", "next-chap", "next_chap", "next-chapter", "btn-next", "nextchap"}


def _clean(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()


def _text_of(node) -> str:
    text = node.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _best_candidate_by_density(soup: BeautifulSoup):
    candidates = soup.find_all(["div", "article", "section"])
    best, best_score = None, 0.0
    for node in candidates:
        text = node.get_text(separator=" ", strip=True)
        text_len = len(text)
        if text_len < 200:
            continue
        link_text_len = sum(len(a.get_text(strip=True)) for a in node.find_all("a"))
        link_ratio = link_text_len / text_len if text_len else 1.0
        if link_ratio > 0.4:
            continue
        p_count = len(node.find_all("p"))
        score = text_len * (1.0 - link_ratio) + p_count * 20
        if score > best_score:
            best, best_score = node, score
    return best


_TITLE_SEPARATOR = re.compile(r"\s+[-|–—]\s+")


def _chapter_title_from_title_tag(soup: BeautifulSoup, heading: str):
    """Return the leading part of <title> when the page heading is just the novel/site name.

    e.g. <title>Chuong 2: Ve que (2) - Vot Thi Nhan | Webnovel.vn</title> with h1 "Vot Thi Nhan"
    -> "Chuong 2: Ve que (2)".
    """
    if not soup.title:
        return None
    full = soup.title.get_text(strip=True)
    parts = _TITLE_SEPARATOR.split(full)
    if len(parts) < 2 or not parts[0].strip():
        return None
    head = parts[0].strip()
    if heading and heading != head and heading in parts[1:]:
        return head
    return None


def extract_title(soup: BeautifulSoup) -> str:
    # Chapter-specific selectors first; a bare h1 is often the novel name.
    for selector in [".chapter-title", ".chr-title"]:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node.get_text(strip=True)
    h1 = soup.select_one("h1")
    heading = h1.get_text(strip=True) if h1 else ""
    from_title_tag = _chapter_title_from_title_tag(soup, heading)
    if from_title_tag:
        return from_title_tag
    if heading:
        return heading
    node = soup.select_one("h2")
    if node and node.get_text(strip=True):
        return node.get_text(strip=True)
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    return "Untitled"


def extract_chapter(html: str, base_url: str):
    """Return (title, content_text) for a single chapter page."""
    soup = BeautifulSoup(html, "lxml")
    title = extract_title(soup)
    _clean(soup)

    for selector in KNOWN_CONTENT_SELECTORS:
        node = soup.select_one(selector)
        if node:
            text = _text_of(node)
            if len(text) > 200:
                return title, text

    node = _best_candidate_by_density(soup)
    if node is not None:
        return title, _text_of(node)

    body = soup.find("body")
    return title, _text_of(body) if body else ""


def find_next_link(html: str, base_url: str):
    """Best-effort detection of the 'next chapter' link on a chapter page."""
    soup = BeautifulSoup(html, "lxml")

    rel_next = soup.find("a", rel=lambda v: v and "next" in [r.lower() for r in (v if isinstance(v, list) else [v])])
    if rel_next and rel_next.get("href"):
        return urljoin(base_url, rel_next["href"])

    best = None
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript"):
            continue
        text = strip_accents(a.get_text(strip=True).lower())
        class_tokens = {c.lower() for c in a.get("class", [])}
        ident = a.get("id", "").lower()
        # Some sites tag the "previous" link with class="next"; never treat those as next.
        if class_tokens & PREV_ATTR_TOKENS or any(word in text.split() for word in PREV_TEXT_WORDS):
            continue
        # Match whole words/phrases only, so e.g. "Sau khi chet" is not a "next" link.
        text_match = text in NEXT_KEYWORDS or any(
            " " in keyword and keyword in text for keyword in NEXT_KEYWORDS
        )
        attr_match = bool(class_tokens & NEXT_ATTR_TOKENS) or ident in NEXT_ATTR_TOKENS
        if text_match:
            return urljoin(base_url, href)  # text is the most reliable signal
        if attr_match and best is None:
            best = urljoin(base_url, href)

    return best
