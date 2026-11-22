"""Shared protocol-aware form used for diagnostics and monitor targets."""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QFormLayout, QStackedWidget, QVBoxLayout, QWidget

from netcraft.diagnostic_models import RecordType
from netcraft.gui.diagnostic_data import DiagnosticRequest
from netcraft.gui.widgets import button, combo, controls, form_layout, form_row, label, line, pick
from netcraft.monitoring.probes import ProbeOptions, Target
from netcraft.monitoring.telemetry import MonitorProtocol

_PROTOCOLS = tuple((protocol.value.upper(), protocol) for protocol in MonitorProtocol)


def _form() -> tuple[QWidget, QFormLayout]:
    widget = QWidget()
    layout = form_layout()
    layout.setContentsMargins(0, 0, 0, 0)
    widget.setLayout(layout)
    return widget, layout


class ProbeEditor(QWidget):
    def __init__(self, timeout: float, *, monitoring: bool = False) -> None:
        super().__init__()
        self.monitoring = monitoring
        self.protocol = combo("Protocol", _PROTOCOLS)
        self.timeout = line("Timeout in seconds", "0.1–60", f"{timeout:g}")
        self.host = line("Hostname or IP address", "Hostname or IP address")
        self.port = line("Destination port", "1–65535")
        self.payload = line("UDP echo payload", value="NetCraft echo")
        self.dns_name = line("DNS name or reverse lookup IP", "Domain or IP for PTR")
        self.record_type = combo("DNS record type", ((value.value, value) for value in RecordType))
        self.resolver = line("DNS resolver address", "System resolver if blank")
        self.resolver_port = line("DNS resolver port", "53")
        self.url = line("HTTP or HTTPS URL", "https://hostname/path")
        self.method = combo("HTTP method", (("GET", "GET"), ("HEAD", "HEAD")))
        self.ca_path: Path | None = None
        self.ca_status = label("System trust")
        self.ca_button = button("Choose CA…")
        self.ca_button.clicked.connect(self._choose_ca)
        self.ca_reset = button("Use system trust")
        self.ca_reset.clicked.connect(self._reset_ca)
        self.note = label("", "muted", wrap=True)

        self.stack = QStackedWidget()
        socket_panel, socket_form = _form()
        form_row(socket_form, "Host / IP", self.host)
        form_row(socket_form, "Port", self.port)
        self.payload_label = label("Payload")
        self.payload_label.setBuddy(self.payload)
        socket_form.addRow(self.payload_label, self.payload)
        self.stack.addWidget(socket_panel)
        dns_panel, dns_form = _form()
        form_row(dns_form, "Name / reverse IP", self.dns_name)
        form_row(dns_form, "Record type", self.record_type)
        form_row(dns_form, "Resolver IP", self.resolver)
        form_row(dns_form, "Resolver port", self.resolver_port)
        self.stack.addWidget(dns_panel)

        self.web_panel, web_form = _form()
        form_row(web_form, "URL", self.url)
        self.method_label = label("Method")
        self.method_label.setBuddy(self.method)
        web_form.addRow(self.method_label, self.method)
        self.ca_row = QWidget()
        ca_layout = controls(self.ca_status, self.ca_button, self.ca_reset)
        ca_layout.setContentsMargins(0, 0, 0, 0)
        self.ca_row.setLayout(ca_layout)
        self.ca_label = label("TLS trust")
        self.ca_label.setBuddy(self.ca_button)
        web_form.addRow(self.ca_label, self.ca_row)
        self.stack.addWidget(self.web_panel)

        form = form_layout()
        form_row(form, "Protocol", self.protocol)
        form_row(form, "Timeout (seconds)", self.timeout)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(self.stack)
        layout.addWidget(self.note)
        self.protocol.currentIndexChanged.connect(self._apply_protocol)
        self._apply_protocol()

    def _apply_protocol(self) -> None:
        protocol = MonitorProtocol(self.protocol.currentData())
        udp = protocol is MonitorProtocol.UDP
        web = protocol in {MonitorProtocol.HTTP, MonitorProtocol.HTTPS}
        self.stack.setCurrentIndex(2 if web else (1 if protocol is MonitorProtocol.DNS else 0))
        self.payload.setVisible(udp and not self.monitoring)
        self.payload_label.setVisible(udp and not self.monitoring)
        self.method.setVisible(web and not self.monitoring)
        self.method_label.setVisible(web and not self.monitoring)
        self.ca_row.setVisible(protocol is MonitorProtocol.HTTPS)
        self.ca_label.setVisible(protocol is MonitorProtocol.HTTPS)
        if udp:
            self.note.setText(
                "Requires a UDP echo service. A silent endpoint does not prove network failure."
            )
        elif web:
            self.note.setText(
                "Monitoring uses HEAD. Redirects are reported without following."
                if self.monitoring
                else "Response reads are bounded. Redirects are reported without following."
            )
        elif protocol is MonitorProtocol.DNS:
            self.note.setText("Use PTR with an IPv4 or IPv6 address for reverse lookup.")
        else:
            self.note.setText("The measured TCP handshake describes the selected endpoint.")

    def _choose_ca(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a trusted CA file", "", "PEM (*.pem *.crt)"
        )
        if path:
            self.ca_path = Path(path)
            self.ca_status.setText("System + selected CA")

    def _reset_ca(self) -> None:
        self.ca_path = None
        self.ca_status.setText("System trust")

    def selection(self) -> DiagnosticRequest:
        protocol = MonitorProtocol(self.protocol.currentData())
        try:
            timeout = float(self.timeout.text())
            if not 0.1 <= timeout <= 60:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Enter a timeout between 0.1 and 60 seconds.") from exc
        resolver = None
        resolver_port = None
        if protocol in {MonitorProtocol.TCP, MonitorProtocol.UDP}:
            try:
                port = int(self.port.text())
            except ValueError as exc:
                raise ValueError("Enter a destination port from 1 to 65535.") from exc
            target = Target(protocol, self.host.text().strip(), port)
        elif protocol is MonitorProtocol.DNS:
            target = Target(
                protocol,
                self.dns_name.text().strip(),
                record_type=RecordType(self.record_type.currentData()),
            )
            resolver = self.resolver.text().strip() or None
            if self.resolver_port.text().strip():
                try:
                    resolver_port = int(self.resolver_port.text())
                except ValueError as exc:
                    raise ValueError("Enter a resolver port from 1 to 65535.") from exc
        else:
            target = Target(protocol, self.url.text().strip())
        options = ProbeOptions(
            timeout,
            resolver,
            resolver_port,
            self.ca_path if protocol is MonitorProtocol.HTTPS else None,
        )
        return DiagnosticRequest(
            target,
            options,
            "HEAD" if self.monitoring else self.method.currentText(),
            self.payload.text().encode("utf-8") if protocol is MonitorProtocol.UDP else b"",
        )

    def load(self, request: DiagnosticRequest) -> None:
        target, options = request.target, request.options
        pick(self.protocol, target.protocol)
        self.timeout.setText(f"{options.timeout:g}")
        if target.protocol is MonitorProtocol.DNS:
            self.dns_name.setText(target.destination)
            pick(self.record_type, target.record_type)
            self.resolver.setText(options.resolver or "")
            self.resolver_port.setText(
                str(options.resolver_port) if options.resolver_port is not None else ""
            )
        elif target.protocol in {MonitorProtocol.HTTP, MonitorProtocol.HTTPS}:
            self.url.setText(target.destination)
            self.ca_path = options.ca_file
            if self.ca_path:
                self.ca_status.setText("System + selected CA")
        else:
            self.host.setText(target.destination)
            self.port.setText(str(target.port))
