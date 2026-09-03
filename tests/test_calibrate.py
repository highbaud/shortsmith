"""Tests for scripts/calibrate.py: joining post analytics back to the ledger
and turning them into clip-picking calibration. All offline."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import calibrate  # noqa: E402


def test_rows_are_found_at_any_nesting_and_keys_match_loosely() -> None:
    raw = {"data": {"instagram": [
        {"publishedDate": "2026-06-02T09:00:00", "Views": "1,200", "saves": 30,
         "likes": 80, "comments": 4, "shares": 12, "caption": "Tether props up bitcoin"},
        {"notes": "no metrics here"},
    ]}}
    rows = calibrate.normalize_analytics(raw)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-06-02"
    assert rows[0]["metrics"]["views"] == 1200.0
    assert rows[0]["metrics"]["saves"] == 30.0
    assert rows[0]["title"] == "Tether props up bitcoin"


def test_epoch_dates_are_understood() -> None:
    assert calibrate._parse_date(1780000000) == "2026-05-28"
    assert calibrate._parse_date("1780000000000") == "2026-05-28"
    assert calibrate._parse_date("not a date") == ""


def test_score_weights_intent_over_reach() -> None:
    base = {"views": 0, "reach": 0, "likes": 0, "comments": 0, "shares": 0, "saves": 0}
    assert calibrate._score({**base, "saves": 1}) > calibrate._score({**base, "likes": 5})
    assert calibrate._score({**base, "shares": 1}) > calibrate._score({**base, "comments": 2})


def ledger(*entries: tuple[str, str, str]) -> list[dict]:
    return [{"slug": slug, "topic": slug.replace("-", " "), "brand": "jc", "date": date}
            for slug, _brand, date in entries]


def row(date: str, **metrics) -> dict:
    m = {"views": 0.0, "reach": 0.0, "likes": 0.0, "comments": 0.0, "shares": 0.0, "saves": 0.0}
    m.update({k: float(v) for k, v in metrics.items()})
    return {"date": date, "title": "", "network": "instagram", "metrics": m}


def test_analytics_attach_to_the_nearest_ledger_entry_within_tolerance() -> None:
    led = ledger(("tether-props-up-bitcoin", "jc", "2026-06-02"),
                 ("grow-vs-maintain-wealth", "jc", "2026-06-05"))
    scored = calibrate.match_and_score(led, [
        row("2026-06-02", views=1000, saves=10),
        row("2026-06-03", views=500),          # one day off: still the June 2 post
        row("2026-06-09", views=9999),         # four days from anything: ignored
    ])
    assert [s["slug"] for s in scored] == ["tether-props-up-bitcoin"]
    assert scored[0]["matched"] == 2
    assert scored[0]["metrics"]["views"] == 1500.0


def test_topics_come_back_best_first_and_unmatched_ones_are_left_out() -> None:
    led = ledger(("a-weak-one", "jc", "2026-06-01"), ("a-strong-one", "jc", "2026-06-10"),
                 ("never-measured", "jc", "2026-07-01"))
    scored = calibrate.match_and_score(led, [row("2026-06-01", views=100),
                                             row("2026-06-10", views=100, saves=50)])
    assert [s["slug"] for s in scored] == ["a-strong-one", "a-weak-one"]


def test_emit_writes_the_three_calibration_files(tmp_path: Path) -> None:
    scored = [{"slug": f"topic-{i}", "topic": f"topic {i}", "score": 100 - i,
               "metrics": {"views": 100 - i, "saves": 1, "shares": 0, "comments": 0,
                           "likes": 0, "reach": 0}, "matched": 1, "dates": []}
              for i in range(20)]
    calibrate.emit(scored, tmp_path, top_n=5)
    top = json.loads((tmp_path / "top_topics.json").read_text(encoding="utf-8"))
    weak = json.loads((tmp_path / "weak_topics.json").read_text(encoding="utf-8"))
    assert top == [f"topic {i}" for i in range(5)]
    assert weak == [f"topic {i}" for i in range(19, 14, -1)]
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Top performers" in report and "Underperformers" in report


def test_ledger_parser_reads_the_real_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger_file = tmp_path / "scheduled_ledger.json"
    ledger_file.write_text(json.dumps({
        "jc": {"source__short-03-grow-vs-maintain-wealth-skill": [{"date": "2026-06-02T09:00:00"}]},
        "ff": {"not-a-short-key": [{"date": "2026-06-02T09:00:00"}]},
    }), encoding="utf-8")
    monkeypatch.setattr(calibrate, "LEDGER", ledger_file)
    entries = calibrate.load_ledger()
    assert entries == [{"slug": "grow-vs-maintain-wealth-skill",
                        "topic": "grow vs maintain wealth skill", "brand": "jc",
                        "date": "2026-06-02"}]
