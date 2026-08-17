from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "scrape_rankings.py"
SPEC = importlib.util.spec_from_file_location("scrape_rankings", SCRIPT_PATH)
assert SPEC and SPEC.loader
scrape_rankings = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scrape_rankings)


def ranking_html(rows: list[tuple[str, str, str, str]]) -> str:
    body = "".join(
        "<tr>"
        f"<td>{position}</td><td>{code}</td><td>-</td><td>{name}</td>"
        f"<td>-</td><td>{category}</td><td>-</td><td>{points}</td>"
        "</tr>"
        for position, code, name, category, points in (
            (*row, "1000") for row in rows
        )
    )
    return f'<table class="clear_table">{body}</table>'


class CategoryChangeRuleTest(unittest.TestCase):
    def test_parses_supported_dates_and_empty_value(self) -> None:
        self.assertEqual(
            scrape_rankings.parse_category_change_date("17/08/2026"),
            date(2026, 8, 17),
        )
        self.assertEqual(
            scrape_rankings.parse_category_change_date("2026-08-18"),
            date(2026, 8, 18),
        )
        self.assertIsNone(scrape_rankings.parse_category_change_date(""))

    def test_e_category_keeps_only_eligible_promotion(self) -> None:
        html = ranking_html(
            [
                ("1", "100", "Atleta E", "BTFE"),
                ("2", "200", "Promovida recente", "BTFD"),
                ("3", "300", "Promovida antiga", "BTFD"),
                ("4", "400", "Categoria incorreta", "BTFC"),
            ]
        )
        athletes = scrape_rankings.parse_athletes(
            html,
            "BTFE",
            {("200", "BTFE")},
        )
        self.assertEqual([athlete["athleteCode"] for athlete in athletes], ["100", "200"])

    def test_c_and_b_categories_do_not_accept_promotions(self) -> None:
        html = ranking_html(
            [
                ("1", "100", "Atleta C", "BTFC"),
                ("2", "200", "Atleta B", "BTFB"),
            ]
        )
        athletes = scrape_rankings.parse_athletes(
            html,
            "BTFC",
            {("200", "BTFC")},
        )
        self.assertEqual([athlete["athleteCode"] for athlete in athletes], ["100"])


if __name__ == "__main__":
    unittest.main()
