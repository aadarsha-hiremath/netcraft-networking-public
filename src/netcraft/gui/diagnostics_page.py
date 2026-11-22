"""Single-run diagnostic form and safe result actions."""

import json
from collections.abc import Callable

from PySide6.QtWidgets import QApplication, QHBoxLayout, QPlainTextEdit

from netcraft.core.errors import NetworkFailure
from netcraft.gui.data import save_diagnostic
from netcraft.gui.diagnostic_data import DiagnosticRequest, DiagnosticView, execute_diagnostic
from netcraft.gui.editor import ProbeEditor
from netcraft.gui.session import Session
from netcraft.gui.theme import tone
from netcraft.gui.widgets import Card, Page, button, choose_destination, controls, label, latency
from netcraft.gui.workers import JobControl, JobResult

_GOOD = {"Success", "HTTP response", "Empty answer"}


class DiagnosticsPage(Page):
    def __init__(
        self,
        session: Session,
        perform: Callable[[DiagnosticRequest, JobControl], DiagnosticView] = execute_diagnostic,
    ) -> None:
        super().__init__("Inspect an endpoint", "Run one diagnostic against a target you specify.")
        self.session, self.perform = session, perform
        self.result: DiagnosticView | None = None
        self.editor = ProbeEditor(session.preferences.timeout)
        self.start = button("Start diagnostic", primary=True)
        self.cancel = button("Cancel")
        self.clear = button("Clear result")
        self.copy = button("Copy sanitized result")
        self.save = button("Save JSON…")
        self.details_toggle = button("Technical details")
        self.details_toggle.setCheckable(True)
        self.state = label("Ready for a diagnostic", "panelTitle", wrap=True)
        self.context = label("", "muted", wrap=True)
        self.explanation = label(
            "Completed results stay associated with the submitted target.", wrap=True
        )
        self.error = label("", wrap=True)
        tone(self.error, "error")
        self.timings = label("", wrap=True)
        self.metadata = label("", wrap=True)
        self.details = QPlainTextEdit()
        self.details.setAccessibleName("Sanitized diagnostic details")
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(200)
        self.details.hide()

        form = Card("Target")
        form.body.addWidget(self.editor)
        form.body.addWidget(self.error)
        form.body.addLayout(controls(self.start, self.cancel))
        form.body.addStretch(1)
        result = Card("Result")
        for widget in (self.state, self.context, self.explanation, self.timings, self.metadata):
            result.body.addWidget(widget)
        result.body.addLayout(controls(self.details_toggle, self.clear))
        result.body.addWidget(self.details)
        result.body.addLayout(controls(self.copy, self.save))
        result.body.addStretch(1)
        split = QHBoxLayout()
        split.setSpacing(14)
        split.addWidget(form, 5)
        split.addWidget(result, 6)
        self.content.addLayout(split)
        self.content.addStretch(1)

        self.start.clicked.connect(self.request)
        self.cancel.clicked.connect(self.stop)
        self.clear.clicked.connect(self.reset_result)
        self.copy.clicked.connect(self.copy_result)
        self.save.clicked.connect(self.save_result)
        self.details_toggle.toggled.connect(self.details.setVisible)
        self.session.preferences_changed.connect(self.refresh_preferences)
        self._buttons(False)

    def _buttons(self, busy: bool) -> None:
        self.start.setEnabled(not busy)
        self.cancel.setEnabled(busy)
        for control in (self.clear, self.copy, self.save, self.details_toggle):
            control.setEnabled(self.result is not None and not busy)

    def refresh_preferences(self) -> None:
        self.editor.timeout.setText(f"{self.session.preferences.timeout:g}")
        if self.result is not None:
            self._render(self.result)

    def request(self) -> None:
        self.error.clear()
        try:
            request = self.editor.selection()
        except (NetworkFailure, ValueError) as exc:
            self.error.setText(str(exc))
            self.session.notify("Check diagnostic input.", "error")
            return
        if not self.session.jobs.submit(
            "diagnostic",
            lambda control: self.perform(request, control),
            self._done,
        ):
            return
        self.result = None
        self.state.setText("Running")
        tone(self.state, "warn")
        self.context.setText(f"{request.target.protocol.value.upper()} · {request.target.label}")
        self.explanation.setText("Waiting for the selected endpoint.")
        self.timings.clear()
        self.metadata.clear()
        self.details.clear()
        self.details_toggle.setChecked(False)
        self._buttons(True)
        self.session.notify("Diagnostic running")

    def stop(self) -> None:
        self.session.jobs.cancel("diagnostic")
        self.cancel.setEnabled(False)
        self.state.setText("Cancelling…")
        self.session.notify("Waiting for the diagnostic to stop.", "warn")

    def _done(self, outcome: JobResult[DiagnosticView]) -> None:
        self._buttons(False)
        if outcome.value is None:
            self.state.setText("Cancelled" if outcome.cancelled else "Failed")
            self.explanation.setText(outcome.error or "The diagnostic was cancelled.")
            tone(self.state, "warn" if outcome.cancelled else "error")
            self.session.notify(
                self.state.text(), "warn" if outcome.cancelled else "error", remember=True
            )
            return
        self.result = outcome.value
        self._render(outcome.value)
        self._buttons(False)
        self.session.notify(
            f"Diagnostic · {outcome.value.outcome}",
            "good" if outcome.value.outcome in _GOOD else "warn",
            remember=True,
        )

    def _render(self, result: DiagnosticView) -> None:
        self.state.setText(result.outcome)
        tone(
            self.state,
            "good" if result.outcome in _GOOD else ("warn" if result.error is None else "error"),
        )
        self.context.setText(f"{result.protocol.value.upper()} · {result.target}")
        self.explanation.setText(result.explanation)
        timing_rows = [
            f"{key.replace('_', ' ').title()}  {latency(value, self.session.preferences.unit)}"
            for key, value in result.timing_ns.items()
            if value is not None
        ]
        self.timings.setText("  ·  ".join(timing_rows) if timing_rows else "Timing unavailable")
        facts = []
        for key in (
            "response_code",
            "resolver",
            "record_count",
            "http_status",
            "resolved_address",
            "redirect",
        ):
            value = result.metadata.get(key)
            if value is not None:
                facts.append(f"{key.replace('_', ' ').title()}  {value}")
        if result.error is not None:
            facts.append(f"Error category  {result.error}")
        self.metadata.setText("  ·  ".join(facts))
        self.details.setPlainText(json.dumps(result.sanitized(), ensure_ascii=True, indent=2))

    def reset_result(self) -> None:
        self.result = None
        self.state.setText("Ready for a diagnostic")
        tone(self.state, "")
        self.context.clear()
        self.explanation.setText("Completed results stay associated with the submitted target.")
        self.timings.clear()
        self.metadata.clear()
        self.details.clear()
        self.details_toggle.setChecked(False)
        self._buttons(False)

    def copy_result(self) -> None:
        if self.result is not None:
            QApplication.clipboard().setText(self.details.toPlainText())
            self.session.notify("Sanitized result copied.", "good")

    def save_result(self) -> None:
        result = self.result
        if result is None or self.session.jobs.busy("file"):
            return
        destination = choose_destination(self, "json", "netcraft-diagnostic.json")
        if destination is None:
            return
        path, overwrite = destination
        history_path = self.session.path
        preferences_path = self.session.preferences_path
        self.session.jobs.submit(
            "file",
            lambda control: save_diagnostic(
                path,
                overwrite,
                result,
                control,
                history_path=history_path,
                preferences_path=preferences_path,
            ),
            lambda outcome: self.session.notify(
                outcome.error
                or ("Save cancelled." if outcome.cancelled else "Sanitized result saved."),
                "error" if outcome.error else "good",
                remember=True,
            ),
        )
