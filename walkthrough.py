"""Two-reader RFID walk-through detection tester."""

from __future__ import annotations

import csv
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from sa810_reader import Sa810Client


APP_DIR = Path(__file__).resolve().parent
HEX_PATTERN = re.compile(r"^[0-9A-F]+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def normalize_epc(value: str) -> str:
    return re.sub(r"[\s:-]", "", value).upper()


@dataclass
class Participant:
    person_id: str
    epc: str
    placement: str


@dataclass
class Detection:
    timestamps: list[float] = field(default_factory=list)
    rssis: list[float] = field(default_factory=list)

    def add(self, timestamp: float, rssi: float) -> None:
        self.timestamps.append(timestamp)
        self.rssis.append(rssi)


class ReaderPanel(QGroupBox):
    """Connection and radio controls for one SA810 reader."""

    def __init__(self, name: str, default_ip: str) -> None:
        super().__init__(name)
        self.reader_name = name
        self.client = Sa810Client()
        self.usb_devices: list[dict] = []
        self.connected_usb_path: bytes | None = None

        form = QFormLayout(self)
        form.setContentsMargins(12, 12, 12, 12)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(5)

        self.mode = QComboBox()
        self.mode.addItems(("LAN", "USB HID"))
        self.mode.currentTextChanged.connect(self._mode_changed)
        self.host = QLineEdit(default_ip)
        self.port = QLineEdit("49152")
        self.usb_device = QComboBox()
        self.refresh_usb_button = QPushButton("Refresh USB devices")
        self.refresh_usb_button.clicked.connect(self.refresh_usb_devices)
        self.power = QSpinBox()
        self.power.setRange(0, 33)
        self.power.setValue(15)
        self.power.setSuffix(" dBm")

        self.encryption = QComboBox()
        self.encryption.addItems(("None", "Pairing", "CRC"))
        self.encryption.currentTextChanged.connect(self._encryption_changed)
        self.password = QLineEdit()
        self.password.setEnabled(False)
        self.password.setPlaceholderText("Not required")
        self.apply_security_button = QPushButton("Apply listen configuration")
        self.apply_security_button.clicked.connect(self.apply_security)

        self.status = QLabel("Disconnected")
        self.status.setStyleSheet("color: #a33; font-weight: 600;")
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_connection)
        self.apply_power_button = QPushButton("Apply power")
        self.apply_power_button.clicked.connect(self.apply_power)

        form.addRow("Connection", self.mode)
        form.addRow("IP address", self.host)
        form.addRow("TCP port", self.port)
        form.addRow("USB device", self.usb_device)
        form.addRow(self.refresh_usb_button)
        form.addRow("TX power", self.power)
        form.addRow("Encryption", self.encryption)
        form.addRow("Password", self.password)
        form.addRow(self.apply_security_button)
        form.addRow("Status", self.status)
        buttons = QHBoxLayout()
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.apply_power_button)
        form.addRow(buttons)
        self._mode_changed("LAN")

    def _mode_changed(self, mode: str) -> None:
        lan = mode == "LAN"
        self.host.setEnabled(lan)
        self.port.setEnabled(lan)
        self.usb_device.setEnabled(not lan)
        self.refresh_usb_button.setEnabled(not lan)
        if not lan:
            self.refresh_usb_devices()

    def _encryption_changed(self, mode: str) -> None:
        self.password.setEnabled(mode != "None")
        if mode == "None":
            self.password.clear()
            self.password.setPlaceholderText("Not required")
            self.password.setMaxLength(5)
        elif mode == "Pairing":
            self.password.setMaxLength(3)
            self.password.setPlaceholderText("Decimal 0-255")
        else:
            self.password.setMaxLength(5)
            self.password.setPlaceholderText("Decimal 0-65535")

    def refresh_usb_devices(self) -> None:
        if self.mode.currentText() != "USB HID":
            return
        self.usb_device.clear()
        try:
            self.usb_devices = Sa810Client.list_usb_devices()
        except RuntimeError as error:
            self.usb_devices = []
            self.usb_device.addItem(str(error))
            return
        if not self.usb_devices:
            self.usb_device.addItem("No SA810 USB reader found")
            return
        for index, device in enumerate(self.usb_devices, start=1):
            product = device.get("product_string") or "USB UHF Reader"
            serial = device.get("serial_number") or "no serial"
            self.usb_device.addItem(f"{index}: {product} ({serial})")

    def toggle_connection(self) -> None:
        if self.client.connected:
            self.disconnect_reader()
            return
        try:
            if self.mode.currentText() == "USB HID":
                index = self.usb_device.currentIndex()
                if not self.usb_devices or index < 0:
                    raise ConnectionError("No SA810 USB reader is selected.")
                path = self.usb_devices[index]["path"]
                self.client.connect_usb(path)
                self.connected_usb_path = path
                endpoint = "USB HID"
            else:
                host = self.host.text().strip()
                port = int(self.port.text())
                self.client.connect(host, port)
                endpoint = f"{host}:{port}"
            self.client.set_power(self.power.value())
        except (OSError, RuntimeError, ValueError) as error:
            self._set_status(False, "Connection failed")
            QMessageBox.critical(self, f"{self.reader_name} connection failed", str(error))
            return
        self._set_status(True, f"Connected: {endpoint}")

    def disconnect_reader(self) -> None:
        self.client.disconnect()
        self.connected_usb_path = None
        self._set_status(False, "Disconnected")

    def _set_status(self, connected: bool, text: str) -> None:
        self.status.setText(text)
        color = "#18794e" if connected else "#a33"
        self.status.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.mode.setEnabled(not connected)
        self.host.setEnabled(not connected and self.mode.currentText() == "LAN")
        self.port.setEnabled(not connected and self.mode.currentText() == "LAN")
        self.usb_device.setEnabled(not connected and self.mode.currentText() == "USB HID")
        self.refresh_usb_button.setEnabled(
            not connected and self.mode.currentText() == "USB HID")

    def apply_power(self) -> None:
        if not self.client.connected:
            QMessageBox.warning(self, "Reader disconnected", "Connect this reader first.")
            return
        try:
            self.client.set_power(self.power.value())
        except (OSError, ConnectionError) as error:
            QMessageBox.critical(self, "Power command failed", str(error))
            return
        self.status.setText(f"Power applied: {self.power.value()} dBm")

    def apply_security(self) -> None:
        if not self.client.connected:
            QMessageBox.warning(self, "Reader disconnected", "Connect this reader first.")
            return
        try:
            self.client.set_encryption(
                self.encryption.currentText(), self.password.text())
        except (OSError, ConnectionError, ValueError) as error:
            QMessageBox.critical(self, "Listener configuration failed", str(error))
            return
        self.status.setText(
            f"Listen mode sent: {self.encryption.currentText()}")


class WalkThroughWindow(QMainWindow):
    RESULT_COLUMNS = (
        "Person", "EPC", "Placement",
        "A reads", "A avg", "A strongest", "A first",
        "B reads", "B avg", "B strongest", "B first",
        "Detected by", "Order", "Reader gap", "Result",
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RFID Walk-Through Tester")
        self.resize(1500, 900)
        self.setMinimumSize(1100, 700)
        self.participants: dict[str, Participant] = {}
        self.detections: dict[tuple[str, str], Detection] = {}
        self.export_rows: list[dict[str, object]] = []
        self.trial_active = False
        self.trial_started_monotonic = 0.0
        self.trial_started_at = ""
        self.trial_number = 1

        self._build_ui()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(50)
        self.poll_timer.timeout.connect(self.poll_readers)
        self.poll_timer.start()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        title = QLabel("RFID Walk-Through Tester")
        title.setStyleSheet("font-size: 25px; font-weight: 700;")
        layout.addWidget(title)
        subtitle = QLabel(
            "Compare two readers while one or more people walk through the detection area")
        subtitle.setStyleSheet("color: #555;")
        layout.addWidget(subtitle)

        readers = QHBoxLayout()
        self.reader_a = ReaderPanel("Reader A", "192.168.2.120")
        self.reader_b = ReaderPanel("Reader B", "192.168.2.121")
        readers.addWidget(self.reader_a)
        readers.addWidget(self.reader_b)
        layout.addLayout(readers)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_setup_panel())
        splitter.addWidget(self._build_results_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

    def _build_setup_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMaximumWidth(475)
        layout = QVBoxLayout(panel)

        participant_box = QGroupBox("People and tags")
        participant_layout = QVBoxLayout(participant_box)
        form = QGridLayout()
        self.person_id = QLineEdit()
        self.person_id.setPlaceholderText("Person 1")
        self.epc = QLineEdit()
        self.epc.setPlaceholderText("Tag EPC in hexadecimal")
        self.placement = QComboBox()
        self.placement.setEditable(True)
        self.placement.addItems((
            "Neck", "Chest", "Front pocket", "Back pocket",
            "Waist", "Wrist", "Bag", "Other"))
        form.addWidget(QLabel("Person ID"), 0, 0)
        form.addWidget(self.person_id, 0, 1)
        form.addWidget(QLabel("EPC"), 1, 0)
        form.addWidget(self.epc, 1, 1)
        form.addWidget(QLabel("Tag placement"), 2, 0)
        form.addWidget(self.placement, 2, 1)
        participant_layout.addLayout(form)
        buttons = QHBoxLayout()
        add = QPushButton("Add / update")
        add.clicked.connect(self.add_participant)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove_participant)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        participant_layout.addLayout(buttons)
        self.participant_table = QTableWidget(0, 3)
        self.participant_table.setHorizontalHeaderLabels(
            ("Person", "EPC", "Placement"))
        self.participant_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.participant_table.horizontalHeader().setStretchLastSection(True)
        self.participant_table.setSelectionBehavior(QTableWidget.SelectRows)
        participant_layout.addWidget(self.participant_table)
        layout.addWidget(participant_box, 1)

        trial_box = QGroupBox("Walk-through trial")
        trial_form = QFormLayout(trial_box)
        self.trial_name = QLineEdit("Trial 1")
        self.duration = QSpinBox()
        self.duration.setRange(1, 600)
        self.duration.setValue(15)
        self.duration.setSuffix(" s")
        self.countdown = QLabel("Ready")
        self.countdown.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.start_button = QPushButton("Start walk-through trial")
        self.start_button.setMinimumHeight(42)
        self.start_button.clicked.connect(self.toggle_trial)
        trial_form.addRow("Trial name", self.trial_name)
        trial_form.addRow("Duration", self.duration)
        trial_form.addRow("Status", self.countdown)
        trial_form.addRow(self.start_button)
        layout.addWidget(trial_box)

        export = QPushButton("Export trial results to CSV")
        export.clicked.connect(self.export_csv)
        clear = QPushButton("Clear displayed results")
        clear.clicked.connect(self.clear_results)
        layout.addWidget(export)
        layout.addWidget(clear)
        return panel

    def _build_results_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        summary_box = QGroupBox("Detection summary")
        summary = QHBoxLayout(summary_box)
        self.summary_labels: dict[str, QLabel] = {}
        for key, title in (
            ("registered", "Registered"), ("either", "Detected by either"),
            ("both", "Detected by both"), ("missed", "Missed")):
            card = QLabel(f"{title}\n0")
            card.setAlignment(Qt.AlignCenter)
            card.setStyleSheet(
                "font-size: 16px; font-weight: 600; padding: 10px; "
                "border: 1px solid #bbb; border-radius: 4px;")
            summary.addWidget(card)
            self.summary_labels[key] = card
        layout.addWidget(summary_box)

        self.results = QTableWidget(0, len(self.RESULT_COLUMNS))
        self.results.setHorizontalHeaderLabels(self.RESULT_COLUMNS)
        self.results.setAlternatingRowColors(True)
        self.results.setSortingEnabled(True)
        self.results.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.results.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.results, 1)
        note = QLabel(
            "RSSI values are dBm. Strongest is the value closest to zero. "
            "First is seconds after the trial started.")
        note.setStyleSheet("color: #555;")
        layout.addWidget(note)
        return panel

    def add_participant(self) -> None:
        person_id = self.person_id.text().strip()
        epc = normalize_epc(self.epc.text())
        placement = self.placement.currentText().strip()
        if not person_id or not epc or not placement:
            QMessageBox.warning(self, "Missing information",
                "Enter a person ID, EPC, and tag placement.")
            return
        if len(epc) % 2 or not HEX_PATTERN.fullmatch(epc):
            QMessageBox.warning(self, "Invalid EPC",
                "EPC must contain an even number of hexadecimal characters.")
            return
        self.participants[epc] = Participant(person_id, epc, placement)
        self._refresh_participants()
        self.person_id.clear()
        self.epc.clear()

    def remove_participant(self) -> None:
        rows = sorted({item.row() for item in self.participant_table.selectedItems()},
                      reverse=True)
        for row in rows:
            epc_item = self.participant_table.item(row, 1)
            if epc_item:
                self.participants.pop(epc_item.text(), None)
        self._refresh_participants()

    def _refresh_participants(self) -> None:
        self.participant_table.setRowCount(0)
        for participant in self.participants.values():
            row = self.participant_table.rowCount()
            self.participant_table.insertRow(row)
            for column, value in enumerate((
                    participant.person_id, participant.epc,
                    participant.placement)):
                self.participant_table.setItem(
                    row, column, QTableWidgetItem(value))
        self._set_summary("registered", "Registered", len(self.participants))

    def toggle_trial(self) -> None:
        if self.trial_active:
            self.finish_trial()
        else:
            self.start_trial()

    def start_trial(self) -> None:
        if not self.participants:
            QMessageBox.warning(self, "No participants",
                "Add at least one person and EPC before starting.")
            return
        if not self.reader_a.client.connected or not self.reader_b.client.connected:
            QMessageBox.warning(self, "Two readers required",
                "Connect both Reader A and Reader B before starting the trial.")
            return
        if (self.reader_a.connected_usb_path is not None and
                self.reader_a.connected_usb_path == self.reader_b.connected_usb_path):
            QMessageBox.warning(self, "Same USB reader selected",
                "Reader A and Reader B must use different USB devices.")
            return
        if (self.reader_a.mode.currentText() == "LAN" and
                self.reader_b.mode.currentText() == "LAN" and
                self.reader_a.host.text().strip() == self.reader_b.host.text().strip() and
                self.reader_a.port.text().strip() == self.reader_b.port.text().strip()):
            QMessageBox.warning(self, "Same LAN reader selected",
                "Reader A and Reader B must use different IP addresses or ports.")
            return
        self._discard_queued_tags()
        try:
            self.reader_a.client.start_inventory()
            self.reader_b.client.start_inventory()
        except (OSError, ConnectionError) as error:
            self.reader_a.client.stop_inventory()
            self.reader_b.client.stop_inventory()
            QMessageBox.critical(self, "Could not start trial", str(error))
            return

        self.detections = {}
        self.trial_active = True
        self.trial_started_monotonic = time.monotonic()
        self.trial_started_at = utc_now()
        self.start_button.setText("Stop trial now")
        self.start_button.setStyleSheet("background: #b42318; color: white;")
        self.duration.setEnabled(False)
        self.trial_name.setEnabled(False)
        self._update_countdown()

    def finish_trial(self) -> None:
        if not self.trial_active:
            return
        self.reader_a.client.stop_inventory()
        self.reader_b.client.stop_inventory()
        self._drain_tags(record=True)
        self.trial_active = False
        self.start_button.setText("Start walk-through trial")
        self.start_button.setStyleSheet("")
        self.duration.setEnabled(True)
        self.trial_name.setEnabled(True)
        self.countdown.setText("Complete")
        self._render_results()
        self.trial_number += 1
        self.trial_name.setText(f"Trial {self.trial_number}")

    def poll_readers(self) -> None:
        for panel, label in ((self.reader_a, "A"), (self.reader_b, "B")):
            while not panel.client.messages.empty():
                message = panel.client.messages.get_nowait()
                panel.status.setText(message)
                if not panel.client.connected:
                    panel._set_status(False, "Disconnected")
        self._drain_tags(record=self.trial_active)
        if self.trial_active:
            self._update_countdown()

    def _drain_tags(self, record: bool) -> None:
        for panel, label in ((self.reader_a, "A"), (self.reader_b, "B")):
            while not panel.client.tags.empty():
                tag = panel.client.tags.get_nowait()
                if record:
                    key = (label, normalize_epc(tag.epc))
                    self.detections.setdefault(key, Detection()).add(
                        tag.received_at, tag.rssi)

    def _update_countdown(self) -> None:
        elapsed = time.monotonic() - self.trial_started_monotonic
        remaining = max(0.0, self.duration.value() - elapsed)
        self.countdown.setText(f"Running: {remaining:.1f} s remaining")
        if remaining <= 0:
            self.finish_trial()

    def _discard_queued_tags(self) -> None:
        for panel in (self.reader_a, self.reader_b):
            while not panel.client.tags.empty():
                panel.client.tags.get_nowait()

    @staticmethod
    def _metrics(detection: Detection | None,
                 start_epoch: float) -> tuple[int, str, str, str]:
        if detection is None or not detection.rssis:
            return 0, "-", "-", "-"
        return (
            len(detection.rssis),
            f"{statistics.fmean(detection.rssis):.1f}",
            f"{max(detection.rssis):.1f}",
            f"{max(0.0, detection.timestamps[0] - start_epoch):.2f}",
        )

    def _render_results(self) -> None:
        observed = {epc for _, epc in self.detections}
        all_epcs = list(self.participants)
        all_epcs.extend(sorted(observed - set(self.participants)))
        self.results.setSortingEnabled(False)
        self.results.setRowCount(0)
        detected_either = detected_both = missed = 0
        start_epoch = datetime.fromisoformat(self.trial_started_at).timestamp()
        trial_name = self.trial_name.text().strip() or f"Trial {self.trial_number}"

        for epc in all_epcs:
            participant = self.participants.get(epc)
            detection_a = self.detections.get(("A", epc))
            detection_b = self.detections.get(("B", epc))
            a = self._metrics(detection_a, start_epoch)
            b = self._metrics(detection_b, start_epoch)
            seen_a, seen_b = a[0] > 0, b[0] > 0
            order, reader_gap = "-", "-"
            if seen_a and seen_b:
                detected_by, result, color = "A + B", "Detected by both", "#d1fadf"
                first_a = min(detection_a.timestamps)
                first_b = min(detection_b.timestamps)
                order = "A -> B" if first_a <= first_b else "B -> A"
                reader_gap = f"{abs(first_b - first_a):.2f} s"
            elif seen_a:
                detected_by, result, color = "A only", "Partial detection", "#fef0c7"
            elif seen_b:
                detected_by, result, color = "B only", "Partial detection", "#fef0c7"
            else:
                detected_by, result, color = "Neither", "Missed", "#fee4e2"

            if participant:
                if seen_a or seen_b:
                    detected_either += 1
                else:
                    missed += 1
                if seen_a and seen_b:
                    detected_both += 1
                person = participant.person_id
                placement = participant.placement
            else:
                person, placement = "Unregistered", "Unknown"
                result = "Unregistered tag"
                color = "#e4e7ec"

            values = (
                person, epc, placement,
                str(a[0]), a[1], a[2], a[3],
                str(b[0]), b[1], b[2], b[3],
                detected_by, order, reader_gap, result,
            )
            row = self.results.rowCount()
            self.results.insertRow(row)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(QColor(color))
                self.results.setItem(row, column, item)

            for reader, detection in (("A", detection_a), ("B", detection_b)):
                metrics = self._metrics(detection, start_epoch)
                last_seen = ""
                if detection and detection.timestamps:
                    last_seen = f"{max(0.0, detection.timestamps[-1] - start_epoch):.3f}"
                self.export_rows.append({
                    "trial": trial_name,
                    "started_at_utc": self.trial_started_at,
                    "duration_s": self.duration.value(),
                    "person_id": person,
                    "epc": epc,
                    "placement": placement,
                    "reader": reader,
                    "detected": "yes" if metrics[0] else "no",
                    "read_count": metrics[0],
                    "average_rssi_dbm": "" if metrics[1] == "-" else metrics[1],
                    "strongest_rssi_dbm": "" if metrics[2] == "-" else metrics[2],
                    "first_seen_s": "" if metrics[3] == "-" else metrics[3],
                    "last_seen_s": last_seen,
                    "detection_order": order if seen_a and seen_b else "",
                    "reader_gap_s": (reader_gap.removesuffix(" s")
                                     if seen_a and seen_b else ""),
                })

        self.results.setSortingEnabled(True)
        self._set_summary("registered", "Registered", len(self.participants))
        self._set_summary("either", "Detected by either", detected_either)
        self._set_summary("both", "Detected by both", detected_both)
        self._set_summary("missed", "Missed", missed)

    def _set_summary(self, key: str, title: str, value: int) -> None:
        self.summary_labels[key].setText(f"{title}\n{value}")

    def export_csv(self) -> None:
        if not self.export_rows:
            QMessageBox.information(self, "No results", "Complete a trial before exporting.")
            return
        default = APP_DIR / "walkthrough_results.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export walk-through results", str(default), "CSV files (*.csv)")
        if not path:
            return
        columns = list(self.export_rows[0])
        with open(path, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(self.export_rows)
        QMessageBox.information(self, "Export complete", f"Saved results to:\n{path}")

    def clear_results(self) -> None:
        if self.trial_active:
            QMessageBox.warning(self, "Trial running", "Stop the trial before clearing results.")
            return
        self.results.setRowCount(0)
        self.detections.clear()
        self.export_rows.clear()
        for key, title in (("either", "Detected by either"),
                           ("both", "Detected by both"),
                           ("missed", "Missed")):
            self._set_summary(key, title, 0)
        self.countdown.setText("Ready")

    def closeEvent(self, event) -> None:
        self.reader_a.client.disconnect()
        self.reader_b.client.disconnect()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    window = WalkThroughWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
