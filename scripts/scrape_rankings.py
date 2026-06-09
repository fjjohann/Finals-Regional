#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "sources.json"
OUTPUT_PATH = ROOT / "docs" / "data" / "rankings.json"
USER_AGENT = "FinalsRegionalBot/1.0 (+https://github.com/fjjohann/Finals-Regional)"


class RankingTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._table_depth = 0
        self._in_ranking_table = False
        self._in_row = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        if tag == "table":
            class_names = attrs_dict.get("class", "")
            if self._in_ranking_table:
                self._table_depth += 1
            elif "clear_table" in class_names.split():
                self._in_ranking_table = True
                self._table_depth = 1

        if not self._in_ranking_table:
            return

        if tag == "tr" and self._table_depth == 1:
            self._in_row = True
            self._current_row = []
        elif tag == "td" and self._in_row and self._table_depth == 1:
            self._in_cell = True
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_cell:
            self._current_row.append(normalize_text("".join(self._current_cell)))
            self._current_cell = []
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = []
            self._in_row = False
        elif tag == "table" and self._in_ranking_table:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._in_ranking_table = False


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def load_sources() -> dict[str, Any]:
    return json.loads(SOURCE_PATH.read_text(encoding="utf-8"))


def build_targets(sources: dict[str, Any]) -> list[dict[str, Any]]:
    year = str(sources["year"])
    base_url = sources["baseUrl"].rstrip("/")
    targets = []
    state_ranking = sources["stateRanking"]

    for category in sources["categories"]:
        url = (
            f"{base_url}/{year}/{state_ranking['path']}/"
            f"{category['code']}/{category['rankingId']}"
        )
        targets.append(
            {
                "rankingScope": "state",
                "regionalId": state_ranking["id"],
                "regionalLabel": state_ranking["label"],
                "categoryKey": f"{category['code']}:{category['rankingId']}",
                "categoryGroup": category["group"],
                "gender": category["gender"],
                "categoryLabel": category["label"],
                "categoryCode": category["code"],
                "rankingId": category["rankingId"],
                "url": url,
            }
        )

    for regional in sources["regionals"]:
        regional_id = regional["id"]
        for category in sources["categories"]:
            url = (
                f"{base_url}/{year}/bt-regiao-{regional_id}/"
                f"{category['code']}/{category['rankingId']}"
            )
            targets.append(
                {
                    "rankingScope": "regional",
                    "regionalId": regional_id,
                    "regionalLabel": regional["label"],
                    "categoryKey": f"{category['code']}:{category['rankingId']}",
                    "categoryGroup": category["group"],
                    "gender": category["gender"],
                    "categoryLabel": category["label"],
                    "categoryCode": category["code"],
                    "rankingId": category["rankingId"],
                    "url": url,
                }
            )
    return targets


def fetch_html(url: str, timeout: int) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason):
            context = ssl._create_unverified_context()
            with urlopen(request, timeout=timeout, context=context) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        raise


def parse_athletes(html: str) -> list[dict[str, Any]]:
    parser = RankingTableParser()
    parser.feed(html)
    athletes = []
    for cells in parser.rows:
        if len(cells) < 8:
            continue
        if not cells[0].isdigit() or not cells[1].isdigit():
            continue
        points_text = re.sub(r"[^\d]", "", cells[7])
        athletes.append(
            {
                "position": int(cells[0]),
                "athleteCode": cells[1],
                "name": cells[3],
                "points": int(points_text or "0"),
            }
        )
    return athletes


def scrape_target(target: dict[str, Any], timeout: int) -> dict[str, Any]:
    started = time.time()
    try:
        html = fetch_html(target["url"], timeout)
        athletes = parse_athletes(html)
        status = "ok"
        error = None
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        athletes = []
        status = "error"
        error = str(exc)

    return {
        **target,
        "status": status,
        "error": error,
        "athleteCount": len(athletes),
        "athletes": athletes,
        "durationMs": round((time.time() - started) * 1000),
    }


def scrape_all(max_workers: int, timeout: int) -> dict[str, Any]:
    sources = load_sources()
    targets = build_targets(sources)
    rankings = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_target = {
            executor.submit(scrape_target, target, timeout): target for target in targets
        }
        for future in as_completed(future_to_target):
            result = future.result()
            rankings.append(result)
            marker = "OK" if result["status"] == "ok" else "ERRO"
            print(
                f"{marker} {result['regionalLabel']} "
                f"{result['categoryCode']} ({result['athleteCount']} atletas)",
                file=sys.stderr,
            )

    rankings.sort(
        key=lambda item: (
            0 if item.get("rankingScope") == "state" else 1,
            0 if item.get("rankingScope") == "state" else int(item["regionalId"]),
            item["categoryGroup"],
            item["gender"],
            item["categoryLabel"],
            item["categoryCode"],
        )
    )

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "year": sources["year"],
        "source": "Federação Paranaense de Tênis",
        "sourceUrl": sources["baseUrl"],
        "totalRankings": len(rankings),
        "totalAthletes": sum(item["athleteCount"] for item in rankings),
        "rankings": rankings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Coleta rankings regionais de Beach Tennis.")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help="Arquivo JSON de saída.")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=int(os.environ.get("SCRAPER_WORKERS", "6")),
        help="Número máximo de coletas simultâneas.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("SCRAPER_TIMEOUT", "20")),
        help="Timeout por página em segundos.",
    )
    args = parser.parse_args()

    data = scrape_all(max_workers=args.max_workers, timeout=args.timeout)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(output_path)

    failures = [item for item in data["rankings"] if item["status"] != "ok"]
    print(
        f"Gerados {data['totalRankings']} rankings com "
        f"{data['totalAthletes']} linhas em {output_path}.",
        file=sys.stderr,
    )
    if failures:
        print(f"{len(failures)} páginas falharam.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
