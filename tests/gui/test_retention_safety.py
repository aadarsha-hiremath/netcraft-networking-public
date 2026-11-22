# ruff: noqa: E402
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from pytestqt.qtbot import QtBot

from netcraft.gui import settings_page
from netcraft.gui.preferences import Preferences
from netcraft.gui.widgets import pick
from netcraft.gui.window import StudioWindow
from netcraft.monitoring.history import RetentionChanged, open_history
from netcraft.monitoring.telemetry import (
    MonitorProtocol,
    Observation,
    Run,
    RunStatus,
    Sample,
    SampleStatus,
    utc_text,
)

ANCHOR = datetime(2026, 11, 21, 9, tzinfo=UTC)
CUTOFF = ANCHOR - timedelta(days=1)
RETAINED = ("tie-b", "tie-c", "newest")


def seed_retention(path: Path) -> None:
    recent = CUTOFF + timedelta(hours=2)
    runs = {
        "age-expired": (
            ("aged-0", CUTOFF - timedelta(days=1)),
            ("aged-1", CUTOFF - timedelta(microseconds=1)),
        ),
        "count-expired": (
            ("boundary", CUTOFF),
            ("recent-low", CUTOFF + timedelta(hours=1)),
        ),
        "mixed": (("aged-2", CUTOFF - timedelta(days=1)), ("tie-a", recent), ("tie-c", recent)),
        "survivor": (("tie-b", recent), ("newest", recent + timedelta(hours=1))),
    }
    with open_history(path) as history:
        for run_id, items in runs.items():
            started, ended = items[0][1], items[-1][1]
            history.add_run(
                Run(run_id, RunStatus.RUNNING, started, None, 1, len(items), 1, 1, None)
            )
            for index, (sample_id, when) in enumerate(items):
                protocol = MonitorProtocol.HTTP if run_id == "survivor" else MonitorProtocol.TCP
                is_http = protocol == MonitorProtocol.HTTP
                history.insert_sample(
                    Sample(
                        sample_id,
                        run_id,
                        index,
                        protocol,
                        "http://127.0.0.1:8787/" if is_http else "127.0.0.1:9000",
                        when,
                        when,
                        Observation(
                            SampleStatus.SUCCESS,
                            latency_ns=(index + 1) * 1_000_000,
                            http_status=200 if is_http else None,
                        ),
                    )
                )
            history.finish_run(run_id, RunStatus.COMPLETED, ended)


def test_retention_preview_cancel_apply_refresh_and_repeat(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "history.sqlite3"
    prefs = tmp_path / "preferences.ini"
    seed_retention(database)
    monkeypatch.setattr(settings_page, "recent_cutoff", lambda days: ANCHOR - timedelta(days=days))
    window = StudioWindow(database, QSettings(str(prefs), QSettings.Format.IniFormat))
    qtbot.addWidget(window)
    window.show()
    settings = window.settings
    window.show_page("Settings")
    settings.age.setValue(1)
    settings.count.setValue(3)
    settings.timeout.setValue(4.25)
    pick(settings.window_picker, 10080)
    QTest.mouseClick(settings.save, Qt.MouseButton.LeftButton)
    expected_settings = Preferences(
        window_minutes=10080, timeout=4.25, retention_days=1, retention_count=3
    )
    assert window.session.preferences == expected_settings
    saved_settings = prefs.read_bytes()
    with open_history(database) as history:
        all_runs, all_samples = tuple(history.runs(20)), tuple(history.samples())
    assert (len(all_runs), len(all_samples)) == (4, 9)

    # Provide the click from the modal event loop, so queued signals cannot block the test.
    def apply(answer: QMessageBox.StandardButton, expected: str, result: str) -> None:
        observed: list[tuple[str, str, bool]] = []

        def answer_dialog() -> None:
            prompt = QApplication.activeModalWidget()
            if isinstance(prompt, QMessageBox):
                observed.append(
                    (
                        prompt.windowTitle(),
                        prompt.text(),
                        prompt.defaultButton() == prompt.button(QMessageBox.StandardButton.No),
                    )
                )
                QTest.mouseClick(prompt.button(answer), Qt.MouseButton.LeftButton)
            else:
                timer.start(10)

        timer = QTimer(window)
        timer.setSingleShot(True)
        timer.timeout.connect(answer_dialog)
        timer.start(10)
        try:
            QTest.mouseClick(settings.prune, Qt.MouseButton.LeftButton)
            qtbot.waitUntil(lambda: settings.message.text() == result, timeout=5000)
        finally:
            timer.stop()
        assert len(observed) == 1
        title, description, default_no = observed[0]
        assert title == "Apply retention?"
        assert description.startswith(expected)
        assert default_no

    apply(
        QMessageBox.StandardButton.No, "Remove 6 samples and 2 empty runs?", "Retention cancelled."
    )
    with open_history(database) as history:
        assert (tuple(history.runs(20)), tuple(history.samples())) == (all_runs, all_samples)
    apply(
        QMessageBox.StandardButton.Yes,
        "Remove 6 samples and 2 empty runs?",
        "Removed 6 samples and 2 empty runs.",
    )
    assert prefs.read_bytes() == saved_settings
    with open_history(database) as history:
        assert tuple(history.runs(20)) == tuple(
            run for run in all_runs if run.run_id in {"mixed", "survivor"}
        )
        assert tuple(history.samples()) == tuple(
            sample for sample in all_samples if sample.sample_id in RETAINED
        )
        assert history.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert history.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert window.overview.snapshot is None and window.analytics.snapshot is None
    window.show_page("Overview")
    qtbot.waitUntil(lambda: window.overview.snapshot is not None)
    assert window.overview.snapshot is not None
    assert {run.run_id for run in window.overview.snapshot.recent_runs} == {"mixed", "survivor"}
    window.show_page("History")
    qtbot.waitUntil(lambda: len(window.history.table.rows.rows) == 2)
    assert {row.key for row in window.history.table.rows.rows} == {"mixed", "survivor"}
    window.show_page("Analytics")
    pick(window.analytics.window_picker, 0)
    qtbot.waitUntil(lambda: window.analytics.snapshot is not None)
    assert window.analytics.snapshot is not None
    assert window.analytics.snapshot.overall.samples == 3
    window.show_page("Settings")
    apply(
        QMessageBox.StandardButton.Yes,
        "Remove 0 samples and 0 empty runs?",
        "Removed 0 samples and 0 empty runs.",
    )
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    reopened = StudioWindow(database, QSettings(str(prefs), QSettings.Format.IniFormat))
    qtbot.addWidget(reopened)
    reopened.show()
    assert reopened.session.preferences == expected_settings
    reopened.show_page("History")
    qtbot.waitUntil(lambda: len(reopened.history.table.rows.rows) == 2)
    assert {row.key for row in reopened.history.table.rows.rows} == {"mixed", "survivor"}


def test_changed_retention_plan_is_rejected_even_with_same_counts(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite3"
    seed_retention(database)
    with open_history(database) as history:
        shown = history.preview_retention(before=CUTOFF, keep_samples=3)
        assert history.preview_retention(before=CUTOFF, keep_samples=None).samples == 3
        with open_history(database) as writer:
            writer.connection.execute(
                "UPDATE samples SET observed_at = ? WHERE sample_id = 'tie-a'",
                (utc_text(CUTOFF + timedelta(hours=4)),),
            )
        updated = history.preview_retention(before=CUTOFF, keep_samples=3)
        assert (shown.samples, shown.runs) == (updated.samples, updated.runs) == (6, 2)
        assert shown.fingerprint != updated.fingerprint
        all_rows = tuple(history.samples()), tuple(history.runs(20))
        with pytest.raises(RetentionChanged, match="Preview retention again"):
            history.prune(before=CUTOFF, keep_samples=3, expected=shown)
        assert (tuple(history.samples()), tuple(history.runs(20))) == all_rows
