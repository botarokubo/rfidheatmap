"""RAIN RFID coverage mapper with PySide6, Matplotlib, NumPy and SQLite."""

from __future__ import annotations

import csv
import math
import random
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QStatusBar, QVBoxLayout, QWidget,
)

from sa810_reader import Sa810TcpClient

matplotlib.use("QtAgg")

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "rfid_measurements.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class TagRead:
    timestamp: str
    epc: str
    rssi: float
    frequency: float
    phase: float
    antenna: int


class MeasurementStore:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.create_function("sqrt", 1, math.sqrt)
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
                epc TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
                z REAL NOT NULL, duration REAL NOT NULL,
                power_dbm REAL NOT NULL, mode TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tag_reads (
                id INTEGER PRIMARY KEY,
                measurement_id INTEGER NOT NULL REFERENCES measurements(id),
                timestamp TEXT NOT NULL, epc TEXT NOT NULL,
                rssi REAL NOT NULL, frequency_mhz REAL NOT NULL,
                phase_deg REAL NOT NULL, antenna INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS reads_measurement_idx
                ON tag_reads(measurement_id);
            CREATE INDEX IF NOT EXISTS measurements_position_idx
                ON measurements(z, power_dbm, x, y);
            """
        )
        self.connection.commit()

    def save(self, *, epc: str, x: float, y: float, z: float,
             duration: float, power: float, mode: str,
             reads: list[TagRead]) -> int:
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO measurements
                   (started_at, epc, x, y, z, duration, power_dbm, mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (reads[0].timestamp if reads else utc_now(), epc, x, y, z,
                 duration, power, mode),
            )
            measurement_id = int(cursor.lastrowid)
            self.connection.executemany(
                """INSERT INTO tag_reads
                   (measurement_id, timestamp, epc, rssi, frequency_mhz,
                    phase_deg, antenna) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [(measurement_id, r.timestamp, r.epc, r.rssi, r.frequency,
                  r.phase, r.antenna) for r in reads],
            )
        return measurement_id

    def positions(self, z: float, power: float) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT m.id, m.x, m.y, m.z, m.duration, m.power_dbm,
                      COUNT(r.id) AS read_count,
                      AVG(r.rssi) AS rssi_average,
                      MIN(r.rssi) AS rssi_min, MAX(r.rssi) AS rssi_max,
                      CASE WHEN COUNT(r.id) > 1 THEN
                        sqrt(MAX(0, AVG(r.rssi * r.rssi) -
                                    AVG(r.rssi) * AVG(r.rssi)))
                      ELSE 0 END AS rssi_stability,
                      COUNT(r.id) / m.duration AS read_rate
               FROM measurements m
               LEFT JOIN tag_reads r ON r.measurement_id = m.id
               WHERE ABS(m.z - ?) < 0.0001
                 AND ABS(m.power_dbm - ?) < 0.0001
               GROUP BY m.id ORDER BY m.y, m.x""", (z, power)))

    def filter_values(self, column: str) -> list[float]:
        if column not in {"z", "power_dbm"}:
            raise ValueError("Unsupported filter")
        return [float(row[0]) for row in self.connection.execute(
            f"SELECT DISTINCT {column} FROM measurements ORDER BY {column}")]

    def export_csv(self, destination: Path) -> int:
        rows = list(self.connection.execute(
            """SELECT r.measurement_id, r.timestamp, r.epc, m.x, m.y, m.z,
                      m.duration, m.power_dbm, r.rssi, r.frequency_mhz,
                      r.phase_deg, r.antenna, m.mode
               FROM tag_reads r JOIN measurements m
                 ON m.id = r.measurement_id ORDER BY r.id"""))
        headers = list(rows[0].keys()) if rows else [
            "measurement_id", "timestamp", "epc", "x", "y", "z",
            "duration", "power_dbm", "rssi", "frequency_mhz",
            "phase_deg", "antenna", "mode"]
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)
        return len(rows)

    def import_csv(self, source: Path) -> tuple[int, int, float, float]:
        """Import this application's raw-read CSV export into the database."""
        with source.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            headers = set(reader.fieldnames or [])
        required = {
            "measurement_id", "timestamp", "epc", "x", "y", "z",
            "duration", "power_dbm", "rssi", "frequency_mhz",
            "phase_deg", "antenna", "mode",
        }
        missing = required - headers
        if missing:
            raise ValueError(
                "This is not an RFID Field Mapper raw-read export. Missing: "
                + ", ".join(sorted(missing)))
        if not rows:
            raise ValueError("The selected CSV contains no RFID reads.")
        groups: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            groups.setdefault(row["measurement_id"], []).append(row)
        imported_reads = 0
        last_z = 1.0
        last_power = 15.0
        try:
            for group_rows in groups.values():
                first = group_rows[0]
                x, y, z = float(first["x"]), float(first["y"]), float(first["z"])
                duration = float(first["duration"])
                power = float(first["power_dbm"])
                tag_reads = [TagRead(
                    timestamp=row["timestamp"], epc=row["epc"],
                    rssi=float(row["rssi"]),
                    frequency=float(row["frequency_mhz"] or 0),
                    phase=float(row["phase_deg"] or 0),
                    antenna=int(float(row["antenna"] or 0)),
                ) for row in group_rows]
                self.save(
                    epc=first["epc"], x=x, y=y, z=z,
                    duration=duration, power=power,
                    mode=f"imported:{first['mode']}", reads=tag_reads)
                imported_reads += len(tag_reads)
                last_z, last_power = z, power
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid value in CSV: {error}") from error
        return len(groups), imported_reads, last_z, last_power

    def close(self) -> None:
        self.connection.close()

    def clear_all(self) -> tuple[int, int]:
        """Permanently remove every saved measurement and raw tag read."""
        measurement_count = int(self.connection.execute(
            "SELECT COUNT(*) FROM measurements").fetchone()[0])
        read_count = int(self.connection.execute(
            "SELECT COUNT(*) FROM tag_reads").fetchone()[0])
        with self.connection:
            self.connection.execute("DELETE FROM tag_reads")
            self.connection.execute("DELETE FROM measurements")
        return measurement_count, read_count


class SimulatedSA810:
    """Repeatable RF-like samples until the real SA810 protocol is supplied."""

    frequencies = (920.625, 921.375, 922.125, 923.125, 924.375)

    @classmethod
    def collect(cls, epc: str, x: float, y: float, z: float,
                duration: float, power: float) -> list[TagRead]:
        rng = random.Random(f"{epc}:{x:.3f}:{y:.3f}:{z:.3f}:{power:.1f}")
        distance = math.sqrt(x * x + y * y + z * z)
        expected_rssi = min(-28.0, -35.0 - 4.2 * distance
                            + (power - 30.0) * 0.8)
        read_rate = max(0.5, 25.0 - distance * 2.0
                        + (power - 30.0) * 0.6)
        count = max(1, round(read_rate * duration * rng.uniform(0.85, 1.15)))
        start = time.time()
        return [TagRead(
            datetime.fromtimestamp(
                start + index / max(count - 1, 1) * duration,
                timezone.utc).isoformat(timespec="milliseconds"),
            epc, round(rng.gauss(expected_rssi, 1.8 + distance * 0.12), 1),
            rng.choice(cls.frequencies), round(rng.uniform(0, 360), 1), 1,
        ) for index in range(count)]


class HeatmapCanvas(FigureCanvasQTAgg):
    metrics = {
        "RSSI Average": ("rssi_average", "RSSI (dBm)", "RdYlGn"),
        "Read Rate": ("read_rate", "Reads per second", "RdYlGn"),
        "Read Count": ("read_count", "Reads", "RdYlGn"),
        "RSSI Stability": ("rssi_stability", "Standard deviation (dB)",
                           "RdYlGn_r"),
    }

    def __init__(self) -> None:
        self.figure = Figure(figsize=(7, 5), constrained_layout=True)
        self.axes = self.figure.add_subplot(111)
        super().__init__(self.figure)
        self._colorbar = None
        self._cells: dict[tuple[int, int], sqlite3.Row] = {}
        self.current_rows: list[sqlite3.Row] = []
        self.current_metric = "RSSI Average"
        self.current_z = 1.0
        self.current_power = 15.0
        self.mpl_connect("button_press_event", self._clicked)
        self.point_selected = None

    def draw_rows(self, rows: list[sqlite3.Row], metric: str,
                  z: float, power: float) -> None:
        self.current_rows = list(rows)
        self.current_metric = metric
        self.current_z = z
        self.current_power = power
        if self._colorbar is not None:
            self._colorbar.remove()
            self._colorbar = None
        self.axes.clear()
        self._cells.clear()
        if not rows:
            self.axes.set_axis_off()
            self.axes.text(.5, .5,
                "No measurements for this Z level and power.\n"
                "Enter a point and select Measure and save point.",
                ha="center", va="center", transform=self.axes.transAxes,
                color="#5d6670", fontsize=11)
            self.draw_idle()
            return

        self.axes.set_axis_on()
        key, colorbar_label, color_map = self.metrics[metric]
        x_values = sorted({float(row["x"]) for row in rows})
        y_values = sorted({float(row["y"]) for row in rows})
        x_indices = {value: index for index, value in enumerate(x_values)}
        y_indices = {value: index for index, value in enumerate(y_values)}
        matrix = np.full((len(y_values), len(x_values)), np.nan)
        for row in rows:
            xi, yi = x_indices[float(row["x"])], y_indices[float(row["y"])]
            self._cells[(xi, yi)] = row
            if row["read_count"] > 0 and row[key] is not None:
                matrix[yi, xi] = float(row[key])
        masked = np.ma.masked_invalid(matrix)
        image = self.axes.imshow(masked, origin="lower", aspect="equal",
                                 cmap=color_map,
                                 **({} if masked.count() else {"vmin": 0, "vmax": 1}))
        image.cmap.set_bad("#e3e7eb")
        self.axes.set_xticks(range(len(x_values)), [f"{v:g}" for v in x_values])
        self.axes.set_yticks(range(len(y_values)), [f"{v:g}" for v in y_values])
        self.axes.set_xlabel("X position (m)")
        self.axes.set_ylabel("Y position (m)")
        self.axes.set_title(f"{metric} at Z={z:g} m and {power:g} dBm",
                            fontweight="bold", pad=12)
        for yi in range(matrix.shape[0]):
            for xi in range(matrix.shape[1]):
                value = matrix[yi, xi]
                row = self._cells.get((xi, yi))
                if row is not None and row["read_count"] == 0:
                    self.axes.text(xi, yi, "No read", ha="center", va="center",
                                   fontsize=9, fontweight="bold", color="#59636d")
                elif not np.isnan(value):
                    label = str(round(value)) if key == "read_count" else f"{value:.1f}"
                    self.axes.text(xi, yi, label, ha="center", va="center",
                                   fontsize=9, fontweight="bold")
        if masked.count():
            self._colorbar = self.figure.colorbar(image, ax=self.axes, shrink=.82)
            self._colorbar.set_label(colorbar_label)
        self.draw_idle()

    def export_report(self, destination: Path) -> int:
        """Export the current heatmap and per-grid summary as one PNG."""
        rows = self.current_rows
        if not rows:
            raise ValueError("There are no measured grid points to export.")
        key, colorbar_label, color_map = self.metrics[self.current_metric]
        x_values = sorted({float(row["x"]) for row in rows})
        y_values = sorted({float(row["y"]) for row in rows})
        x_indices = {value: index for index, value in enumerate(x_values)}
        y_indices = {value: index for index, value in enumerate(y_values)}
        matrix = np.full((len(y_values), len(x_values)), np.nan)
        report_cells: dict[tuple[int, int], sqlite3.Row] = {}
        for row in rows:
            position = (x_indices[float(row["x"])], y_indices[float(row["y"])])
            report_cells[position] = row
            if row["read_count"] > 0 and row[key] is not None:
                matrix[position[1], position[0]] = float(row[key])

        table_height = max(2.2, .34 * len(rows) + 1.0)
        report = Figure(figsize=(12, 7 + table_height), constrained_layout=True)
        grid = report.add_gridspec(2, 1, height_ratios=(7, table_height))
        axes = report.add_subplot(grid[0])
        table_axes = report.add_subplot(grid[1])
        masked = np.ma.masked_invalid(matrix)
        image = axes.imshow(masked, origin="lower", aspect="equal", cmap=color_map,
                            **({} if masked.count() else {"vmin": 0, "vmax": 1}))
        image.cmap.set_bad("#e3e7eb")
        axes.set_xticks(range(len(x_values)), [f"{v:g}" for v in x_values])
        axes.set_yticks(range(len(y_values)), [f"{v:g}" for v in y_values])
        axes.set_xlabel("X position (m)")
        axes.set_ylabel("Y position (m)")
        axes.set_title(
            f"{self.current_metric} at Z={self.current_z:g} m and "
            f"{self.current_power:g} dBm", fontweight="bold", fontsize=15)
        for yi in range(matrix.shape[0]):
            for xi in range(matrix.shape[1]):
                value = matrix[yi, xi]
                row = report_cells.get((xi, yi))
                if row is not None and row["read_count"] == 0:
                    axes.text(xi, yi, "No read", ha="center", va="center",
                              fontsize=10, fontweight="bold", color="#59636d")
                elif not np.isnan(value):
                    label = str(round(value)) if key == "read_count" else f"{value:.1f}"
                    axes.text(xi, yi, label, ha="center", va="center",
                              fontsize=10, fontweight="bold")
        if masked.count():
            colorbar = report.colorbar(image, ax=axes, shrink=.82)
            colorbar.set_label(colorbar_label)

        table_axes.axis("off")
        headers = ("X", "Y", "Z", "RSSI avg", "Min", "Max", "Std",
                   "Reads", "Rate/s")
        table_rows = []
        for row in sorted(rows, key=lambda item: (-item["y"], item["x"])):
            no_read = row["read_count"] == 0
            table_rows.append([
                f"{row['x']:g}", f"{row['y']:g}", f"{row['z']:g}",
                "No read" if no_read else f"{row['rssi_average']:.1f}",
                "N/A" if no_read else f"{row['rssi_min']:.1f}",
                "N/A" if no_read else f"{row['rssi_max']:.1f}",
                "N/A" if no_read else f"{row['rssi_stability']:.1f}",
                str(row["read_count"]), f"{row['read_rate']:.1f}",
            ])
        table_axes.set_title("Grid measurement data", loc="left",
                             fontweight="bold", fontsize=12, pad=8)
        table = table_axes.table(
            cellText=table_rows, colLabels=headers, cellLoc="center",
            colLoc="center", loc="upper center", bbox=(0, 0, 1, .92))
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        for (row_index, _column), cell in table.get_celld().items():
            cell.set_edgecolor("#d0d4d8")
            if row_index == 0:
                cell.set_facecolor("#263746")
                cell.set_text_props(color="white", fontweight="bold")
            elif row_index % 2 == 0:
                cell.set_facecolor("#f1f4f6")
        report.savefig(destination, dpi=200, bbox_inches="tight",
                       facecolor="white")
        return len(rows)

    def _clicked(self, event) -> None:
        if event.inaxes is not self.axes or event.xdata is None:
            return
        row = self._cells.get((round(event.xdata), round(event.ydata)))
        if row is not None and self.point_selected:
            self.point_selected(row)


class CoverageWindow(QMainWindow):
    def __init__(self, store: MeasurementStore | None = None) -> None:
        super().__init__()
        self.store = store or MeasurementStore()
        self.reader = Sa810TcpClient()
        self._live_reads: list[TagRead] = []
        self._live_context: tuple[str, float, float, float, float, float] | None = None
        self._listening_for_tags = False
        self._tag_stats: dict[str, dict[str, float | int]] = {}
        self.reader_timer = QTimer(self)
        self.reader_timer.setInterval(50)
        self.reader_timer.timeout.connect(self.poll_reader)
        self.reader_timer.start()
        self.setWindowTitle("RFID Field Mapper")
        self.resize(1220, 790)
        self.setMinimumSize(960, 640)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready - simulator mode")
        self._build_menu()
        self._build_ui()
        self._reload_filters()
        self.refresh_heatmap()

    def _build_menu(self) -> None:
        export_action = QAction("Export reads to CSV", self)
        export_action.triggered.connect(self.export_csv)
        report_action = QAction("Export heatmap and grid data", self)
        report_action.triggered.connect(self.export_heatmap_report)
        reset_action = QAction("Reset all grid data", self)
        reset_action.triggered.connect(self.reset_ui)
        quit_action = QAction("Exit", self)
        quit_action.triggered.connect(self.close)
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(report_action)
        file_menu.addAction(export_action)
        file_menu.addAction(reset_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        title = QLabel("RFID Field Mapper")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        outer.addWidget(title)
        outer.addWidget(QLabel("Measure and visualize RFID coverage"))
        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)
        self.setCentralWidget(root)

        sidebar = QWidget()
        sidebar.setMaximumWidth(410)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(6, 6, 10, 6)
        side_layout.setSpacing(9)
        side_layout.setAlignment(Qt.AlignTop)
        reader_box = QGroupBox("Reader")
        reader_form = QFormLayout(reader_box)
        self._compact_form(reader_form)
        self.reader_mode = QComboBox()
        self.reader_mode.addItems(("Live SA810", "Simulator"))
        self.reader_host = QLineEdit("192.168.2.120")
        self.reader_port = QLineEdit("49152")
        self.power_input = self._spin(0, 33, 15, 1, " dBm")
        self.reader_status = QLabel("Disconnected")
        self.reader_status.setStyleSheet("color: #a33; font-weight: 600;")
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_reader_connection)
        reader_form.addRow("Mode", self.reader_mode)
        reader_form.addRow("IP address", self.reader_host)
        reader_form.addRow("TCP port", self.reader_port)
        reader_form.addRow("TX power", self.power_input)
        reader_form.addRow("Status", self.reader_status)
        reader_form.addRow(self.connect_button)
        self.apply_power_button = QPushButton("Apply power to reader")
        self.apply_power_button.clicked.connect(self.apply_reader_power)
        reader_form.addRow(self.apply_power_button)
        self.reader_mode.currentTextChanged.connect(self.reader_mode_changed)
        self._compact_group(reader_box)
        side_layout.addWidget(reader_box)

        encryption_box = QGroupBox("Encrypted tag listener")
        encryption_form = QFormLayout(encryption_box)
        self._compact_form(encryption_form)
        self.encryption_mode = QComboBox()
        self.encryption_mode.addItems(("None", "Pairing", "CRC"))
        self.encryption_mode.currentTextChanged.connect(
            self.encryption_mode_changed)
        self.encryption_password = QLineEdit()
        self.encryption_password.setMaxLength(5)
        self.encryption_password.setPlaceholderText("Not required")
        self.apply_encryption_button = QPushButton(
            "Apply listen configuration")
        self.apply_encryption_button.clicked.connect(
            self.apply_listener_encryption)
        encryption_form.addRow("Mode", self.encryption_mode)
        encryption_form.addRow("Password", self.encryption_password)
        encryption_form.addRow(self.apply_encryption_button)
        encryption_note = QLabel(
            "Reader-side listening only. This does not encrypt or modify tags.")
        encryption_note.setWordWrap(True)
        encryption_note.setStyleSheet("color: #555; font-size: 11px;")
        encryption_form.addRow(encryption_note)
        self._compact_group(encryption_box)
        side_layout.addWidget(encryption_box)

        listener_box = QGroupBox("Tag listener")
        listener_form = QFormLayout(listener_box)
        self._compact_form(listener_form)
        self.detected_tags = QComboBox()
        self.detected_tags.setMinimumContentsLength(24)
        self.listen_status = QLabel("Not listening")
        self.listen_button = QPushButton("Start listening")
        self.listen_button.clicked.connect(self.toggle_tag_listener)
        self.use_tag_button = QPushButton("Use selected tag as reference")
        self.use_tag_button.clicked.connect(self.use_selected_tag)
        self.clear_tags_button = QPushButton("Clear detected tags")
        self.clear_tags_button.clicked.connect(self.clear_detected_tags)
        listener_form.addRow("Detected EPC", self.detected_tags)
        listener_form.addRow("Latest", self.listen_status)
        listener_form.addRow(self.listen_button)
        listener_form.addRow(self.use_tag_button)
        listener_form.addRow(self.clear_tags_button)
        self._compact_group(listener_box)
        side_layout.addWidget(listener_box)

        point_box = QGroupBox("Measurement point")
        point_form = QFormLayout(point_box)
        self._compact_form(point_form)
        self.epc = QLineEdit()
        self.epc.setPlaceholderText("Leave empty to accept all tags")
        self.x_input = self._spin(-100, 100, 0, 2, " m")
        self.y_input = self._spin(-100, 100, 0, 2, " m")
        self.z_input = self._spin(0, 100, 1, 2, " m")
        self.duration_input = self._spin(.1, 300, 5, 1, " s")
        point_form.addRow("Reference EPC", self.epc)
        point_form.addRow("X", self.x_input)
        point_form.addRow("Y", self.y_input)
        point_form.addRow("Z", self.z_input)
        point_form.addRow("Duration", self.duration_input)
        self.measure_button = QPushButton("Measure and save point")
        self.measure_button.clicked.connect(self.measure)
        point_form.addRow(self.measure_button)
        self._compact_group(point_box)
        side_layout.addWidget(point_box)

        summary_box = QGroupBox("Last measurement")
        summary_grid = QGridLayout(summary_box)
        summary_grid.setContentsMargins(12, 12, 12, 12)
        summary_grid.setHorizontalSpacing(16)
        summary_grid.setVerticalSpacing(5)
        self.summary: dict[str, QLabel] = {}
        for row, name in enumerate(("RSSI average", "RSSI range", "Read count",
                                    "Read rate", "RSSI stability")):
            summary_grid.addWidget(QLabel(name), row, 0)
            value = QLabel("-")
            value.setAlignment(Qt.AlignRight)
            value.setStyleSheet("font-size: 16px; font-weight: 600;")
            summary_grid.addWidget(value, row, 1)
            self.summary[name] = value
        self._compact_group(summary_box)
        side_layout.addWidget(summary_box)
        export_button = QPushButton("Export all reads to CSV")
        export_button.clicked.connect(self.export_csv)
        side_layout.addWidget(export_button)
        report_button = QPushButton("Export heatmap and grid data")
        report_button.clicked.connect(self.export_heatmap_report)
        side_layout.addWidget(report_button)
        reset_button = QPushButton("Reset all grid data")
        reset_button.clicked.connect(self.reset_ui)
        side_layout.addWidget(reset_button)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(sidebar)
        scroll.setMinimumWidth(390)
        scroll.setMaximumWidth(430)
        splitter.addWidget(scroll)

        chart_panel = QWidget()
        chart_layout = QVBoxLayout(chart_panel)
        filters = QHBoxLayout()
        self.metric_combo = QComboBox()
        self.metric_combo.addItems(HeatmapCanvas.metrics)
        self.z_combo = QComboBox()
        self.z_combo.setEditable(True)
        self.power_combo = QComboBox()
        self.power_combo.setEditable(True)
        filters.addWidget(QLabel("Metric"))
        filters.addWidget(self.metric_combo)
        filters.addSpacing(12)
        filters.addWidget(QLabel("Z level (m)"))
        filters.addWidget(self.z_combo)
        filters.addSpacing(12)
        filters.addWidget(QLabel("Heatmap power (dBm)"))
        filters.addWidget(self.power_combo)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_heatmap)
        filters.addWidget(refresh)
        filters.addStretch()
        chart_layout.addLayout(filters)
        self.heatmap = HeatmapCanvas()
        self.heatmap.point_selected = self.show_point
        chart_layout.addWidget(self.heatmap, 1)
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        chart_layout.addWidget(divider)
        self.point_label = QLabel(
            "Collect a point, then click its heatmap cell for details.")
        self.point_label.setWordWrap(True)
        chart_layout.addWidget(self.point_label)
        splitter.addWidget(chart_panel)
        splitter.setStretchFactor(1, 1)
        self.metric_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.z_combo.currentTextChanged.connect(self.refresh_heatmap)
        self.power_combo.currentTextChanged.connect(self.refresh_heatmap)

    @staticmethod
    def _spin(minimum: float, maximum: float, value: float,
              decimals: int, suffix: str) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(decimals)
        widget.setValue(value)
        widget.setSuffix(suffix)
        widget.setSingleStep(.5)
        return widget

    @staticmethod
    def _compact_form(layout: QFormLayout) -> None:
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(6)
        layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

    @staticmethod
    def _compact_group(group: QGroupBox) -> None:
        group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        for button in group.findChildren(QPushButton):
            button.setMinimumHeight(30)
            button.setMaximumHeight(36)

    def measure(self) -> None:
        epc = self.epc.text().strip()
        x, y, z = self.x_input.value(), self.y_input.value(), self.z_input.value()
        duration, power = self.duration_input.value(), self.power_input.value()
        if self.reader_mode.currentText() == "Live SA810":
            self.start_live_measurement(epc, x, y, z, duration, power)
            return
        self.measure_button.setEnabled(False)
        self.statusBar().showMessage(f"Measuring ({duration:g} s simulated)...")
        QApplication.processEvents()
        simulated_epc = epc or "E28011606000000000000000001"
        reads = SimulatedSA810.collect(simulated_epc, x, y, z, duration, power)
        self.save_measurement(epc or "ALL", x, y, z, duration, power,
                              "simulator", reads)

    def save_measurement(self, epc: str, x: float, y: float, z: float,
                         duration: float, power: float, mode: str,
                         reads: list[TagRead]) -> None:
        measurement_id = self.store.save(
            epc=epc, x=x, y=y, z=z, duration=duration, power=power,
            mode=mode, reads=reads)
        rssis = [item.rssi for item in reads]
        if rssis:
            self.summary["RSSI average"].setText(
                f"{statistics.fmean(rssis):.1f} dBm")
            self.summary["RSSI range"].setText(
                f"{min(rssis):.1f} to {max(rssis):.1f}")
            self.summary["RSSI stability"].setText(
                f"{statistics.pstdev(rssis):.1f} dB")
        else:
            self.summary["RSSI average"].setText("No read")
            self.summary["RSSI range"].setText("N/A")
            self.summary["RSSI stability"].setText("N/A")
        self.summary["Read count"].setText(str(len(reads)))
        self.summary["Read rate"].setText(f"{len(reads) / duration:.1f}/s")
        self._reload_filters(z, power)
        self.measure_button.setEnabled(True)
        if reads:
            self.statusBar().showMessage(
                f"Saved measurement #{measurement_id} with {len(reads)} individual reads")
        else:
            self.statusBar().showMessage(
                f"Saved measurement #{measurement_id} as No read at "
                f"({x:g}, {y:g}, {z:g})")
        self.refresh_heatmap()

    def reader_mode_changed(self, mode: str) -> None:
        live = mode == "Live SA810"
        self.reader_host.setEnabled(live)
        self.reader_port.setEnabled(live)
        self.connect_button.setEnabled(live)
        self.encryption_mode.setEnabled(live)
        self.apply_encryption_button.setEnabled(live)
        self.encryption_password.setEnabled(
            live and self.encryption_mode.currentText() != "None")
        if not live and self.reader.connected:
            self.reader.disconnect()
            self._set_reader_status(False, "Disconnected")
            self._set_listening(False)
        if not live:
            self.statusBar().showMessage("Ready - simulator mode")

    def encryption_mode_changed(self, mode: str) -> None:
        enabled = (mode != "None" and
                   self.reader_mode.currentText() == "Live SA810")
        self.encryption_password.setEnabled(enabled)
        if mode == "None":
            self.encryption_password.clear()
            self.encryption_password.setPlaceholderText("Not required")
        elif mode == "Pairing":
            self.encryption_password.setMaxLength(3)
            self.encryption_password.setPlaceholderText("Decimal 0-255")
        else:
            self.encryption_password.setMaxLength(5)
            self.encryption_password.setPlaceholderText("Decimal 0-65535")

    def apply_listener_encryption(self) -> None:
        if self.reader_mode.currentText() != "Live SA810":
            QMessageBox.information(self, "Live reader required",
                "Select Live SA810 before applying the listener configuration.")
            return
        if not self.reader.connected:
            QMessageBox.warning(self, "Reader disconnected",
                "Connect to the SA810 before applying the listener configuration.")
            return
        if self._live_context is not None or self._listening_for_tags:
            QMessageBox.warning(self, "Stop inventory first",
                "Stop listening and finish the current measurement before changing encryption settings.")
            return
        mode = self.encryption_mode.currentText()
        password = self.encryption_password.text()
        try:
            self.reader.set_encryption(mode, password)
        except (OSError, ConnectionError, ValueError) as error:
            QMessageBox.critical(self, "Listener configuration failed", str(error))
            return
        description = "normal, unencrypted tags" if mode == "None" else f"{mode}-encrypted tags"
        self.statusBar().showMessage(
            f"Listener configuration sent for {description}")

    def toggle_reader_connection(self) -> None:
        if self.reader.connected:
            self.reader.disconnect()
            self._set_reader_status(False, "Disconnected")
            self._set_listening(False)
            self.statusBar().showMessage("Reader disconnected")
            return
        try:
            host = self.reader_host.text().strip()
            port = int(self.reader_port.text())
            self.statusBar().showMessage(f"Connecting to {host}:{port}...")
            QApplication.processEvents()
            self.reader.connect(host, port)
            self.reader.set_power(round(self.power_input.value()))
        except (OSError, ValueError) as error:
            self._set_reader_status(False, "Connection failed")
            QMessageBox.critical(self, "SA810 connection failed", str(error))
            return
        self._set_reader_status(True, f"Connected to {host}:{port}")
        self.statusBar().showMessage(
            f"Connected to SA810 at {host}:{port}; TX power applied")

    def apply_reader_power(self) -> None:
        power = round(self.power_input.value())
        if self.reader_mode.currentText() == "Simulator":
            self.statusBar().showMessage(
                f"Simulator TX power set to {power} dBm for the next measurement")
            return
        if not self.reader.connected:
            QMessageBox.warning(self, "Reader disconnected",
                "Connect to the SA810 before applying transmit power.")
            return
        try:
            self.reader.set_power(power)
        except (OSError, ConnectionError) as error:
            QMessageBox.critical(self, "Power command failed", str(error))
            return
        self.statusBar().showMessage(
            f"TX power command sent to SA810: {power} dBm")

    def _set_reader_status(self, connected: bool, text: str) -> None:
        self.reader_status.setText(text)
        color = "#18794e" if connected else "#a33"
        self.reader_status.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.connect_button.setText("Disconnect" if connected else "Connect")

    def toggle_tag_listener(self) -> None:
        if self._listening_for_tags:
            self._set_listening(False)
            if self._live_context is None:
                self.reader.stop_inventory()
            self.statusBar().showMessage("Tag listener stopped")
            return
        if self.reader_mode.currentText() != "Live SA810":
            QMessageBox.information(self, "Live reader required",
                "Select Live SA810 and connect before listening for tags.")
            return
        if not self.reader.connected:
            QMessageBox.warning(self, "Reader disconnected",
                "Connect to the SA810 before listening for tags.")
            return
        try:
            self.reader.start_inventory()
        except (OSError, ConnectionError) as error:
            QMessageBox.critical(self, "Reader error", str(error))
            return
        self._set_listening(True)
        self.statusBar().showMessage(
            "Listening for tags - place a tag in the antenna field")

    def _set_listening(self, listening: bool) -> None:
        self._listening_for_tags = listening
        self.listen_button.setText(
            "Stop listening" if listening else "Start listening")
        if not listening:
            self.listen_status.setText("Not listening")

    def clear_detected_tags(self) -> None:
        self._tag_stats.clear()
        self.detected_tags.clear()
        self.listen_status.setText(
            "Listening - no tag yet" if self._listening_for_tags
            else "Not listening")

    def use_selected_tag(self) -> None:
        epc = self.detected_tags.currentText().strip()
        if not epc:
            QMessageBox.information(self, "No tag selected",
                "Start listening and select a detected EPC first.")
            return
        self.epc.setText(epc)
        self.statusBar().showMessage(f"Reference EPC configured: {epc}")

    def _record_detected_tag(self, tag) -> None:
        stats = self._tag_stats.setdefault(
            tag.epc, {"count": 0, "rssi": tag.rssi})
        stats["count"] = int(stats["count"]) + 1
        stats["rssi"] = tag.rssi
        if self.detected_tags.findText(tag.epc) < 0:
            self.detected_tags.addItem(tag.epc)
        if self.detected_tags.currentText() == tag.epc:
            self.listen_status.setText(
                f"{tag.rssi:.0f} dBm | {stats['count']} reads | ant {tag.antenna}")

    def start_live_measurement(self, epc: str, x: float, y: float, z: float,
                               duration: float, power: float) -> None:
        if not self.reader.connected:
            QMessageBox.warning(self, "Reader disconnected",
                "Connect to the SA810 before starting a live measurement.")
            return
        try:
            self.reader.set_power(round(power))
            if not self._listening_for_tags:
                self.reader.start_inventory()
        except (OSError, ConnectionError) as error:
            QMessageBox.critical(self, "Reader error", str(error))
            return
        self._live_reads = []
        self._live_context = (epc.upper().replace(" ", ""), x, y, z,
                              duration, power)
        self.measure_button.setEnabled(False)
        self.statusBar().showMessage(
            f"Collecting live SA810 reads for {duration:g} seconds...")
        QTimer.singleShot(round(duration * 1000), self.finish_live_measurement)

    def poll_reader(self) -> None:
        while not self.reader.messages.empty():
            message = self.reader.messages.get_nowait()
            self.statusBar().showMessage(message)
            if not self.reader.connected:
                self._set_reader_status(False, "Disconnected")
        while not self.reader.tags.empty():
            tag = self.reader.tags.get_nowait()
            if self._listening_for_tags:
                self._record_detected_tag(tag)
            if self._live_context is None:
                continue
            expected_epc = self._live_context[0]
            if expected_epc and tag.epc != expected_epc and not tag.epc.endswith(expected_epc):
                continue
            timestamp = datetime.fromtimestamp(
                tag.received_at, timezone.utc).isoformat(timespec="milliseconds")
            self._live_reads.append(TagRead(
                timestamp=timestamp, epc=tag.epc, rssi=tag.rssi,
                frequency=0.0, phase=0.0, antenna=tag.antenna))
            self.statusBar().showMessage(
                f"Live measurement: {len(self._live_reads)} reads; "
                f"latest RSSI {tag.rssi:.0f} dBm")

    def finish_live_measurement(self) -> None:
        if self._live_context is None:
            return
        if not self._listening_for_tags:
            self.reader.stop_inventory()
        self.poll_reader()
        epc, x, y, z, duration, power = self._live_context
        reads = self._live_reads
        self._live_context = None
        self._live_reads = []
        saved_epc = epc or (reads[0].epc if reads else "ALL")
        self.save_measurement(saved_epc, x, y, z, duration, power,
                              "live", reads)

    def _reload_filters(self, selected_z: float | None = None,
                        selected_power: float | None = None) -> None:
        self.z_combo.blockSignals(True)
        self.power_combo.blockSignals(True)
        z_values = self.store.filter_values("z") or [1.0]
        power_values = self.store.filter_values("power_dbm") or [30.0]
        self.z_combo.clear()
        self.z_combo.addItems([f"{v:g}" for v in z_values])
        self.power_combo.clear()
        self.power_combo.addItems([f"{v:g}" for v in power_values])
        self.z_combo.setCurrentText(f"{selected_z if selected_z is not None else z_values[0]:g}")
        self.power_combo.setCurrentText(f"{selected_power if selected_power is not None else power_values[0]:g}")
        self.z_combo.blockSignals(False)
        self.power_combo.blockSignals(False)

    def refresh_heatmap(self, _value=None) -> None:
        try:
            z = float(self.z_combo.currentText())
            power = float(self.power_combo.currentText())
        except ValueError:
            return
        self.heatmap.draw_rows(
            self.store.positions(z, power), self.metric_combo.currentText(),
            z, power)

    def show_point(self, row: sqlite3.Row) -> None:
        if row["read_count"] == 0:
            self.point_label.setText(
                f"Point ({row['x']:g}, {row['y']:g}, {row['z']:g}) m | "
                "No read | 0 reads | 0.0 reads/s | RSSI N/A")
            return
        self.point_label.setText(
            f"Point ({row['x']:g}, {row['y']:g}, {row['z']:g}) m | "
            f"RSSI {row['rssi_average']:.1f} dBm "
            f"[{row['rssi_min']:.1f}, {row['rssi_max']:.1f}] | "
            f"{row['read_count']} reads | {row['read_rate']:.1f} reads/s | "
            f"stability {row['rssi_stability']:.1f} dB")

    def export_csv(self) -> None:
        destination, _ = QFileDialog.getSaveFileName(
            self, "Export RFID reads", str(APP_DIR / "rfid_reads.csv"),
            "CSV files (*.csv)")
        if destination:
            count = self.store.export_csv(Path(destination))
            self.statusBar().showMessage(
                f"Exported {count} reads to {destination}")

    def import_csv(self) -> None:
        source, _ = QFileDialog.getOpenFileName(
            self, "Import RFID reads", str(APP_DIR), "CSV files (*.csv)")
        if not source:
            return
        try:
            measurements, reads, z, power = self.store.import_csv(Path(source))
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Import failed", str(error))
            return
        self._reload_filters(z, power)
        self.refresh_heatmap()
        self.statusBar().showMessage(
            f"Imported {reads} reads in {measurements} measurements from {source}")

    def export_heatmap_report(self) -> None:
        destination, _ = QFileDialog.getSaveFileName(
            self, "Export heatmap and grid data",
            str(APP_DIR / "rfid_heatmap_report.png"),
            "PNG image (*.png)")
        if not destination:
            return
        if not destination.lower().endswith(".png"):
            destination += ".png"
        try:
            point_count = self.heatmap.export_report(Path(destination))
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Export failed", str(error))
            return
        self.statusBar().showMessage(
            f"Exported heatmap and {point_count} grid rows to {destination}")

    def reset_ui(self) -> None:
        answer = QMessageBox.question(
            self, "Reset all grid data",
            "Delete every saved grid measurement and raw RFID read?\n\n"
            "This cannot be undone. Export the heatmap or CSV first if you "
            "need to keep the results.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        measurement_count, read_count = self.store.clear_all()
        self._reset_controls()
        self.statusBar().showMessage(
            f"Reset complete: deleted {measurement_count} grid measurements "
            f"and {read_count} raw reads")

    def _reset_controls(self) -> None:
        if self.reader.connected:
            self.reader.disconnect()
        self._live_context = None
        self._live_reads = []
        self._set_reader_status(False, "Disconnected")
        self._set_listening(False)
        self.clear_detected_tags()
        self.reader_mode.setCurrentText("Live SA810")
        self.reader_host.setText("192.168.2.120")
        self.reader_port.setText("49152")
        self.power_input.setValue(15)
        self.epc.clear()
        self.x_input.setValue(0)
        self.y_input.setValue(0)
        self.z_input.setValue(1)
        self.duration_input.setValue(5)
        self.metric_combo.blockSignals(True)
        self.z_combo.blockSignals(True)
        self.power_combo.blockSignals(True)
        self.metric_combo.setCurrentText("RSSI Average")
        self.z_combo.clear()
        self.z_combo.addItem("1")
        self.power_combo.clear()
        self.power_combo.addItem("15")
        self.metric_combo.blockSignals(False)
        self.z_combo.blockSignals(False)
        self.power_combo.blockSignals(False)
        for label in self.summary.values():
            label.setText("-")
        self.point_label.setText(
            "Collect a point, then click its heatmap cell for details.")
        self.heatmap.draw_rows([], "RSSI Average", 1, 15)
        self.measure_button.setEnabled(True)

    def closeEvent(self, event) -> None:
        self.reader_timer.stop()
        self._set_listening(False)
        self.reader.disconnect()
        self.store.close()
        event.accept()


def main() -> None:
    application = QApplication(sys.argv)
    application.setStyle("Fusion")
    window = CoverageWindow()
    window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
