"""Simple Steam store search helper with translation fallback.

Usage:
    python search.py "elden ring"
"""

from __future__ import annotations

import argparse
import json
from typing import Dict, List, Optional

import requests


STEAM_SEARCH_API = "https://store.steampowered.com/api/storesearch/"

# Simple browser UA reduces chances of the store returning filtered results.
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def _is_ascii(text: str) -> bool:
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _translate_to_english(term: str) -> Optional[str]:
    """Best-effort translate to English using Google's public endpoint."""
    translate_api = (
        "https://translate.googleapis.com/translate_a/single"
        "?client=gtx&sl=auto&tl=en&dt=t&q={}"
    )
    try:
        response = requests.get(
            translate_api.format(requests.utils.quote(term)),
            headers=DEFAULT_HEADERS,
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        # data format: [[["translated text", "original text", ...], ...], ...]
        translated = "".join(part[0] for part in data[0])
        return translated.strip()
    except Exception:
        return None


def _fetch_store(term: str, language: str, country: str) -> List[Dict[str, object]]:
    params = {
        "term": term,
        "l": language,
        "cc": country,
    }
    response = requests.get(
        STEAM_SEARCH_API, params=params, timeout=15, headers=DEFAULT_HEADERS
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("items", [])


def search_games(
    term: str,
    *,
    limit: int = 10,
    language: str = "schinese",
    country: str = "CN",
    translate_fallback: bool = True,
) -> List[Dict[str, object]]:
    """Search Steam by term and return game name, appid, and image URL."""
    items = _fetch_store(term, language, country)

    # If the original term is non-ASCII and results are thin, try an English translation.
    if translate_fallback and len(items) < limit and not _is_ascii(term):
        translated = _translate_to_english(term)
        if translated and translated.lower() != term.lower():
            translated_items = _fetch_store(translated, "english", country)
            # Merge while preserving order and de-duplicating by appid.
            seen = {item.get("id") for item in items}
            for item in translated_items:
                if item.get("id") not in seen:
                    items.append(item)
                    seen.add(item.get("id"))

    results: List[Dict[str, object]] = []
    for item in items[:limit]:
        image = (
            item.get("tiny_image")
            or item.get("large_capsule_image")
            or item.get("header_image")
            or ""
        )
        results.append(
            {
                "name": item.get("name", ""),
                "appid": item.get("id"),
                "image": image,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search Steam and return game name, appid, and image URL."
    )
    parser.add_argument("term", help="Search keyword")
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=10,
        help="Max number of results to return (default: 10)",
    )
    parser.add_argument(
        "-l",
        "--language",
        default="schinese",
        help="Language code for Steam (default: schinese)",
    )
    parser.add_argument(
        "-c",
        "--country",
        default="CN",
        help="Country code for Steam (default: CN)",
    )
    parser.add_argument(
        "--no-translate",
        action="store_true",
        help="Disable automatic English translation fallback for non-ASCII terms",
    )
    args = parser.parse_args()

    results = search_games(
        args.term,
        limit=args.limit,
        language=args.language,
        country=args.country,
        translate_fallback=not args.no_translate,
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
