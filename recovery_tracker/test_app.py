"""Tests for recovery_tracker data saving and report generation logic.

Run from the recovery_tracker directory:  python -m pytest test_app.py -v
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from modules import storage
from modules.communication import analyze_draft
from modules.reports import DISCLAIMER, build_report, write_report
from modules.storage import DailyLog, Incident


def _ts(days_ago: int = 0) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")


def make_log(days_ago=0, fog=5, stress=5, boundaries=True, impulses=False) -> DailyLog:
    return DailyLog(
        timestamp=_ts(days_ago),
        brain_fog=fog,
        stress=stress,
        boundaries_kept=boundaries,
        impulses_occurred=impulses,
    )


def make_incident(days_ago=0, trigger="boredom scroll", state="boredom", passed=True) -> Incident:
    return Incident(
        timestamp=_ts(days_ago),
        trigger=trigger,
        emotional_state=state,
        impulse_passed=passed,
    )


class TestStorage:
    def test_ensure_data_dir_creates_directory(self, tmp_path):
        target = tmp_path / "data"
        assert not target.exists()
        result = storage.ensure_data_dir(target)
        assert result.exists() and result.is_dir()

    def test_save_and_load_incident(self, tmp_path):
        incident = make_incident()
        storage.save_incident(incident, tmp_path)
        loaded = storage.load_incidents(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].trigger == "boredom scroll"
        assert loaded[0].impulse_passed is True

    def test_save_and_load_daily_log(self, tmp_path):
        entry = make_log(fog=7, stress=4)
        storage.save_daily_log(entry, tmp_path)
        loaded = storage.load_daily_logs(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].brain_fog == 7
        assert loaded[0].stress == 4

    def test_appending_preserves_existing_records(self, tmp_path):
        for i in range(3):
            storage.save_incident(make_incident(trigger=f"trigger-{i}"), tmp_path)
        loaded = storage.load_incidents(tmp_path)
        assert [i.trigger for i in loaded] == ["trigger-0", "trigger-1", "trigger-2"]

    def test_saved_file_is_valid_json_list(self, tmp_path):
        storage.save_daily_log(make_log(), tmp_path)
        raw = json.loads((tmp_path / storage.DAILY_LOGS_FILE).read_text())
        assert isinstance(raw, list) and len(raw) == 1

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert storage.load_incidents(tmp_path) == []
        assert storage.load_daily_logs(tmp_path) == []

    def test_load_corrupt_file_returns_empty(self, tmp_path):
        storage.ensure_data_dir(tmp_path)
        (tmp_path / storage.INCIDENTS_FILE).write_text("{not valid json!")
        assert storage.load_incidents(tmp_path) == []

    def test_load_empty_file_returns_empty(self, tmp_path):
        storage.ensure_data_dir(tmp_path)
        (tmp_path / storage.DAILY_LOGS_FILE).write_text("")
        assert storage.load_daily_logs(tmp_path) == []

    def test_malformed_rows_are_skipped(self, tmp_path):
        storage.ensure_data_dir(tmp_path)
        good = make_incident().model_dump()
        (tmp_path / storage.INCIDENTS_FILE).write_text(json.dumps([good, {"nonsense": True}]))
        assert len(storage.load_incidents(tmp_path)) == 1

    def test_daily_log_validates_scale_bounds(self):
        with pytest.raises(Exception):
            DailyLog(
                timestamp=_ts(),
                brain_fog=11,
                stress=5,
                boundaries_kept=True,
                impulses_occurred=False,
            )


class TestReports:
    def test_report_contains_disclaimer(self):
        report = build_report([], [], days=7)
        assert DISCLAIMER in report

    def test_empty_data_report(self):
        report = build_report([], [], days=7)
        assert "No data was recorded" in report

    def test_averages_and_compliance(self):
        logs = [
            make_log(days_ago=0, fog=4, stress=6, boundaries=True),
            make_log(days_ago=1, fog=6, stress=8, boundaries=False),
        ]
        report = build_report(logs, [], days=7)
        assert "5.0" in report  # average fog
        assert "7.0" in report  # average stress
        assert "50%" in report  # compliance rate
        assert "1/2 days" in report

    def test_impulse_frequency_and_triggers(self):
        incidents = [
            make_incident(days_ago=0, trigger="notification", state="stress"),
            make_incident(days_ago=1, trigger="notification", state="boredom", passed=False),
        ]
        report = build_report([], incidents, days=7)
        assert "notification: 2×" in report
        assert "stress: 1×" in report
        assert "1/2" in report  # impulses passed

    def test_window_filtering_excludes_old_entries(self):
        logs = [make_log(days_ago=0), make_log(days_ago=20)]
        report = build_report(logs, [], days=7)
        assert "**1** of 7 days" in report

    def test_30_day_window_includes_more(self):
        logs = [make_log(days_ago=0), make_log(days_ago=20)]
        report = build_report(logs, [], days=30)
        assert "**2** of 30 days" in report

    def test_write_report_creates_dated_file(self, tmp_path):
        storage.save_daily_log(make_log(), tmp_path)
        path = write_report(days=7, data_dir=tmp_path, output_dir=tmp_path)
        assert path.name == f"report_{date.today().isoformat()}.md"
        assert path.exists()
        assert DISCLAIMER in path.read_text()

    def test_bad_timestamps_do_not_crash_report(self):
        log = make_log()
        log.timestamp = "not-a-date"
        report = build_report([log], [], days=7)
        assert "No data was recorded" in report


class TestCommunication:
    def test_good_message_passes_all_criteria(self):
        draft = (
            "I broke my boundary last night and I take full responsibility for it. "
            "I understand this hurt you and damaged your trust, and you deserve honesty. "
            "Tonight I will show you my screen-time report, and from now on I'll check in daily."
        )
        result = analyze_draft(draft)
        assert result["score"] == 4

    def test_defensive_message_flagged(self):
        draft = "It's not a big deal, you always overreact. I was just tired because I was stressed."
        result = analyze_draft(draft)
        criteria = {c["name"]: c for c in result["criteria"]}
        assert not criteria["Avoids defensive or minimizing language"]["passed"]
        assert not criteria["Takes full accountability without excuses"]["passed"]

    def test_missing_empathy_gets_suggestion(self):
        draft = "I was wrong and I take responsibility. I will do better and from now on I'll be honest."
        result = analyze_draft(draft)
        empathy = next(c for c in result["criteria"] if "empathy" in c["name"].lower())
        assert not empathy["passed"]
        assert empathy["suggestions"]

    def test_no_action_gets_suggestion(self):
        draft = "I'm sorry. I was wrong. I understand this hurt you deeply and your trust is broken."
        result = analyze_draft(draft)
        action = next(c for c in result["criteria"] if "actionable" in c["name"].lower())
        assert not action["passed"]
