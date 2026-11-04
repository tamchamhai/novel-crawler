import argparse
import os
import sys
from urllib.parse import urlparse

from .downloader import crawl
from .utils import sanitize_filename, setup_logging


def _default_out_dir(url: str) -> str:
    parsed = urlparse(url)
    domain = sanitize_filename(parsed.netloc.replace(":", "_"))
    segments = [s for s in parsed.path.split("/") if s]
    slug = sanitize_filename(segments[-1]) if segments else "truyen"
    return os.path.join("novels", domain, slug)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="novel-crawler",
        description="Crawl toan bo chuong cua mot truyen online tu 1 URL (trang muc luc hoac 1 chuong bat ky).",
    )
    parser.add_argument("url", help="URL trang muc luc truyen, hoac URL cua 1 chuong bat ky.")
    parser.add_argument("-o", "--out", help="Thu muc luu file .txt (mac dinh: novels/<domain>).")
    parser.add_argument("--delay", type=float, default=1.5, help="Thoi gian cho giua cac request (giay). Mac dinh 1.5s.")
    parser.add_argument("--max-chapters", type=int, default=None, help="Gioi han so chuong crawl (de test thu).")
    parser.add_argument("--timeout", type=float, default=15.0, help="Timeout cho moi request (giay).")
    parser.add_argument("--retries", type=int, default=3, help="So lan thu lai khi request loi.")
    parser.add_argument("--ignore-robots", action="store_true", help="Bo qua kiem tra robots.txt (tu chiu trach nhiem).")
    parser.add_argument("-v", "--verbose", action="store_true", help="In log chi tiet.")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    out_dir = args.out or _default_out_dir(args.url)

    try:
        saved = crawl(
            start_url=args.url,
            out_dir=out_dir,
            delay=args.delay,
            max_chapters=args.max_chapters,
            timeout=args.timeout,
            retries=args.retries,
            ignore_robots=args.ignore_robots,
        )
    except PermissionError as exc:
        print(f"Bi chan boi robots.txt: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("\nDa huy theo yeu cau nguoi dung.", file=sys.stderr)
        sys.exit(130)

    if saved == 0:
        print("Khong crawl duoc chuong nao. Co the site nay dung cau truc khong duoc ho tro.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
