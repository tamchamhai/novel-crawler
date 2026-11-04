import logging
import re
import unicodedata

_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = _WS.sub(" ", name).strip()
    name = _INVALID_FS_CHARS.sub("", name)
    name = name.strip(" .")
    if not name:
        name = "chapter"
    name = name[:max_len]
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        name = "_" + name
    return name
