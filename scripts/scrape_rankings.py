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
STATE_RESULT_LIMIT = 8
REMAINING_STATE_EVENTS = 2
FUTURE_STATE_EVENT_POINTS = 3000
FEDERATION_TECHNICAL_LABELS = {"A", "B", "C"}


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


class PointsCompositionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_row = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._in_row = True
            self._current_row = []
        elif tag == "td" and self._in_row:
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


def ranking_positions(athletes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positioned = []
    previous_points = None
    previous_position = 0

    for index, athlete in enumerate(athletes, start=1):
        position = previous_position if athlete["points"] == previous_points else index
        positioned.append({**athlete, "position": position})
        previous_points = athlete["points"]
        previous_position = position

    return positioned


def parse_athletes(html: str, category_code: str | None = None) -> list[dict[str, Any]]:
    parser = RankingTableParser()
    parser.feed(html)
    athletes = []
    expected_category = normalize_text(category_code or "").upper()

    for cells in parser.rows:
        if len(cells) < 8:
            continue
        if not cells[0].isdigit() or not cells[1].isdigit():
            continue
        athlete_category = normalize_text(cells[5]).upper()
        if expected_category and athlete_category != expected_category:
            continue
        points_text = re.sub(r"[^\d]", "", cells[7])
        athletes.append(
            {
                "position": int(cells[0]),
                "athleteCode": cells[1],
                "name": cells[3],
                "categoryCode": athlete_category,
                "sourcePosition": int(cells[0]),
                "points": int(points_text or "0"),
            }
        )
    return ranking_positions(athletes) if expected_category else athletes


def parse_tennis_ids(html: str) -> list[str]:
    return re.findall(r"Pontos\((\d+), 'div\d+', 'ic\d+', 'pt\d+', 'estId\d+'\);", html)


def parse_points_endpoint_suffix(html: str) -> str:
    match = re.search(r"PontosPartial/' \+ 2026 \+ '/' \+ 'bt-estadual' \+ '/' \+ '[^']+' \+ '/' \+ idTenista \+ '(/[^']+)'", html)
    return match.group(1) if match else "/5/34"


def parse_point_components(html: str, current_total: int) -> list[int]:
    parser = PointsCompositionParser()
    parser.feed(html)
    points = []

    for row in parser.rows:
        if len(row) < 4:
            continue
        points_text = re.sub(r"[^\d]", "", row[-1])
        if points_text:
            points.append(int(points_text))

    if points and points[-1] == current_total:
        points = points[:-1]
    return points


def projected_state_points(components: list[int]) -> int:
    future_points = [FUTURE_STATE_EVENT_POINTS] * REMAINING_STATE_EVENTS
    return sum(sorted([*components, *future_points], reverse=True)[:STATE_RESULT_LIMIT])


def has_federation_spots(target: dict[str, Any]) -> bool:
    if target.get("categoryGroup") != "Tecnicas":
        return True
    return target.get("categoryLabel") in FEDERATION_TECHNICAL_LABELS


def enrich_state_guarantees(
    target: dict[str, Any],
    html: str,
    athletes: list[dict[str, Any]],
    timeout: int,
) -> list[dict[str, Any]]:
    if target.get("rankingScope") != "state" or not athletes:
        return athletes
    if not has_federation_spots(target):
        return [{**athlete, "stateTop2Guaranteed": False} for athlete in athletes]

    tennis_ids = parse_tennis_ids(html)
    if len(tennis_ids) < len(athletes):
        return athletes

    suffix = parse_points_endpoint_suffix(html)
    ranking_id = target["rankingId"]
    base_url = "/".join(target["url"].split("/")[:3])
    projected_max_by_code: dict[str, int] = {}
    components_by_code: dict[str, list[int]] = {}
    top_two = athletes[:2]
    thresholds = [athlete["points"] for athlete in top_two]
    contenders = {
        index
        for threshold in thresholds
        for index, athlete in enumerate(athletes)
        if athlete["points"] + (REMAINING_STATE_EVENTS * FUTURE_STATE_EVENT_POINTS) >= threshold
    }

    for index in sorted(contenders):
        athlete = athletes[index]
        tennis_id = tennis_ids[index]
        points_url = f"{base_url}/Ranking/PontosPartial/{target['url'].split('/')[-4]}/bt-estadual/{ranking_id}/{tennis_id}{suffix}"
        try:
            point_html = fetch_html(points_url, timeout)
            components = parse_point_components(point_html, athlete["points"])
            components_by_code[athlete["athleteCode"]] = components
            projected_max_by_code[athlete["athleteCode"]] = projected_state_points(components)
        except (HTTPError, URLError, TimeoutError, OSError):
            projected_max_by_code[athlete["athleteCode"]] = athlete["points"] + (REMAINING_STATE_EVENTS * FUTURE_STATE_EVENT_POINTS)

    enriched = []
    for athlete in athletes:
        code = athlete["athleteCode"]
        projected_max = projected_max_by_code.get(code)
        guaranteed = False

        if athlete in top_two:
            threats = 0
            for other in athletes:
                if other["athleteCode"] == code:
                    continue
                other_max = projected_max_by_code.get(other["athleteCode"])
                if other_max is None:
                    upper_bound = other["points"] + (REMAINING_STATE_EVENTS * FUTURE_STATE_EVENT_POINTS)
                    other_max = upper_bound if upper_bound >= athlete["points"] else other["points"]
                if other_max >= athlete["points"]:
                    threats += 1
            guaranteed = threats <= 1

        enriched.append(
            {
                **athlete,
                **({"stateProjectionMax": projected_max} if projected_max is not None else {}),
                **({"statePointComponents": components_by_code[code]} if code in components_by_code else {}),
                "stateTop2Guaranteed": guaranteed,
            }
        )
    return enriched


def scrape_target(target: dict[str, Any], timeout: int, retries: int) -> dict[str, Any]:
    started = time.time()
    error = None

    for attempt in range(retries + 1):
        try:
            html = fetch_html(target["url"], timeout)
            category_code = target.get("categoryCode") if target.get("categoryGroup") == "Tecnicas" else None
            athletes = parse_athletes(html, category_code)
            athletes = enrich_state_guarantees(target, html, athletes, timeout)
            return {
                **target,
                "status": "ok",
                "error": None,
                "athleteCount": len(athletes),
                "athletes": athletes,
                "durationMs": round((time.time() - started) * 1000),
                "attempts": attempt + 1,
            }
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(min(2 ** attempt, 10))

    return {
        **target,
        "status": "error",
        "error": error,
        "athleteCount": 0,
        "athletes": [],
        "durationMs": round((time.time() - started) * 1000),
        "attempts": retries + 1,
    }


def scrape_all(max_workers: int, timeout: int, retries: int) -> dict[str, Any]:
    sources = load_sources()
    targets = build_targets(sources)
    rankings = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_target = {
            executor.submit(scrape_target, target, timeout, retries): target for target in targets
        }
        for future in as_completed(future_to_target):
            result = future.result()
            rankings.append(result)
            marker = "OK" if result["status"] == "ok" else "ERRO"
            detail = f" após {result['attempts']} tentativas" if result.get("attempts", 1) > 1 else ""
            print(
                f"{marker} {result['regionalLabel']} "
                f"{result['categoryCode']} ({result['athleteCount']} atletas){detail}",
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
        default=int(os.environ.get("SCRAPER_TIMEOUT", "45")),
        help="Timeout por página em segundos.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=int(os.environ.get("SCRAPER_RETRIES", "3")),
        help="Número de novas tentativas por página com erro.",
    )
    args = parser.parse_args()

    data = scrape_all(max_workers=args.max_workers, timeout=args.timeout, retries=args.retries)
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
        for item in failures:
            print(
                f"- {item['regionalLabel']} {item['categoryCode']}: {item.get('error')}",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
