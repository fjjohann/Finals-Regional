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
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener, urlopen


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "sources.json"
OUTPUT_PATH = ROOT / "docs" / "data" / "rankings.json"
CATEGORY_CHANGE_CACHE_PATH = ROOT / "data" / "category-change-cache.json"
USER_AGENT = "FinalsRegionalBot/1.0 (+https://github.com/fjjohann/Finals-Regional)"
STATE_RESULT_LIMIT = 8
FEDERATION_TECHNICAL_LABELS = {"A", "B", "C"}
CATEGORY_CHANGE_CUTOFF = date(2026, 8, 17)
PROMOTED_CATEGORY_BY_SOURCE = {
    "BTFE": "BTFD",
    "BTME": "BTMD",
    "BTFD": "BTFC",
    "BTMD": "BTMC",
}
FPT_ADMIN_BASE = "https://fpt.com.br/3213fpt023/"
FPT_ADMIN_LOGIN_URL = urljoin(FPT_ADMIN_BASE, "entra_gestao.asp")
FPT_ADMIN_PLAYERS_URL = urljoin(
    FPT_ADMIN_BASE,
    "pg7.asp?tabela=tenistas&descricao=Tenistas&tipopage=7&page=1",
)


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


class AdminPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.inputs: dict[str, str] = {}
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        if tag == "a" and attrs_dict.get("href"):
            self._link_href = attrs_dict["href"]
            self._link_text = []
        elif tag == "input" and attrs_dict.get("name"):
            self.inputs[attrs_dict["name"]] = attrs_dict.get("value", "")

    def handle_data(self, data: str) -> None:
        if self._link_href is not None:
            self._link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._link_href is not None:
            self.links.append((normalize_text("".join(self._link_text)), self._link_href))
            self._link_href = None
            self._link_text = []


class FptAdminClient:
    def __init__(self, username: str, password: str, timeout: int) -> None:
        self.username = username
        self.password = password
        self.timeout = timeout
        context = ssl._create_unverified_context()
        self.opener = build_opener(
            HTTPCookieProcessor(CookieJar()),
            HTTPSHandler(context=context),
        )

    def _open(self, url: str, data: dict[str, str] | None = None) -> str:
        body = urlencode(data, encoding="latin-1").encode("ascii") if data else None
        request = Request(
            url,
            data=body,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            charset = response.headers.get_content_charset() or "latin-1"
            return response.read().decode(charset, errors="replace")

    def login(self) -> None:
        html = self._open(
            FPT_ADMIN_LOGIN_URL,
            {"usuario": self.username, "senha": self.password},
        )
        normalized = normalize_text(html).lower()
        if "senha" in normalized and "usuario" in normalized:
            raise RuntimeError("Login no sistema interno da FPT não foi aceito.")

    def category_change_date(self, athlete_code: str) -> date | None:
        search_html = self._open(
            FPT_ADMIN_PLAYERS_URL,
            {"cboBusca": "tenistas.cdTenista", "pesquisa": athlete_code},
        )
        parser = AdminPageParser()
        parser.feed(search_html)
        edit_url = ""
        for text, href in parser.links:
            blob = f"{text} {href}"
            if "alterar" in blob.lower() and athlete_code in blob:
                edit_url = urljoin(FPT_ADMIN_BASE, href)
                break
        if not edit_url:
            raise RuntimeError(f"Cadastro FPT não localizado para o atleta {athlete_code}.")

        profile_parser = AdminPageParser()
        profile_parser.feed(self._open(edit_url))
        if "dtMudancaCategoriaBT" not in profile_parser.inputs:
            raise RuntimeError(
                f"Campo de mudança de categoria não localizado para o atleta {athlete_code}."
            )
        return parse_category_change_date(profile_parser.inputs["dtMudancaCategoriaBT"])


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def parse_category_change_date(value: str) -> date | None:
    cleaned = normalize_text(value)
    if not cleaned:
        return None
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"Data de mudança de categoria inválida: {cleaned}")


def category_change_cache_key(athlete_code: str, current_category: str) -> str:
    return f"{athlete_code}|{current_category}"


def load_category_change_cache() -> dict[str, Any]:
    if not CATEGORY_CHANGE_CACHE_PATH.exists():
        return {"cutoffDate": CATEGORY_CHANGE_CUTOFF.isoformat(), "athletes": {}}
    data = json.loads(CATEGORY_CHANGE_CACHE_PATH.read_text(encoding="utf-8"))
    if data.get("cutoffDate") != CATEGORY_CHANGE_CUTOFF.isoformat():
        raise RuntimeError("A data de corte do cache de mudanças de categoria não confere.")
    data.setdefault("athletes", {})
    return data


def save_category_change_cache(data: dict[str, Any]) -> None:
    CATEGORY_CHANGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CATEGORY_CHANGE_CACHE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(CATEGORY_CHANGE_CACHE_PATH)


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


def parse_athletes(
    html: str,
    category_code: str | None = None,
    eligible_promotions: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    parser = RankingTableParser()
    parser.feed(html)
    athletes = []
    expected_category = normalize_text(category_code or "").upper()

    for cells in parser.rows:
        if len(cells) < 8:
            continue
        if not cells[0].isdigit() or not cells[1].isdigit():
            continue
        athlete_code = cells[1]
        athlete_category = normalize_text(cells[5]).upper()
        if expected_category and athlete_category != expected_category:
            expected_promoted_category = PROMOTED_CATEGORY_BY_SOURCE.get(expected_category)
            is_eligible_promotion = (
                expected_promoted_category == athlete_category
                and eligible_promotions is not None
                and (athlete_code, expected_category) in eligible_promotions
            )
            if not is_eligible_promotion:
                continue
        points_text = re.sub(r"[^\d]", "", cells[7])
        athletes.append(
            {
                "position": int(cells[0]),
                "athleteCode": athlete_code,
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


def future_state_event_points(_target: dict[str, Any]) -> list[int]:
    return []


def projected_state_points(components: list[int], event_points: list[int]) -> int:
    return sum(sorted([*components, *event_points], reverse=True)[:STATE_RESULT_LIMIT])


def future_state_points_total(event_points: list[int]) -> int:
    return sum(event_points)


def has_federation_spots(target: dict[str, Any]) -> bool:
    if target.get("categoryGroup") != "Tecnicas":
        return True
    return target.get("categoryLabel") in FEDERATION_TECHNICAL_LABELS


def state_qualification_limit(target: dict[str, Any]) -> int:
    return 6 if target.get("categoryGroup") == "Tecnicas" else 4


def enrich_state_guarantees(
    target: dict[str, Any],
    html: str,
    athletes: list[dict[str, Any]],
    timeout: int,
) -> list[dict[str, Any]]:
    if target.get("rankingScope") != "state" or not athletes:
        return athletes
    event_points = future_state_event_points(target)
    if not has_federation_spots(target):
        finals_only_is_complete = target.get("categoryLabel") == "E" and not event_points
        return [
            {
                **athlete,
                "stateProjectionMax": athlete["points"],
                "stateTop2Guaranteed": False,
                "stateFinalsGuaranteed": finals_only_is_complete and index < 4,
            }
            for index, athlete in enumerate(athletes)
        ]

    projected_max_by_code: dict[str, int] = (
        {athlete["athleteCode"]: athlete["points"] for athlete in athletes}
        if not event_points
        else {}
    )
    components_by_code: dict[str, list[int]] = {}
    list_index_by_code = {athlete["athleteCode"]: index for index, athlete in enumerate(athletes)}
    qualification_limit = state_qualification_limit(target)
    top_two = athletes[:2]
    state_qualified = athletes[:qualification_limit]
    thresholds = [athlete["points"] for athlete in state_qualified]
    contenders = {
        index
        for threshold in thresholds
        for index, athlete in enumerate(athletes)
        if athlete["points"] + future_state_points_total(event_points) >= threshold
    }

    if event_points:
        tennis_ids = parse_tennis_ids(html)
        if len(tennis_ids) < len(athletes):
            return athletes

        suffix = parse_points_endpoint_suffix(html)
        ranking_id = target["rankingId"]
        base_url = "/".join(target["url"].split("/")[:3])
        for index in sorted(contenders):
            athlete = athletes[index]
            tennis_id = tennis_ids[index]
            points_url = f"{base_url}/Ranking/PontosPartial/{target['url'].split('/')[-4]}/bt-estadual/{ranking_id}/{tennis_id}{suffix}"
            try:
                point_html = fetch_html(points_url, timeout)
                components = parse_point_components(point_html, athlete["points"])
                components_by_code[athlete["athleteCode"]] = components
                projected_max_by_code[athlete["athleteCode"]] = projected_state_points(components, event_points)
            except (HTTPError, URLError, TimeoutError, OSError):
                projected_max_by_code[athlete["athleteCode"]] = athlete["points"] + future_state_points_total(event_points)

    enriched = []
    for athlete in athletes:
        code = athlete["athleteCode"]
        athlete_index = list_index_by_code[code]
        projected_max = projected_max_by_code.get(code)
        federation_guaranteed = False
        finals_guaranteed = False

        if athlete in top_two:
            threats = 0
            for other in athletes:
                if other["athleteCode"] == code:
                    continue
                other_max = projected_max_by_code.get(other["athleteCode"])
                if other_max is None:
                    upper_bound = other["points"] + future_state_points_total(event_points)
                    other_max = upper_bound if upper_bound >= athlete["points"] else other["points"]
                other_is_ahead_on_tie = list_index_by_code[other["athleteCode"]] < athlete_index
                if other_max > athlete["points"] or (other_max == athlete["points"] and other_is_ahead_on_tie):
                    threats += 1
            federation_guaranteed = threats <= 1

        if athlete in state_qualified[2:]:
            threats = 0
            for other in athletes:
                if other["athleteCode"] == code:
                    continue
                other_max = projected_max_by_code.get(other["athleteCode"])
                if other_max is None:
                    upper_bound = other["points"] + future_state_points_total(event_points)
                    other_max = upper_bound if upper_bound >= athlete["points"] else other["points"]
                other_is_ahead_on_tie = list_index_by_code[other["athleteCode"]] < athlete_index
                if other_max > athlete["points"] or (other_max == athlete["points"] and other_is_ahead_on_tie):
                    threats += 1

            cannot_reach_top_two = (
                projected_max is not None
                and len(top_two) == 2
                and projected_max <= top_two[1]["points"]
            )
            finals_guaranteed = threats < qualification_limit and cannot_reach_top_two

        enriched.append(
            {
                **athlete,
                **({"stateProjectionMax": projected_max} if projected_max is not None else {}),
                **({"statePointComponents": components_by_code[code]} if code in components_by_code else {}),
                "stateTop2Guaranteed": federation_guaranteed,
                "stateFinalsGuaranteed": finals_guaranteed,
            }
        )
    return enriched


def query_category_change_dates(
    candidates: list[dict[str, str]],
    username: str,
    password: str,
    timeout: int,
    max_workers: int,
) -> dict[str, date | None]:
    if not candidates:
        return {}
    if not username or not password:
        raise RuntimeError(
            "Há atletas promovidos novos, mas FPT_USERNAME/FPT_PASSWORD não estão configurados."
        )

    worker_count = max(1, min(max_workers, 4, len(candidates)))
    chunks = [candidates[index::worker_count] for index in range(worker_count)]

    def query_chunk(chunk: list[dict[str, str]]) -> dict[str, date | None]:
        client = FptAdminClient(username, password, timeout)
        client.login()
        result: dict[str, date | None] = {}
        for candidate in chunk:
            result[candidate["cacheKey"]] = client.category_change_date(
                candidate["athleteCode"]
            )
        return result

    resolved: dict[str, date | None] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(query_chunk, chunk) for chunk in chunks if chunk]
        for future in as_completed(futures):
            resolved.update(future.result())
    return resolved


def resolve_eligible_promotions(
    rankings: list[dict[str, Any]],
    timeout: int,
    max_workers: int,
) -> set[tuple[str, str]]:
    candidates_by_key: dict[str, dict[str, str]] = {}
    source_categories_by_key: dict[str, set[str]] = {}

    for ranking in rankings:
        if ranking.get("status") != "ok":
            continue
        source_category = ranking.get("categoryCode", "")
        promoted_category = PROMOTED_CATEGORY_BY_SOURCE.get(source_category)
        if not promoted_category:
            continue
        for athlete in parse_athletes(ranking["_html"]):
            if athlete["categoryCode"] != promoted_category:
                continue
            cache_key = category_change_cache_key(
                athlete["athleteCode"], athlete["categoryCode"]
            )
            candidates_by_key.setdefault(
                cache_key,
                {
                    "cacheKey": cache_key,
                    "athleteCode": athlete["athleteCode"],
                    "currentCategory": athlete["categoryCode"],
                },
            )
            source_categories_by_key.setdefault(cache_key, set()).add(source_category)

    cache = load_category_change_cache()
    cache_athletes = cache["athletes"]
    missing = [
        candidate
        for cache_key, candidate in candidates_by_key.items()
        if cache_key not in cache_athletes
    ]
    if missing:
        print(
            f"Consultando data de mudança de categoria de {len(missing)} atletas novos...",
            file=sys.stderr,
        )
        resolved = query_category_change_dates(
            missing,
            os.environ.get("FPT_USERNAME", ""),
            os.environ.get("FPT_PASSWORD", ""),
            timeout,
            max_workers,
        )
        checked_at = datetime.now(timezone.utc).isoformat()
        for candidate in missing:
            change_date = resolved[candidate["cacheKey"]]
            cache_athletes[candidate["cacheKey"]] = {
                "athleteCode": candidate["athleteCode"],
                "currentCategory": candidate["currentCategory"],
                "changeDate": change_date.isoformat() if change_date else None,
                "checkedAt": checked_at,
            }
        save_category_change_cache(cache)

    eligible: set[tuple[str, str]] = set()
    for cache_key, source_categories in source_categories_by_key.items():
        cached_date = cache_athletes[cache_key].get("changeDate")
        if not cached_date:
            continue
        change_date = date.fromisoformat(cached_date)
        if change_date < CATEGORY_CHANGE_CUTOFF:
            continue
        athlete_code = candidates_by_key[cache_key]["athleteCode"]
        eligible.update((athlete_code, source) for source in source_categories)

    print(
        f"Promoções elegíveis após {CATEGORY_CHANGE_CUTOFF.strftime('%d/%m/%Y')}: "
        f"{len(eligible)}.",
        file=sys.stderr,
    )
    return eligible


def scrape_target(target: dict[str, Any], timeout: int, retries: int) -> dict[str, Any]:
    started = time.time()
    error = None

    for attempt in range(retries + 1):
        try:
            html = fetch_html(target["url"], timeout)
            return {
                **target,
                "status": "ok",
                "error": None,
                "_html": html,
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

    eligible_promotions = resolve_eligible_promotions(rankings, timeout, max_workers)
    for result in rankings:
        if result["status"] == "ok":
            html = result.pop("_html")
            category_code = (
                result.get("categoryCode")
                if result.get("categoryGroup") == "Tecnicas"
                else None
            )
            athletes = parse_athletes(html, category_code, eligible_promotions)
            athletes = enrich_state_guarantees(result, html, athletes, timeout)
            result["athleteCount"] = len(athletes)
            result["athletes"] = athletes

        marker = "OK" if result["status"] == "ok" else "ERRO"
        detail = (
            f" após {result['attempts']} tentativas"
            if result.get("attempts", 1) > 1
            else ""
        )
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
