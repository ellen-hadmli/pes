#!/usr/bin/env python3
"""Small, local, page-aware search over the portable Hekate library."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "Library"
INDEX = ROOT / "library.json"
PAGE_HEADING = re.compile(r"^## PDF page (\d+)\s*$", re.M)
MAX_RESULTS_HARD = 50
MAX_PAGES_HARD = 3
MAX_CHARS_HARD = 20_000
MAX_CONTEXT_HARD = 300


def load_index(root: Path = ROOT) -> dict[str, Any]:
    return json.loads((root / "library.json").read_text(encoding="utf-8"))


def resolve_book(index: dict[str, Any], key: str) -> dict[str, Any]:
    folded = key.casefold().strip()
    matches = []
    for book in index["books"]:
        keys = [book["id"], book["title"], *book.get("aliases", [])]
        if any(folded == str(candidate).casefold().strip() for candidate in keys):
            matches.append(book)
    if not matches:
        raise ValueError(f"Книга не найдена: {key}. Сначала выполните list.")
    if len(matches) > 1:
        raise ValueError(f"Неоднозначное название: {key}. Используйте id книги.")
    return matches[0]


def split_pages(text: str) -> list[tuple[int, str]]:
    matches = list(PAGE_HEADING.finditer(text))
    if not matches:
        return []
    result = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        result.append((int(match.group(1)), text[match.end():end].strip()))
    return result


def list_books(index: dict[str, Any], all_books: bool) -> list[dict[str, Any]]:
    books = index["books"] if all_books else index["books"]
    return [{"id": b["id"], "title": b["title"], "aliases": b["aliases"],
             "extraction": b["extraction"], "quality": b["quality"],
             "markdown": b["markdown"], "pdf": b.get("pdf")}
            for b in books]


def search(index: dict[str, Any], query: str, *, book_key: str | None,
           all_books: bool, max_results: int, context_chars: int,
           root: Path = ROOT) -> list[dict[str, Any]]:
    if (book_key is None) == (not all_books):
        raise ValueError("Укажите ровно один режим: --book ID или --all.")
    if not query.strip():
        raise ValueError("Пустой поисковый запрос.")
    max_results = max(1, min(max_results, MAX_RESULTS_HARD))
    context_chars = max(0, min(context_chars, MAX_CONTEXT_HARD))
    books = [resolve_book(index, book_key)] if book_key else index["books"]
    pattern = re.compile(re.escape(query), re.I)
    hits: list[dict[str, Any]] = []
    for record in books:
        text = (root / record["markdown"]).read_text(encoding="utf-8", errors="replace")
        pages = split_pages(text)
        if pages:
            candidates = pages
        else:
            candidates = [(None, text)]
        for page, body in candidates:
            for match in pattern.finditer(body):
                left = max(0, match.start() - context_chars)
                right = min(len(body), match.end() + context_chars)
                excerpt = re.sub(r"\s+", " ", body[left:right]).strip()
                hits.append({"book_id": record["id"], "title": record["title"],
                             "page_pdf": page, "source": record.get("pdf") or record["markdown"],
                             "excerpt": excerpt})
                if len(hits) >= max_results:
                    return hits
    return hits


def show_pages(index: dict[str, Any], book_key: str, page: int,
               pages_count: int = 1, max_chars: int = 8000,
               root: Path = ROOT) -> list[dict[str, Any]]:
    if page < 1:
        raise ValueError("Номер страницы PDF начинается с 1.")
    pages_count = max(1, min(pages_count, MAX_PAGES_HARD))
    max_chars = max(100, min(max_chars, MAX_CHARS_HARD))
    record = resolve_book(index, book_key)
    text = (root / record["markdown"]).read_text(encoding="utf-8", errors="replace")
    parsed = split_pages(text)
    if not parsed:
        if page != 1:
            raise ValueError("У DOCX нет нумерации страниц PDF; используйте страницу 1 как весь текст.")
        body = text.split("## DOCX extracted text", 1)[-1].strip()
        return [{"book_id": record["id"], "title": record["title"],
                 "page_pdf": None, "source": record["markdown"],
                 "note": "DOCX: страница не указана", "text": body[:max_chars],
                 "truncated": len(body) > max_chars}]
    found = {n: body for n, body in parsed}
    if page not in found:
        raise ValueError(f"Страница PDF {page} отсутствует в извлечённом тексте.")
    selected = []
    for number in range(page, min(page + pages_count, max(found) + 1)):
        if number not in found:
            break
        body = found[number]
        selected.append({"book_id": record["id"], "title": record["title"],
                         "page_pdf": number, "source": record.get("pdf") or record["markdown"],
                         "text": body[:max_chars], "truncated": len(body) > max_chars})
    return selected


def emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, list) and value and "excerpt" in value[0]:
        for hit in value:
            locator = (f"PDF, стр. {hit['page_pdf']}" if hit["page_pdf"] else "страница не указана")
            print(f"\n[{hit['title']}; {locator}; {hit['source']}]\n{hit['excerpt']}")
        print(f"\nСовпадений показано: {len(value)}")
    elif isinstance(value, list) and value and "text" in value[0]:
        for item in value:
            locator = f"PDF, стр. {item['page_pdf']}" if item["page_pdf"] else item["note"]
            print(f"\n[{item['title']}; {locator}; {item['source']}]\n{item['text']}")
            if item["truncated"]:
                print("[текст страницы ограничен; запросите страницу отдельно]")
    elif isinstance(value, list):
        for item in value:
            print(f"{item['id']}\t{item['title']}\t[{item['extraction']}; {item['quality']}]")
    else:
        print(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list", help="показать каталог книг")
    p_list.add_argument("--json", action="store_true")
    p_search = sub.add_parser("search", help="искать внутри выбранной книги или явного --all")
    p_search.add_argument("query")
    scope = p_search.add_mutually_exclusive_group(required=True)
    scope.add_argument("--book", help="id, точное название или алиас из каталога")
    scope.add_argument("--all", action="store_true", help="искать по всем книгам явно")
    p_search.add_argument("--max-results", type=int, default=10)
    p_search.add_argument("--context", type=int, default=120)
    p_search.add_argument("--json", action="store_true")
    p_show = sub.add_parser("show", help="показать ограниченное число страниц выбранного источника")
    p_show.add_argument("--book", required=True)
    p_show.add_argument("--page", required=True, type=int)
    p_show.add_argument("--pages", type=int, default=1)
    p_show.add_argument("--max-chars", type=int, default=8000)
    p_show.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        index = load_index()
        if args.command == "list":
            result = list_books(index, all_books=True)
        elif args.command == "search":
            result = search(index, args.query, book_key=args.book, all_books=args.all,
                            max_results=args.max_results, context_chars=args.context)
        else:
            result = show_pages(index, args.book, args.page, args.pages, args.max_chars)
        emit(result, getattr(args, "json", False))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
