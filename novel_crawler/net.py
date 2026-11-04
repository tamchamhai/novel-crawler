import logging
import time
import urllib.robotparser as robotparser
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter, Retry

LOG = logging.getLogger("novel_crawler")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

_robots_cache: dict = {}


def build_session(timeout: float = 15.0, retries: int = 3) -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    retry = Retry(
        total=retries,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.request_timeout = timeout  # custom attribute, read by fetch()
    return session


def is_allowed_by_robots(url: str) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    rp = _robots_cache.get(base)
    if rp is None:
        rp = robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            rp.read()
        except Exception:
            # If robots.txt is unreachable, don't block the user over it.
            _robots_cache[base] = None
            return True
        _robots_cache[base] = rp
    if rp is None:
        return True
    return rp.can_fetch(DEFAULT_HEADERS["User-Agent"], url)


def fetch(session: requests.Session, url: str) -> str:
    timeout = getattr(session, "request_timeout", 15.0)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def polite_sleep(delay: float) -> None:
    if delay > 0:
        time.sleep(delay)
