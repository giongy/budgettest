import sys
from pathlib import Path
from typing import Any
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QHeaderView, QMessageBox, QFileDialog,
    QSizePolicy, QAbstractItemView, QToolButton, QStyle, QFrame,
    QDialog, QTableWidget, QTableWidgetItem, QAbstractScrollArea,
)
from PyQt6.QtGui import QStandardItemModel, QColor, QFont, QBrush, QIcon, QStandardItem
from PyQt6.QtCore import Qt, QTimer, QModelIndex, QSize
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from . import config
from .repository import (
    load_budgetyear_map,
    load_categories,
    fetch_actuals_for_year,
    load_budgets_for_year,
    upsert_budget_entry,
    delete_budget_entry,
)
from .ui import (
    make_item,
    PeriodDelegate,
    ButtonDelegate,
    DividerDelegate,
    SummaryHeaderView,
    BudgetTreeView,
    CategoryDetailDelegate,
)
from .style import (
    CATEGORY_COLUMN_WIDTH,
    PERIOD_COLUMN_WIDTH,
    NUMERIC_COLUMN_WIDTH,
    MIN_COLUMN_WIDTH,
    MAIN_CATEGORY_BG,
    DIFF_POSITIVE_COLOR,
    DIFF_NEGATIVE_COLOR,
    UI_FONT_FAMILY,
    UI_BASE_FONT_SIZE,
    UI_BOLD_FONT_SIZE,
    DIFF_FONT_SIZE,
    WINDOW_SCALE_RATIO,
    CHART_HEIGHT,
    CALCULATED_BUDGET_COLOR,
    SUMMARY_ACTUAL_POSITIVE_COLOR,
    SUMMARY_ACTUAL_NEGATIVE_COLOR,
    SUMMARY_BUDGET_POSITIVE_COLOR,
    SUMMARY_BUDGET_NEGATIVE_COLOR,
    SUMMARY_DIFF_POSITIVE_COLOR,
    SUMMARY_DIFF_NEGATIVE_BG_COLOR,
    SUMMARY_DIFF_NEGATIVE_FG_COLOR,
    SUMMARY_FONT_SIZE,
    DETAIL_FONT_FAMILY,
    DETAIL_FONT_SIZE,
)

ITALIAN_MONTH_NAMES = {
    "01": "Gennaio",
    "02": "Febbraio",
    "03": "Marzo",
    "04": "Aprile",
    "05": "Maggio",
    "06": "Giugno",
    "07": "Luglio",
    "08": "Agosto",
    "09": "Settembre",
    "10": "Ottobre",
    "11": "Novembre",
    "12": "Dicembre",
}


def get_resource_path(name: str) -> Path:
    """Return resource path, compatible with PyInstaller one-file bundles."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / name


def format_diff_value(value: float) -> str:
    return "0" if abs(value) < 1e-6 else f"{value:,.2f}"


def diff_background(value: float) -> QBrush:
    return QBrush(DIFF_POSITIVE_COLOR if value >= 0 else DIFF_NEGATIVE_COLOR)


def annual_total_from_period(amount, period, months_count):
    months = months_count or 12
    if amount is None:
        return 0.0
    amount = float(amount)
    if period == "Yearly":
        return amount
    if period == "Quarterly":
        return amount * 4.0
    if period == "Weekly":
        return amount * 52.0
    return amount * months


def compute_budget_distribution(year_amount, year_period, month_bids, overrides):
    months_count = len(month_bids) or 0
    expected_counts = {
        "Monthly": 12,
        "Yearly": 12,
        "Weekly": 12,
        "Quarterly": 4,
    }
    expected_count = expected_counts.get(year_period, 12 if months_count == 0 else months_count)
    months_for_total = expected_count or months_count or 12

    annual_total = annual_total_from_period(year_amount, year_period, months_for_total)
    overrides = {bid: float(val) for bid, val in overrides.items() if val is not None}
    sum_overrides = sum(overrides.values())
    missing_bids = [bid for bid in month_bids if bid not in overrides]

    has_annual = year_amount is not None and (year_period not in (None, "", "None"))
    if not has_annual:
        values = {}
        for bid in month_bids:
            values[bid] = overrides.get(bid, 0.0)
        return values, sum_overrides, False, set(overrides.keys())

    limited_view = bool(expected_count) and 0 < len(month_bids) < expected_count
    over_limit = False
    if annual_total is not None:
        over_limit = abs(sum_overrides) > abs(annual_total)

    values = {}
    if limited_view:
        for bid in month_bids:
            values[bid] = overrides.get(bid, 0.0) or 0.0
        if year_amount is not None:
            total_display = annual_total
        else:
            total_display = sum(values.values())
            over_limit = False
        return values, total_display, over_limit, set(overrides.keys())

    if over_limit:
        total_display = sum_overrides
        for bid in month_bids:
            values[bid] = overrides.get(bid, 0.0)
        return values, total_display, over_limit, set(overrides.keys())

    total_display = annual_total
    if not missing_bids:
        for bid in month_bids:
            values[bid] = overrides.get(bid, 0.0)
        return values, sum_overrides, over_limit, set(overrides.keys())

    remainder = annual_total - sum_overrides
    share = remainder / len(missing_bids) if missing_bids else 0.0
    for bid in month_bids:
        if bid in overrides:
            values[bid] = overrides[bid]
        else:
            values[bid] = share
    if missing_bids:
        diff = total_display - sum(values.values())
        if abs(diff) > 1e-6:
            last = missing_bids[-1]
            values[last] += diff
    return values, total_display, over_limit, set(overrides.keys())


class CategoryDetailDialog(QDialog):
    def __init__(self, parent, category_name: str, main_category_name: str, data_provider, copy_handler, value_handler):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(f"Dettaglio categoria - {category_name}")
        self.data_provider = data_provider
        self.copy_handler = copy_handler
        self.value_handler = value_handler
        self._category_name = category_name
        self._main_category_name = main_category_name
        self._bulk_budget_indexes: list[QModelIndex] = []
        popup_font = QFont(DETAIL_FONT_FAMILY, DETAIL_FONT_SIZE)
        self.setFont(popup_font)
        self._item_font = QFont(popup_font)
        # Previous default: QFont("Courier New", 14)
        self.setMinimumSize(360, 300)
        self.resize(620, 700)
        self.setSizeGripEnabled(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        header_font = QFont(self._item_font)
        header_font.setBold(True)
        self.header_label = QLabel(self)
        self.header_label.setFont(header_font)
        self.header_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.header_label.setText(self._compose_header_text())
        layout.addWidget(self.header_label)
        layout.addSpacing(8)
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["Periodo", "Actual", "Budget", "Diff", ""])
        self.table.setFont(popup_font)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setMinimumSectionSize(0)
        self.table.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self.table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        header = self.table.horizontalHeader()
        header.setFont(popup_font)
        for col in range(self.table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        header.setMinimumSectionSize(40)
        header.resizeSection(0, 150)
        header.resizeSection(1, 120)
        header.resizeSection(2, 110)
        header.resizeSection(3, 120)
        header.resizeSection(4, 45)
        layout.addSpacing(6)
        layout.addWidget(self.table, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(12)

        bulk_layout = QHBoxLayout()
        bulk_layout.setSpacing(8)
        bulk_label = QLabel("Valore budget:")
        bulk_label.setFont(self._item_font)
        bulk_layout.addStretch()
        bulk_layout.addWidget(bulk_label)
        self.bulk_input = QLineEdit()
        self.bulk_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.bulk_input.setPlaceholderText("0,00")
        self.bulk_input.setClearButtonEnabled(True)
        self.bulk_input.setFixedWidth(120)
        bulk_layout.addWidget(self.bulk_input)
        self.monthly_btn = QPushButton("Mensile")
        self.monthly_btn.clicked.connect(self._apply_monthly_value)
        bulk_layout.addWidget(self.monthly_btn)
        self.annual_btn = QPushButton("Annuale")
        self.annual_btn.clicked.connect(self._apply_annual_value)
        bulk_layout.addWidget(self.annual_btn)
        bulk_layout.addStretch()
        layout.addLayout(bulk_layout)
        layout.addSpacing(12)

        self.chart_figure = Figure(figsize=(4.8, 2.4), dpi=100)
        self.chart_canvas = FigureCanvasQTAgg(self.chart_figure)
        self.chart_canvas.setMinimumHeight(200)
        self.chart_canvas.setMaximumHeight(260)
        self.chart_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.chart_canvas)
        layout.addSpacing(12)

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()
        close_btn = QPushButton("Chiudi")
        close_btn.clicked.connect(self.close)
        buttons_layout.addWidget(close_btn)
        layout.addLayout(buttons_layout)

        icon_path = get_resource_path("pari.png")
        if icon_path.exists():
            self.copy_icon = QIcon(str(icon_path))
        else:
            self.copy_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)

        self._reloading = False
        self.table.itemChanged.connect(self._on_item_changed)
        self._reload()

    def _compose_header_text(self) -> str:
        main_name = (self._main_category_name or "").strip()
        category_name = (self._category_name or "").strip()
        if main_name and category_name and main_name != category_name:
            return f"{main_name} / {category_name}"
        return category_name or main_name or ""

    def _reload(self):
        self._reloading = True
        rows = self.data_provider() or []
        separator_rows: list[int] = []
        self.table.setRowCount(len(rows))
        self._bulk_budget_indexes = []
        for row_idx, row in enumerate(rows):
            row_role = row.get("row_role", "month")

            if row_role == "separator":
                self.table.setSpan(row_idx, 0, 1, self.table.columnCount())
                line_widget = QWidget(self.table)
                line_widget.setMinimumHeight(2)
                line_widget.setMaximumHeight(2)
                line_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                line_widget.setStyleSheet("background-color: #000000; margin: 0px; padding: 0px;")
                self.table.setCellWidget(row_idx, 0, line_widget)
                self.table.verticalHeader().setSectionResizeMode(row_idx, QHeaderView.ResizeMode.Fixed)
                self.table.setRowHeight(row_idx, 2)
                separator_rows.append(row_idx)
                continue

            self.table.verticalHeader().setSectionResizeMode(row_idx, QHeaderView.ResizeMode.ResizeToContents)
            label_item = QTableWidgetItem(row.get("label", ""))
            label_item.setForeground(QBrush(QColor("#111")))
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            label_item.setFont(self._item_font)
            self.table.setItem(row_idx, 0, label_item)

            actual_item = QTableWidgetItem(row.get("actual_text", "0"))
            actual_color = row.get("actual_color")
            if actual_color:
                actual_item.setForeground(QBrush(actual_color))
            actual_item.setFlags(actual_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            actual_item.setFont(self._item_font)
            self.table.setItem(row_idx, 1, actual_item)

            budget_item = QTableWidgetItem(row.get("budget_text", "0"))
            budget_color = row.get("budget_color")
            if budget_color:
                budget_item.setForeground(QBrush(budget_color))
            budget_index = row.get("budget_index")
            if budget_index and budget_index.isValid():
                budget_item.setFlags(budget_item.flags() | Qt.ItemFlag.ItemIsEditable)
            else:
                budget_item.setFlags(budget_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            budget_item.setData(Qt.ItemDataRole.UserRole, budget_index)
            budget_item.setFont(self._item_font)
            self.table.setItem(row_idx, 2, budget_item)

            diff_item = QTableWidgetItem(row.get("diff_text", "0"))
            diff_bg = row.get("diff_background")
            if diff_bg:
                diff_item.setBackground(diff_bg)
            diff_item.setFlags(diff_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            diff_item.setFont(self._item_font)
            self.table.setItem(row_idx, 3, diff_item)

            index = row.get("budget_index")
            if index and index.isValid():
                btn = QToolButton(self.table)
                btn.setIcon(self.copy_icon)
                btn.setAutoRaise(True)
                btn.setToolTip("Imposta il budget uguale all'actual")
                btn.setFont(self._item_font)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda checked=False, idx=index: self._copy_and_refresh(idx))
                self.table.setCellWidget(row_idx, 4, btn)
                if row_role == "month":
                    self._bulk_budget_indexes.append(index)
            else:
                dummy = QWidget(self.table)
                self.table.setCellWidget(row_idx, 4, dummy)

            if row_role == "total":
                for col in range(0, 5):
                    item = self.table.item(row_idx, col)
                    if item is not None:
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)

        self._update_chart(rows)

        self.table.resizeRowsToContents()
        width_targets = {0: 150, 1: 120, 2: 110, 3: 120, 4: 45}
        for col, target in width_targets.items():
            if col < self.table.columnCount():
                self.table.setColumnWidth(col, target)
        self.table.horizontalHeader().setStretchLastSection(False)

        for idx in separator_rows:
            self.table.verticalHeader().setSectionResizeMode(idx, QHeaderView.ResizeMode.Fixed)
            self.table.setRowHeight(idx, 2)
        total_width = self.table.verticalHeader().width() + self.table.frameWidth() * 2
        if self.table.verticalScrollBar().isVisible():
            total_width += self.table.verticalScrollBar().width()
        for col in range(self.table.columnCount()):
            total_width += self.table.columnWidth(col)
        self.table.setMinimumWidth(total_width)
        self.table.setMaximumWidth(total_width)

        total_height = self.table.horizontalHeader().height() + self.table.frameWidth() * 2
        if self.table.horizontalScrollBar().isVisible():
            total_height += self.table.horizontalScrollBar().height()
        for row in range(self.table.rowCount()):
            total_height += self.table.rowHeight(row)
        self.table.setMinimumHeight(total_height)
        self.table.setMaximumHeight(total_height)
        self._reloading = False

    def _copy_and_refresh(self, model_index):
        if self.copy_handler:
            self.copy_handler(model_index)
        self._reload()

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._reloading:
            return
        if item.column() != 2:
            return
        model_index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(model_index, QModelIndex) or not model_index.isValid():
            return
        if self.value_handler:
            self.value_handler(model_index, item.text())
        self._reload()

    def _update_chart(self, rows: list[dict[str, Any]]):
        if not hasattr(self, "chart_figure"):
            return
        self.chart_figure.clear()
        ax = self.chart_figure.add_subplot(111)
        ax.set_facecolor("#ffffff")
        labels: list[str] = []
        values: list[float] = []

        for row in rows or []:
            if row.get("row_role") != "month":
                continue
            label = str(row.get("label", "")).strip()
            val = row.get("actual_value")
            if val is None:
                try:
                    text = str(row.get("actual_text", "0")).replace(" ", "").replace(",", "")
                    val = float(text) if text else 0.0
                except Exception:
                    val = 0.0
            try:
                val_float = float(val)
            except (TypeError, ValueError):
                val_float = 0.0
            labels.append(label or "")
            values.append(val_float)

        if not values:
            self.chart_figure.subplots_adjust(left=0.1, right=0.9, top=0.85, bottom=0.25)
            ax.axis("off")
            ax.text(
                0.5,
                0.5,
                "Nessun dato disponibile",
                ha="center",
                va="center",
                fontsize=9,
                color="#4b5563",
            )
            self.chart_canvas.draw_idle()
            return

        xs = list(range(len(values)))
        ax.plot(
            xs,
            values,
            color="#2c7a7b",
            marker="o",
            linewidth=2,
            markersize=5,
            label="Actual",
        )
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8, color="#1f2937")
        ax.tick_params(axis="y", labelsize=8, colors="#1f2937")
        ax.set_title("Actual - andamento mensile", fontsize=10, color="#1f2937", pad=8)
        ax.grid(axis="y", color="#e5e7eb", linestyle="--", linewidth=0.8, alpha=0.7)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.margins(x=0.05, y=0.2)
        handles, labels_legend = ax.get_legend_handles_labels()
        if labels_legend:
            ax.legend(loc="upper right", fontsize=8, frameon=False)
        self.chart_figure.subplots_adjust(left=0.12, right=0.97, top=0.9, bottom=0.32)
        self.chart_canvas.draw_idle()

    def _parse_bulk_input(self) -> float | None:
        text = (self.bulk_input.text() or "").strip()
        if not text:
            return None
        cleaned = (
            text.replace("€", "")
            .replace("\u202f", "")
            .replace("\u00a0", "")
            .replace(" ", "")
        )
        if not cleaned:
            return None
        if "," in cleaned and "." in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "")
                cleaned = cleaned.replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            cleaned = cleaned.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _show_bulk_input_error(self):
        QMessageBox.warning(self, "Valore non valido", "Inserisci un importo numerico valido.")

    def _apply_monthly_value(self):
        amount = self._parse_bulk_input()
        if amount is None:
            self._show_bulk_input_error()
            return
        if not self._bulk_budget_indexes:
            QMessageBox.information(self, "Nessun mese disponibile", "Non ci sono mesi modificabili per questa categoria.")
            return
        self._apply_values_to_indexes([amount] * len(self._bulk_budget_indexes))

    def _apply_annual_value(self):
        amount = self._parse_bulk_input()
        if amount is None:
            self._show_bulk_input_error()
            return
        if not self._bulk_budget_indexes:
            QMessageBox.information(self, "Nessun mese disponibile", "Non ci sono mesi modificabili per questa categoria.")
            return
        monthly_value = amount / 12.0
        values = [monthly_value] * len(self._bulk_budget_indexes)
        rounded_values = [round(v, 2) for v in values]
        diff = round(amount - sum(rounded_values), 2)
        if rounded_values and abs(diff) > 1e-6:
            rounded_values[-1] += diff
        self._apply_values_to_indexes(rounded_values)

    def _apply_values_to_indexes(self, values: list[float]):
        if not self.value_handler:
            return
        for idx, val in zip(self._bulk_budget_indexes, values):
            if isinstance(idx, QModelIndex) and idx.isValid():
                self.value_handler(idx, f"{val:,.2f}")
        self._reload()
        self.bulk_input.setFocus()
        self.bulk_input.selectAll()


class BudgetApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Budget Manager - Luca")
        icon_path = get_resource_path("money.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        screen = QApplication.primaryScreen().availableGeometry()
        width = int(screen.width() * WINDOW_SCALE_RATIO)
        height = int(screen.height() * WINDOW_SCALE_RATIO)
        self.resize(width, height)
        self.move(screen.center() - self.rect().center())
        self.years, self.per_year_entries, self.name_to_id = load_budgetyear_map()
        self.id2name, self.children_map, self.root_ids = load_categories()
        self.edits = {}
        self._recalc_guard = False  # prevents saving of auto-calculated updates
        self._has_unsaved_changes = False
        self._save_default_stylesheet = (
            "QPushButton { background-color: #ffeb3b; border: 1px solid #bfa400; color: #000; font-weight: bold; } "
            "QPushButton:hover { background-color: #ffe066; }"
        )
        self._save_dirty_stylesheet = (
            "QPushButton { background-color: #c62828; border: 1px solid #8e0000; color: #fff; font-weight: bold; } "
            "QPushButton:hover { background-color: #d32f2f; }"
        )

        layout = QVBoxLayout(self)

        self.control_frame = QFrame()
        self.control_frame.setObjectName("controlPanel")
        self.control_frame.setStyleSheet(
            "#controlPanel { border: 2px solid #000; border-radius: 6px; background-color: #f6f7fb; }"
        )
        control_layout = QHBoxLayout(self.control_frame)
        control_layout.setContentsMargins(10, 6, 10, 6)
        control_layout.setSpacing(12)

        self.select_db_btn = QPushButton("Select DB")
        self.select_db_btn.setMinimumWidth(100)
        self.select_db_btn.clicked.connect(self.select_db)
        control_layout.addWidget(self.select_db_btn)

        self.db_label = QLabel()
        self.db_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.db_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        control_layout.addWidget(self.db_label, stretch=1)

        year_label = QLabel("Year:")
        control_layout.addWidget(year_label)
        self.year_cb = QComboBox()
        self.year_cb.addItems(self.years or [])
        self.year_cb.setMinimumWidth(110)
        last_year = config.load_last_budget_year()
        if last_year and last_year in (self.years or []):
            self.year_cb.setCurrentText(last_year)
        self.year_cb.currentTextChanged.connect(self._on_year_changed)
        control_layout.addWidget(self.year_cb)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setMinimumWidth(100)
        self.refresh_btn.clicked.connect(self.refresh)
        control_layout.addWidget(self.refresh_btn)

        self.save_btn = QPushButton("Save Budgets")
        self.save_btn.setMinimumWidth(120)
        self._set_unsaved_changes(False)
        self.save_btn.clicked.connect(self.save_budgets)
        control_layout.addWidget(self.save_btn)

        # Expand/Collapse all main categories
        self.expand_all_btn = QToolButton()
        self.expand_all_btn.setAutoRaise(True)
        self.expand_all_btn.setToolTip("Expand all categories")
        self.expand_all_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.expand_all_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarUnshadeButton))
        self.expand_all_btn.setIconSize(QSize(14, 14))
        self.expand_all_btn.setFixedSize(QSize(22, 22))
        self.expand_all_btn.clicked.connect(self.expand_all_main)
        control_layout.addWidget(self.expand_all_btn)
        self.collapse_all_btn = QToolButton()
        self.collapse_all_btn.setAutoRaise(True)
        self.collapse_all_btn.setToolTip("Collapse all categories")
        self.collapse_all_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.collapse_all_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarShadeButton))
        self.collapse_all_btn.setIconSize(QSize(14, 14))
        self.collapse_all_btn.setFixedSize(QSize(22, 22))
        self.collapse_all_btn.clicked.connect(self.collapse_all_main)
        control_layout.addWidget(self.collapse_all_btn)

        layout.addWidget(self.control_frame)

        self.figure = Figure(figsize=(6, CHART_HEIGHT / 100), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setFixedHeight(CHART_HEIGHT)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.canvas)

        self.view = BudgetTreeView()
        self.summary_header = SummaryHeaderView(self.view)
        self.view.setHeader(self.summary_header)
        self.summary_cumulative_mode = False
        self.summary_header.configure_toggle(None, self.summary_cumulative_mode, self._on_summary_toggle_requested)
        self.summary_header.sectionDoubleClicked.connect(self._on_header_section_double_clicked)
        self.model = QStandardItemModel()
        self.view.setModel(self.model)
        self.view.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        header = self.view.header()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(MIN_COLUMN_WIDTH)
        header.setDefaultSectionSize(NUMERIC_COLUMN_WIDTH)
        layout.addWidget(self.view)

        self.default_delegate = self.view.itemDelegate()
        self.period_delegate = PeriodDelegate()
        self.budget_button_delegate = ButtonDelegate(self.view, self.apply_actual_to_budget)
        self.total_divider_delegate = DividerDelegate(self.view)
        self.category_detail_delegate = CategoryDetailDelegate(self.view, self._on_category_detail_requested)
        self._collapsed_main: set[int] = set()
        self.view.doubleClicked.connect(self.on_view_double_clicked)
        self.current_headers: list[str] = []
        self.category_label_items: dict[Any, QStandardItem] = {}
        self.apply_light_theme()
        self._db_label_fulltext = ""
        self._set_db_path_label(config.DB_PATH)
        QTimer.singleShot(0, self._update_db_label_text)
        self.refresh()

    def _set_unsaved_changes(self, dirty: bool):
        self._has_unsaved_changes = dirty
        stylesheet = self._save_dirty_stylesheet if dirty else self._save_default_stylesheet
        if hasattr(self, "save_btn"):
            self.save_btn.setStyleSheet(stylesheet)

    def showEvent(self, event):
        super().showEvent(event)
        # Redraw charts once the widget has a real size; avoids narrow plots on first load
        QTimer.singleShot(0, self.update_summary_chart)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_db_label_text()

    def _set_db_path_label(self, path: Path | str | None):
        display_path = str(path) if path else "--"
        full_text = f"Database: {display_path}"
        self._db_label_fulltext = full_text
        tooltip = display_path if path else "Nessun database selezionato"
        self.db_label.setToolTip(tooltip)
        self._update_db_label_text()

    def _update_db_label_text(self):
        if not hasattr(self, "db_label"):
            return
        full_text = getattr(self, "_db_label_fulltext", "")
        if not full_text:
            self.db_label.setText("")
            return
        available = max(self.db_label.width() - 6, 0)
        if available <= 0:
            self.db_label.setText(full_text)
            QTimer.singleShot(0, self._update_db_label_text)
            return
        metrics = self.db_label.fontMetrics()
        elided = metrics.elidedText(full_text, Qt.TextElideMode.ElideMiddle, available)
        self.db_label.setText(elided)

    def _display_header_name(self, raw: str) -> str:
        if isinstance(raw, str) and len(raw) == 7 and raw[4] == "-":
            month_code = raw[5:]
            return ITALIAN_MONTH_NAMES.get(month_code, raw)
        return raw

    def _apply_column_widths(self, header_names):
        header = self.view.header()
        for col, name in enumerate(header_names):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            if col == 0:
                width = CATEGORY_COLUMN_WIDTH
            elif name == "Period":
                width = PERIOD_COLUMN_WIDTH
            else:
                width = NUMERIC_COLUMN_WIDTH
            header.resizeSection(col, width)
            self.view.setColumnWidth(col, width)

    def _highlight_current_month_column(self):
        if not self.current_headers:
            return
        year_text = self.year_cb.currentText() if hasattr(self, "year_cb") else ""
        if not year_text or len(year_text) != 4 or not year_text.isdigit():
            return
        now = datetime.now()
        month_code = f"{year_text}-{now.month:02d}"
        candidates = {month_code, self._display_header_name(month_code)}
        target_idx = None
        for idx, name in enumerate(self.current_headers):
            if name in candidates:
                target_idx = idx
                break
        if target_idx is None:
            return
        if target_idx < 3 or target_idx >= len(self.current_headers) - 1:
            return
        self.view.set_highlighted_columns({target_idx})
        self.summary_header.set_highlighted_sections({target_idx})

    def _on_year_changed(self, year: str):
        if year:
            config.save_last_budget_year(year)
        self.refresh()

    def _compute_summary_totals(
        self, header_names: list[str]
    ) -> dict[int, dict[str, float]]:
        totals: dict[int, dict[str, float]] = {}

        def _accumulate(value_map: dict[str, float], key: str, text: str | None) -> float:
            raw = (text or "").replace(" ", "").replace(",", "")
            try:
                val = float(raw) if raw else 0.0
            except ValueError:
                val = 0.0
            value_map[key] = value_map.get(key, 0.0) + val
            return val

        root = self.model.invisibleRootItem()
        column_count = self.model.columnCount()
        for r in range(root.rowCount()):
            category_item = root.child(r, 0)
            if not category_item:
                continue
            budget_row_idx = None
            actual_row_idx = None
            for rr in range(category_item.rowCount()):
                label_item = category_item.child(rr, 0)
                if not label_item:
                    continue
                label_text = label_item.text()
                if label_text == "Budget":
                    budget_row_idx = rr
                elif label_text == "Actual":
                    actual_row_idx = rr
            if budget_row_idx is None and actual_row_idx is None:
                continue
            category_name = (category_item.text() or "").strip()
            for col in range(1, column_count):
                if col >= len(header_names):
                    continue
                if header_names[col] == "Period":
                    continue
                bucket = totals.setdefault(col, {})
                actual_val = 0.0
                budget_val = 0.0
                if budget_row_idx is not None:
                    cell = category_item.child(budget_row_idx, col)
                    if cell:
                        budget_val = _accumulate(bucket, "budget", cell.text())
                if actual_row_idx is not None:
                    cell = category_item.child(actual_row_idx, col)
                    if cell:
                        actual_val = _accumulate(bucket, "actual", cell.text())
                diff_value = actual_val - budget_val
                if abs(diff_value) > 1e-6:
                    bucket.setdefault("contributors", []).append((category_name, diff_value))
        for data in totals.values():
            actual_total = data.get("actual", 0.0)
            budget_total = data.get("budget", 0.0)
            data["diff"] = actual_total - budget_total
        return totals

    def _on_summary_toggle_requested(self):
        self.summary_cumulative_mode = not self.summary_cumulative_mode
        self._update_summary_header()

    def _update_summary_header(self, header_names: list[str] | None = None):
        if header_names is None:
            header_names = self.current_headers
        toggle_column: int | None = None
        if header_names:
            try:
                toggle_column = header_names.index("Period")
            except ValueError:
                if len(header_names) > 2:
                    toggle_column = 2
        if not header_names:
            self.summary_header.configure_toggle(None, self.summary_cumulative_mode, self._on_summary_toggle_requested)
            self.summary_header.set_summary({})
            return
        totals = self._compute_summary_totals(header_names)
        summary: dict[int, Any] = {}
        model_column_count = self.model.columnCount()
        total_col_index = model_column_count - 1 if model_column_count > 0 else len(header_names) - 1
        summary[0] = {
            "background": QBrush(QColor("#E8EAED")),
            "alignment": Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "lines": [
                {"text": "ACTUAL", "bg": SUMMARY_ACTUAL_POSITIVE_COLOR},
                {"text": "BUDGET", "bg": SUMMARY_BUDGET_POSITIVE_COLOR},
                {"text": "DIFF", "bg": SUMMARY_DIFF_POSITIVE_COLOR},
            ],
        }

        ordered_columns = [
            col
            for col in range(1, len(header_names))
            if header_names[col] != "Period" and col != 1
        ]
        actual_by_col: dict[int, float] = {}
        budget_by_col: dict[int, float] = {}
        diff_by_col: dict[int, float] = {}
        contributors_by_col: dict[int, list[tuple[str, float]]] = {}
        for col in ordered_columns:
            col_totals = totals.get(col, {})
            budget_val = col_totals.get("budget", 0.0)
            actual_val = col_totals.get("actual", 0.0)
            diff_val = col_totals.get("diff", actual_val - budget_val)
            actual_by_col[col] = actual_val
            budget_by_col[col] = budget_val
            diff_by_col[col] = diff_val
            contributors_by_col[col] = list(col_totals.get("contributors", []))

        month_columns = [
            col for col in ordered_columns if header_names[col].upper() != "TOTAL"
        ]
        cumulative_actual_by_col: dict[int, float] = {}
        cumulative_budget_by_col: dict[int, float] = {}
        cumulative_diff_by_col: dict[int, float] = {}
        cumulative_contributors_by_col: dict[int, list[tuple[str, float]]] = {}
        running_actual = running_budget = running_diff = 0.0
        running_contributors: dict[str, float] = {}
        for col in month_columns:
            actual_val = actual_by_col.get(col, 0.0)
            budget_val = budget_by_col.get(col, 0.0)
            diff_val = diff_by_col.get(col, actual_val - budget_val)
            running_actual += actual_val
            running_budget += budget_val
            running_diff += diff_val
            cumulative_actual_by_col[col] = running_actual
            cumulative_budget_by_col[col] = running_budget
            cumulative_diff_by_col[col] = running_diff
            for name, value in contributors_by_col.get(col, []):
                running_contributors[name] = running_contributors.get(name, 0.0) + value
            cumulative_contributors_by_col[col] = [
                (name, value)
                for name, value in running_contributors.items()
                if abs(value) > 1e-6
            ]
        if total_col_index in ordered_columns:
            if month_columns:
                cumulative_actual_by_col[total_col_index] = running_actual
                cumulative_budget_by_col[total_col_index] = running_budget
                cumulative_diff_by_col[total_col_index] = running_diff
                cumulative_contributors_by_col[total_col_index] = [
                    (name, value)
                    for name, value in running_contributors.items()
                    if abs(value) > 1e-6
                ]
            else:
                cumulative_actual_by_col[total_col_index] = actual_by_col.get(total_col_index, 0.0)
                cumulative_budget_by_col[total_col_index] = budget_by_col.get(total_col_index, 0.0)
                cumulative_diff_by_col[total_col_index] = diff_by_col.get(total_col_index, 0.0)
                cumulative_contributors_by_col[total_col_index] = [
                    (name, value)
                    for name, value in contributors_by_col.get(total_col_index, [])
                    if abs(value) > 1e-6
                ]

        def build_summary_entry(
            label: str,
            actual_value: float,
            budget_value: float,
            diff_value: float,
            contributors: list[tuple[str, float]] | None,
        ) -> dict[str, Any]:
            actual_bg = (
                SUMMARY_ACTUAL_POSITIVE_COLOR
                if actual_value >= 0
                else SUMMARY_ACTUAL_NEGATIVE_COLOR
            )
            budget_bg = (
                SUMMARY_BUDGET_POSITIVE_COLOR
                if budget_value >= 0
                else SUMMARY_BUDGET_NEGATIVE_COLOR
            )
            if diff_value >= 0:
                diff_bg = SUMMARY_DIFF_POSITIVE_COLOR
                diff_fg: QColor | None = None
            else:
                diff_bg = SUMMARY_DIFF_NEGATIVE_BG_COLOR
                diff_fg = SUMMARY_DIFF_NEGATIVE_FG_COLOR
            diff_line = {
                "text": format_diff_value(diff_value),
                "bg": diff_bg,
            }
            if diff_fg is not None:
                diff_line["fg"] = diff_fg
            entry = {
                "background": QBrush(QColor("#E8EAED")),
                "alignment": Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "lines": [
                    {"text": format_diff_value(actual_value), "bg": actual_bg},
                    {"text": format_diff_value(budget_value), "bg": budget_bg},
                    diff_line,
                ],
            }
            tooltip = self._format_diff_tooltip(label, diff_value, contributors)
            if tooltip:
                entry["tooltip"] = tooltip
            return entry

        for col in ordered_columns:
            label = header_names[col] if col < len(header_names) else ""
            actual_val = actual_by_col.get(col, 0.0)
            budget_val = budget_by_col.get(col, 0.0)
            diff_val = diff_by_col.get(col, actual_val - budget_val)
            if self.summary_cumulative_mode:
                actual_display = cumulative_actual_by_col.get(col, actual_val)
                budget_display = cumulative_budget_by_col.get(col, budget_val)
                diff_display = cumulative_diff_by_col.get(col, diff_val)
                contributors = cumulative_contributors_by_col.get(
                    col, contributors_by_col.get(col, [])
                )
            else:
                actual_display = actual_val
                budget_display = budget_val
                diff_display = diff_val
                contributors = contributors_by_col.get(col, [])
            summary[col] = build_summary_entry(
                label,
                actual_display,
                budget_display,
                diff_display,
                contributors,
            )

        if total_col_index not in summary and total_col_index >= 0:
            label = "TOTAL"
            col_totals = totals.get(total_col_index, {})
            actual_val = col_totals.get("actual", 0.0)
            budget_val = col_totals.get("budget", 0.0)
            diff_val = col_totals.get("diff", actual_val - budget_val)
            if self.summary_cumulative_mode:
                actual_display = cumulative_actual_by_col.get(total_col_index, actual_val)
                budget_display = cumulative_budget_by_col.get(total_col_index, budget_val)
                diff_display = cumulative_diff_by_col.get(total_col_index, diff_val)
                contributors = cumulative_contributors_by_col.get(
                    total_col_index,
                    list(col_totals.get("contributors", [])),
                )
            else:
                actual_display = actual_val
                budget_display = budget_val
                diff_display = diff_val
                contributors = col_totals.get("contributors", [])
            summary[total_col_index] = build_summary_entry(
                label,
                actual_display,
                budget_display,
                diff_display,
                contributors,
            )

        if toggle_column is not None and 0 <= toggle_column < len(header_names):
            mode_text = "cumulativa" if self.summary_cumulative_mode else "mensile"
            tooltip_text = (
                "Clicca per tornare ai totali mensili"
                if self.summary_cumulative_mode
                else "Clicca per mostrare i totali cumulativi"
            )
            mode_bg = QColor("#C4D5FF") if self.summary_cumulative_mode else QColor("#C8EEDC")
            toggle_font_size = max(6, SUMMARY_FONT_SIZE-2)
            highlight_font_size = max(6, SUMMARY_FONT_SIZE -1)
            summary[toggle_column] = {
                "background": QBrush(QColor("#E8EAED")),
                "alignment": Qt.AlignmentFlag.AlignCenter,
                "lines": [
                    {"text": "Modalità", "fg": QColor("#222"), "font_size": toggle_font_size},
                    {"text": mode_text, "bg": mode_bg, "fg": QColor("#111"), "font_size": highlight_font_size},
                    {"text": "Click per cambiare", "fg": QColor("#444"), "font_size": toggle_font_size},
                ],
                "tooltip": tooltip_text,
            }

        self.summary_header.configure_toggle(toggle_column, self.summary_cumulative_mode, self._on_summary_toggle_requested)
        self.summary_header.set_summary(summary)

    def _format_diff_tooltip(
        self,
        label: str,
        diff_value: float,
        contributors: list[tuple[str, float]] | None,
    ) -> str | None:
        if abs(diff_value) <= 1e-6:
            return None
        if not contributors:
            return None
        lines: list[str] = []
        sorted_contributors = sorted(contributors, key=lambda x: abs(x[1]), reverse=True)
        count = 0
        for name, value in sorted_contributors:
            if abs(value) <= 1e-6:
                continue
            clean_name = name.strip() or "(senza nome)"
            lines.append(f"{clean_name}: {format_diff_value(value)}")
            count += 1
            if count >= 12:
                break
        if not lines:
            return None
        tooltip = "Categorie con differenza:\n" + "\n".join(lines)
        remaining = sum(1 for _, value in sorted_contributors[count:] if abs(value) > 1e-6)
        if remaining > 0:
            tooltip += f"\n... (+{remaining} altre)"
        return tooltip

    def _on_category_detail_requested(self, index: QModelIndex):
        if not index.isValid():
            return
        meta = index.data(Qt.ItemDataRole.UserRole)
        if not meta or not isinstance(meta, tuple) or meta[0] != "category_label":
            return
        cid = meta[1]
        try:
            cid_key = int(cid)
        except Exception:
            cid_key = cid
        self._open_category_detail(cid_key)

    def _open_category_detail(self, cid):
        name = self.id2name.get(cid, f"(id:{cid})")
        main_name = ""
        cat_item = self.category_label_items.get(cid)
        if cat_item:
            meta = cat_item.data(Qt.ItemDataRole.UserRole)
            if meta and isinstance(meta, tuple):
                if len(meta) >= 5 and meta[4]:
                    main_name = str(meta[4])
                elif len(meta) >= 4:
                    try:
                        main_id = int(meta[3])
                    except Exception:
                        main_id = meta[3]
                    main_name = self.id2name.get(main_id, str(main_id))
        if not main_name:
            main_name = name
        dialog = CategoryDetailDialog(
            self,
            name,
            main_name,
            lambda cid=cid: self._category_detail_rows(cid),
            self._copy_budget_from_detail,
            self._update_budget_from_detail,
        )
        dialog.exec()

    def _copy_budget_from_detail(self, model_index: QModelIndex):
        if not model_index or not model_index.isValid():
            return
        self.apply_actual_to_budget(model_index)

    def _update_budget_from_detail(self, model_index: QModelIndex, text: str):
        if not model_index or not model_index.isValid():
            return
        self.model.setData(model_index, text, Qt.ItemDataRole.EditRole)

    def _category_detail_rows(self, cid) -> list[dict[str, Any]]:
        cat_item = self.category_label_items.get(cid)
        if not cat_item:
            return []
        header_names = self.current_headers or []
        if not header_names:
            return []
        actual_row_idx = budget_row_idx = diff_row_idx = None
        for row in range(cat_item.rowCount()):
            label_item = cat_item.child(row, 0)
            if not label_item:
                continue
            label_text = label_item.text()
            if label_text == "Actual":
                actual_row_idx = row
            elif label_text == "Budget":
                budget_row_idx = row
            elif label_text == "Diff":
                diff_row_idx = row
        if actual_row_idx is None or budget_row_idx is None or diff_row_idx is None:
            return []

        def parse_item(item) -> tuple[str, float, QColor | None, QBrush | None]:
            if not item:
                return "0", 0.0, None, None
            text = (item.text() or "").strip()
            display = text if text else "0"
            raw = display.replace(" ", "").replace(",", "")
            try:
                value = float(raw) if raw else 0.0
            except ValueError:
                value = 0.0
            fg_brush = item.foreground()
            fg_color = fg_brush.color() if fg_brush and fg_brush.color().isValid() else None
            bg_brush = item.background()
            return display, value, fg_color, bg_brush

        detail_rows: list[dict[str, Any]] = []
        added_separator = False
        column_count = min(self.model.columnCount(), len(header_names))
        for col in range(1, column_count):
            header_label = header_names[col]
            if header_label == "Period" or col == 1:
                continue
            actual_item = cat_item.child(actual_row_idx, col)
            budget_item = cat_item.child(budget_row_idx, col)
            diff_item = cat_item.child(diff_row_idx, col)
            is_total = header_label.upper() == "TOTAL"
            if is_total and not added_separator and detail_rows:
                detail_rows.append({"row_role": "separator"})
                added_separator = True
            actual_text, actual_value, actual_color, _ = parse_item(actual_item)
            budget_text, budget_value, budget_color, _ = parse_item(budget_item)
            diff_text, _, _, diff_bg = parse_item(diff_item)
            computed_diff = actual_value - budget_value
            if not diff_text or diff_text == "0":
                diff_text = format_diff_value(computed_diff)
            if not diff_bg:
                diff_bg = diff_background(computed_diff)
            meta = budget_item.data(Qt.ItemDataRole.UserRole) if budget_item else None
            if meta and isinstance(meta, tuple) and meta[0] == "budget":
                budget_index = budget_item.index()
            else:
                budget_index = QModelIndex()
            detail_rows.append(
                {
                    "label": "Totale" if header_label.upper() == "TOTAL" else header_label,
                    "actual_text": actual_text or "0",
                    "actual_color": actual_color,
                    "actual_value": actual_value,
                    "budget_text": budget_text or "0",
                    "budget_color": budget_color,
                    "diff_text": diff_text or "0",
                    "diff_background": diff_bg,
                    "budget_index": budget_index,
                    "row_role": "total" if is_total else "month",
                }
            )
        return detail_rows

    def apply_light_theme(self):
        self.setStyleSheet(
            """
            QWidget { background-color: white; color: black; font-size: 11px; }
            QPushButton { background-color: #f3f4f6; border: 1px solid #ccc; border-radius: 5px; padding: 4px 10px; font-size: 11px; }
            QPushButton:hover { background-color: #e2e6ea; }
            QHeaderView::section { background-color: #f2f2f2; color: #111; font-weight: bold; font-size: 11px; }
            QHeaderView::section:last { border-left: 1px solid #000; }
            QTreeView { alternate-background-color: #fafafa; gridline-color: #eee; font-size: 11px; }
            QScrollBar:vertical { background: #f4f5f8; width: 12px; margin: 2px 0 2px 0; border-radius: 6px; }
            QScrollBar::handle:vertical { background: #bfc6d4; min-height: 24px; border-radius: 6px; }
            QScrollBar::handle:vertical:hover { background: #a6aec0; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; background: none; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
            """
        )

    def refresh(self):
        self.edits.clear()
        self._set_unsaved_changes(False)
        year = self.year_cb.currentText()
        if not year:
            return
        self.view.clear_highlighted_columns()
        self.summary_header.set_highlighted_sections(set())

        entries = self.per_year_entries.get(year, [])
        header_names = ["Category / RowType"]
        header_ids = []
        if entries:
            year_id, year_name = entries[0]
            header_names.append(self._display_header_name(year_name))
            header_names.append("Period")
            header_ids.append(year_id)
            for bid, name in entries[1:]:
                header_names.append(self._display_header_name(name))
                header_ids.append(bid)
        header_names.append("TOTAL")
        self.current_headers = header_names[:]

        self.model.clear()
        self.summary_header.set_summary({})
        self.summary_header.configure_toggle(None, self.summary_cumulative_mode)
        self.category_label_items = {}
        self.model.setHorizontalHeaderLabels(header_names)
        for col in range(self.model.columnCount()):
            self.view.setItemDelegateForColumn(col, self.default_delegate)
        self.view.setItemDelegateForColumn(0, self.category_detail_delegate)
        self._apply_column_widths(header_names)
        for col in range(1, len(header_names)):
            if header_names[col] == "Period":
                continue
            self.model.setHeaderData(
                col,
                Qt.Orientation.Horizontal,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                Qt.ItemDataRole.TextAlignmentRole,
            )
        period_col = header_names.index("Period") if "Period" in header_names else None
        if period_col is not None and period_col < self.model.columnCount():
            self.view.setItemDelegateForColumn(period_col, self.period_delegate)
        budget_columns = []
        if entries:
            for idx in range(3, len(header_names) - 1):
                budget_columns.append(idx)
        for col in budget_columns:
            if 0 <= col < self.model.columnCount():
                self.view.setItemDelegateForColumn(col, self.budget_button_delegate)
        total_col = len(header_names) - 1
        if 0 <= total_col < self.model.columnCount():
            self.view.setItemDelegateForColumn(total_col, self.total_divider_delegate)

        df_actual = fetch_actuals_for_year(year)
        df_bud = load_budgets_for_year(year, self.name_to_id, self.per_year_entries)
        colname_to_bid = {name: bid for bid, name in entries}

        actual_map = {
            (int(r["categid"]), colname_to_bid.get(r["month"])): float(r["amount"])
            for _, r in df_actual.iterrows()
            if colname_to_bid.get(r["month"])
        }
        budget_map = {
            (int(r["CATEGID"]), int(r["BUDGETYEARID"])): (float(r["AMOUNT"] or 0), r["PERIOD"] or "Monthly")
            for _, r in df_bud.iterrows()
        }
        # Cache for incremental updates during edits
        self.header_ids = header_ids
        self.actual_map = actual_map
        self.base_budget_map = budget_map
        self.category_totals = {}

        QTimer.singleShot(0, lambda hn=list(header_names): self._apply_column_widths(hn))

        def add_category(cid, depth=0, root_cid=None, root_name=None):
            current_name = self.id2name.get(cid, f"(id:{cid})")
            if root_cid is None:
                root_cid = cid
            if root_name is None:
                root_name = current_name
            cname = ("    " * depth) + current_name
            meta = ("category_label", cid, depth, root_cid, root_name)
            cat_item = make_item(cname, False, bold=True, color=QColor("#000"), meta=meta)

            try:
                cid_key = int(cid)
            except Exception:
                cid_key = cid
            self.category_label_items[cid_key] = cat_item
            if depth == 0:
                row_items = [cat_item]
                cat_item.setBackground(QBrush(MAIN_CATEGORY_BG))
                for _ in range(1, len(header_names)):
                    filler = make_item("", False)
                    filler.setBackground(QBrush(MAIN_CATEGORY_BG))
                    filler.setSelectable(False)
                    row_items.append(filler)
                self.model.appendRow(row_items)
                for ch in sorted(self.children_map.get(cid, []), key=lambda x: self.id2name.get(x, "")):
                    add_category(ch, depth + 1, root_cid, root_name)
                return

            self.model.appendRow([cat_item])

            # Actual row
            act_row = [make_item("Actual", False)]
            total_act = 0.0
            year_bid = header_ids[0]
            val_year = actual_map.get((cid, year_bid), 0.0)
            total_act += val_year
            col = QColor("#1b5e20") if val_year > 0 else QColor("#b71c1c") if val_year < 0 else QColor("#000")
            act_row.append(make_item(f"{val_year:,.2f}", False, ("actual", cid, year_bid), color=col))
            act_row.append(make_item("", False))
            for bid in header_ids[1:]:
                val = actual_map.get((cid, bid), 0.0)
                total_act += val
                col = QColor("#1b5e20") if val > 0 else QColor("#b71c1c") if val < 0 else QColor("#000")
                act_row.append(make_item(f"{val:,.2f}", False, ("actual", cid, bid), color=col))
            act_row.append(make_item(f"{total_act:,.2f}", False))
            cat_item.appendRow(act_row)

            # Budget row
            bud_row = [make_item("Budget", False)]
            first_bid = header_ids[0]
            if (cid, first_bid) in budget_map:
                year_amt, year_per = budget_map[(cid, first_bid)]
            else:
                year_amt = None
                year_per = ""
            year_text = "" if year_amt is None else f"{float(year_amt):,.2f}"
            bud_row.append(
                make_item(
                    year_text,
                    True,
                    ("budget", cid, first_bid),
                    color=QColor("#01579b"),
                )
            )
            bud_row.append(make_item(year_per or "", True, ("budget_period", cid, None)))

            # Build monthly budgets according to annual rules
            month_bids = list(header_ids[1:])
            overrides = {}
            for bid in month_bids:
                amt, per = budget_map.get((cid, bid), (None, None))
                if amt is not None:
                    overrides[bid] = amt

            monthly_value_for_diff, display_total, over_limit, explicit_bids = compute_budget_distribution(
                year_amt, year_per, month_bids, overrides
            )

            for bid in month_bids:
                val = monthly_value_for_diff.get(bid, 0.0)
                is_explicit = bid in explicit_bids
                color = QColor("#01579b") if is_explicit else CALCULATED_BUDGET_COLOR
                text = format_diff_value(val) if (val or is_explicit) else ""
                bud_row.append(
                    make_item(
                        text,
                        True,
                        ("budget", cid, bid),
                        bold=False,
                        color=color,
                    )
                )

            tot_item = make_item(f"{display_total:,.2f}", False)
            bud_row.append(tot_item)
            # Highlight in red if explicit monthly budgets exceed annual budget in absolute value
            if over_limit:
                tot_item.setBackground(QBrush(QColor("#F8D6D6")))
            cat_item.appendRow(bud_row)

            # Diff row
            diff_row = [make_item("Diff", False)]
            diff_font = QFont(UI_FONT_FAMILY, DIFF_FONT_SIZE)
           # diff_font.setItalic(True)
            diff_row.append(make_item("", False))
            diff_row.append(make_item("", False))
            for bid in header_ids[1:]:
                a = actual_map.get((cid, bid), 0.0)
                b = monthly_value_for_diff.get(bid)
                if b is None:
                    b = budget_map.get((cid, bid), (0.0,))[0] or 0.0
                d = a - b
                cell = make_item(format_diff_value(d), False)
                cell.setFont(diff_font)
                cell.setBackground(diff_background(d))
                diff_row.append(cell)
            total_diff_adjusted = total_act - display_total
            tot_cell = make_item(format_diff_value(total_diff_adjusted), False)
            tot_cell.setFont(diff_font)
            tot_cell.setBackground(diff_background(total_diff_adjusted))
            diff_row.append(tot_cell)
            cat_item.appendRow(diff_row)

            for ch in sorted(self.children_map.get(cid, []), key=lambda x: self.id2name.get(x, "")):
                add_category(ch, depth + 1, root_cid, root_name)

            if not self.children_map.get(cid):
                self.category_totals[cid] = {
                    "actual": total_act,
                    "budget": display_total,
                }

        for r in self.root_ids:
            add_category(r)

        self._update_summary_header(header_names)
        self._apply_column_widths(header_names)
        self.view.expandAll()
        self._apply_main_collapse_states()
        try:
            self.model.itemChanged.disconnect()
        except Exception:
            pass
        self.model.itemChanged.connect(self.on_item_changed)
        self.update_summary_chart()
        self._highlight_current_month_column()

    def update_summary_chart(self):
        totals = {
            "actual_income": 0.0,
            "actual_expense": 0.0,
            "budget_income": 0.0,
            "budget_expense": 0.0,
        }

        for data in self.category_totals.values():
            actual = data.get("actual", 0.0)
            budget = data.get("budget", 0.0)
            if actual >= 0:
                totals["actual_income"] += actual
            else:
                totals["actual_expense"] += actual
            if budget >= 0:
                totals["budget_income"] += budget
            else:
                totals["budget_expense"] += budget

        actual_income = totals["actual_income"]
        actual_expense = totals["actual_expense"]  # negative
        budget_income = totals["budget_income"]
        budget_expense = totals["budget_expense"]  # negative

        actual_bars = [
            ("Entrate", actual_income, "#2c7a7b"),
            ("Uscite", abs(actual_expense), "#dd3b50"),
            ("Differenza", actual_income + actual_expense, "#3358c4"),
        ]
        budget_bars = [
            ("Entrate", budget_income, "#5bc8b2"),
            ("Uscite", abs(budget_expense), "#ff8a80"),
            ("Differenza", budget_income + budget_expense, "#7a7cff"),
        ]

        magnitude_values = [abs(v) for _, v, _ in actual_bars + budget_bars]
        max_limit = max(magnitude_values or [1.0]) or 1.0
        offset = max_limit * 0.035

        self.figure.clear()
        self.figure.set_facecolor("#f6f7fb")
        grid = self.figure.add_gridspec(1, 3, width_ratios=[1, 0.12, 1])
        ax_actual = self.figure.add_subplot(grid[0, 0])
        ax_budget = self.figure.add_subplot(grid[0, 2])

        def render_panel(ax, title, items):
            labels = [lbl for lbl, _, _ in items]
            values = [val for _, val, _ in items]
            colors = [clr for _, _, clr in items]
            y_pos = range(len(items))

            ax.set_facecolor("#ffffff")
            bars = ax.barh(
                y_pos,
                values,
                height=0.90,
                color=colors,
                edgecolor="#0e0f0f",
                linewidth=0.6,
            )
            ax.set_yticks(list(y_pos))
            ax.set_yticklabels(labels, fontsize=9, color="#1f2933")
            ax.invert_yaxis()
            ax.tick_params(axis="y", length=0)
            ax.tick_params(axis="x", colors="#6b7280", labelsize=8, pad=2)
            ax.set_title(title, fontsize=10, fontweight="bold", color="#1f2937", pad=6)

            ax.grid(axis="x", color="#e5e7eb", linestyle="--", linewidth=0.8, alpha=0.7)
            ax.set_axisbelow(True)
            ax.axvline(0, color="#94a3b8", linewidth=1.0, alpha=0.8)

            limit = max_limit * 1.18
            ax.set_xlim(-limit, limit)
            ax.set_xlabel("")

            for spine in ax.spines.values():
                spine.set_visible(False)

            for idx, (bar, (_, value, _)) in enumerate(zip(bars, items)):
                width = bar.get_width()
                if abs(width) < 1e-8:
                    continue
                text = f"{value:,.2f}"

                # Estimate text width in pixels for 9pt font
                fontsize_pt = 9.0
                dpi = ax.figure.dpi
                px_per_pt = dpi / 72.0
                avg_char_px = 0.6 * fontsize_pt * px_per_pt
                text_px = max(len(text) * avg_char_px, 10)

                # Compute bar width in pixels
                x0_px = ax.transData.transform((0, 0))[0]
                xw_px = ax.transData.transform((width, 0))[0]
                bar_px = abs(xw_px - x0_px)

                # Decide inside/outside based on pixel widths
                inside_pad_px = 6
                outside_pad_px = max(8, text_px * 0.25)
                show_inside = bar_px > (text_px + inside_pad_px + 2)

                # Helper to convert px to data units using local scale
                inv = ax.transData.inverted()
                def px_to_data(px):
                    return inv.transform((x0_px + px, 0))[0] - inv.transform((x0_px, 0))[0]

                left_lim, right_lim = ax.get_xlim()
                text_data_w = px_to_data(text_px)
                pad_in_data = px_to_data(inside_pad_px)
                pad_out_data = px_to_data(outside_pad_px)

                if width >= 0:
                    if show_inside:
                        x_pos = width - pad_in_data
                        ha = "right"
                        color = "#f8fafc"
                    else:
                        x_pos = width + pad_out_data
                        x_pos = min(x_pos, right_lim - text_data_w - px_to_data(2))
                        ha = "left"
                        color = "#1f2937"
                else:
                    if show_inside:
                        x_pos = width + pad_in_data
                        ha = "left"
                        color = "#f8fafc"
                    else:
                        x_pos = width - pad_out_data
                        x_pos = max(x_pos, left_lim + text_data_w + px_to_data(2))
                        ha = "right"
                        color = "#991b1b"

                ax.text(
                    x_pos,
                    bar.get_y() + bar.get_height() / 2,
                    text,
                    va="center",
                    ha=ha,
                    fontsize=9,
                    fontweight="bold",
                    color=color,
                )
            ax.margins(y=0.28)
            ax.text(
                ax.get_xlim()[1],
                -0.7,
                "(EUR)",
                fontsize=7,
                color="#4b5563",
                ha="right",
                va="top",
            )

        render_panel(ax_actual, "Actual", actual_bars)
        render_panel(ax_budget, "Budget", budget_bars)

        self.figure.subplots_adjust(left=0.12, right=0.88, top=0.88, bottom=0.24, wspace=0.0)
        self.canvas.draw_idle()

    def recalc_category(self, cid: int):
        # guard to avoid treating auto-calculated cells as user edits
        if self._recalc_guard:
            return
        self._recalc_guard = True
        try:
            # Find category row
            root = self.model.invisibleRootItem()
            target = None
            depth = 0
            for r in range(root.rowCount()):
                cat_item = root.child(r, 0)
                if not cat_item:
                    continue
                meta = cat_item.data(Qt.ItemDataRole.UserRole)
                if meta and meta[0] == "category_label" and meta[1] == cid:
                    target = cat_item
                    depth = meta[2] or 0
                    break
            if target is None:
                return

            header_ids = getattr(self, "header_ids", [])
            actual_map = getattr(self, "actual_map", {})
            base_budget_map = getattr(self, "base_budget_map", {})
            if not header_ids:
                return

            year_bid = header_ids[0]
            # Discover row indices
            budget_row_idx = diff_row_idx = actual_row_idx = None
            for rr in range(target.rowCount()):
                first = target.child(rr, 0)
                label = first.text() if first else ""
                if label == "Budget":
                    budget_row_idx = rr
                elif label == "Diff":
                    diff_row_idx = rr
                elif label == "Actual":
                    actual_row_idx = rr
            if budget_row_idx is None or diff_row_idx is None:
                return

            # Read UI period cell (source of truth after edits)
            period_cell = target.child(budget_row_idx, 2)
            year_per = (period_cell.text() or "").strip()

            year_edit = self.edits.get((year_bid, cid)) or {}
            base_year_entry = base_budget_map.get((cid, year_bid), (None, None))
            if not year_per:
                edit_period = year_edit.get("period")
                if edit_period:
                    year_per = edit_period
                else:
                    base_period = base_year_entry[1]
                    if base_period:
                        year_per = base_period

            # Effective annual amount (edits override base)
            if "amount" in year_edit:
                year_amt = year_edit.get("amount")
            else:
                year_amt = base_year_entry[0]

            # Explicit months map (edits override base)
            month_bids = list(header_ids[1:])
            overrides = {}
            for bid in month_bids:
                edit_entry = self.edits.get((bid, cid))
                if edit_entry and "amount" in edit_entry:
                    if edit_entry["amount"] is None:
                        continue
                    overrides[bid] = float(edit_entry["amount"])
                    continue
                base_amt, base_period = base_budget_map.get((cid, bid), (None, None))
                if base_amt is not None:
                    overrides[bid] = float(base_amt)

            # Re-render Budget row cells
            monthly_value_for_diff, display_total, over_limit, explicit_bids = compute_budget_distribution(
                year_amt, year_per, month_bids, overrides
            )

            for idx, bid in enumerate(month_bids, start=3):
                val = monthly_value_for_diff.get(bid, 0.0)
                is_explicit = bid in explicit_bids
                color = QColor("#01579b") if is_explicit else CALCULATED_BUDGET_COLOR
                item = target.child(budget_row_idx, idx)
                if item:
                    item.setText(format_diff_value(val) if (val or is_explicit) else "")
                    item.setForeground(QBrush(color))
                    font = item.font()
                    if is_explicit:
                        font.setBold(False)
                        font.setPointSize(UI_BASE_FONT_SIZE)
                    else:
                        font.setBold(True)
                        font.setPointSize(UI_BOLD_FONT_SIZE)
                    item.setFont(font)

            # Update total cell and color
            tot_col = 3 + len(month_bids)
            tot_item = target.child(budget_row_idx, tot_col)
            if tot_item:
                tot_item.setText(f"{display_total:,.2f}")
                if over_limit:
                    tot_item.setBackground(QBrush(QColor("#F8D6D6")))
                else:
                    # reset to default based on depth shading
                    if depth == 0:
                        tot_item.setBackground(QBrush(MAIN_CATEGORY_BG))
                    else:
                        tot_item.setBackground(QBrush())

            # Recompute Diff row
            year_cell = target.child(diff_row_idx, 1)
            if year_cell:
                year_cell.setText("")
                year_cell.setBackground(QBrush())
            period_cell = target.child(diff_row_idx, 2)
            if period_cell:
                period_cell.setText("")
                period_cell.setBackground(QBrush())
            # monthly diffs
            for idx, bid in enumerate(month_bids, start=3):
                a = actual_map.get((cid, bid), 0.0)
                b = monthly_value_for_diff.get(bid)
                if b is None:
                    b = base_budget_map.get((cid, bid), (0.0,))[0] or 0.0
                d = a - b
                cell = target.child(diff_row_idx, idx)
                if cell:
                    cell.setText(format_diff_value(d))
                    f = QFont(UI_FONT_FAMILY, DIFF_FONT_SIZE)
                  #  f.setItalic(True)
                    cell.setFont(f)
                    cell.setBackground(diff_background(d))
            tot_diff_cell = target.child(diff_row_idx, tot_col)
            if tot_diff_cell:
                total_act = actual_map.get((cid, year_bid), 0.0)
                for bid in month_bids:
                    total_act += actual_map.get((cid, bid), 0.0)
                total_diff_adjusted = total_act - display_total
                tot_diff_cell.setText(format_diff_value(total_diff_adjusted))
                f = QFont(UI_FONT_FAMILY, DIFF_FONT_SIZE)
               # f.setItalic(True)
                tot_diff_cell.setFont(f)
                tot_diff_cell.setBackground(diff_background(total_diff_adjusted))
            if depth == 0 and diff_row_idx is not None:
                for idx in range(target.columnCount()):
                    cell = target.child(diff_row_idx, idx)
                    if cell:
                        cell.setBackground(QBrush(MAIN_CATEGORY_BG))
            if not self.children_map.get(cid):
                total_act = actual_map.get((cid, year_bid), 0.0)
                for bid in month_bids:
                    total_act += actual_map.get((cid, bid), 0.0)
                self.category_totals[cid] = {"actual": total_act, "budget": display_total}
                self.update_summary_chart()
            self._update_summary_header()
        finally:
            self._recalc_guard = False

    def apply_actual_to_budget(self, index):
        meta = index.data(Qt.ItemDataRole.UserRole)
        if not meta or not isinstance(meta, tuple) or meta[0] != "budget":
            return
        _, cid, bid = meta
        cid = int(cid)
        bid = int(bid)

        actual_value = None
        parent_index = index.parent()
        category_item = self.model.itemFromIndex(parent_index)
        if category_item:
            for row in range(category_item.rowCount()):
                label_item = category_item.child(row, 0)
                if label_item and label_item.text() == "Actual":
                    actual_cell = category_item.child(row, index.column())
                    if actual_cell:
                        text_val = (actual_cell.text() or "").replace(" ", "").replace(",", "")
                        try:
                            actual_value = float(text_val)
                        except ValueError:
                            actual_value = 0.0
                    break
        if actual_value is None:
            actual_value = self.actual_map.get((cid, bid))
        if actual_value is None:
            if bid in self.header_ids[1:]:
                actual_value = 0.0
            else:
                actual_value = 0.0
                for month_bid in self.header_ids[1:]:
                    actual_value += self.actual_map.get((cid, month_bid), 0.0)

        try:
            self._recalc_guard = True
            self.model.setData(index, f"{actual_value:,.2f}", Qt.ItemDataRole.EditRole)
        finally:
            self._recalc_guard = False
        item = self.model.itemFromIndex(index)
        if item:
            self.on_item_changed(item)

    def on_item_changed(self, item):
        # ignore changes that come from programmatic recalculation
        if getattr(self, "_recalc_guard", False):
            return
        meta = item.data(Qt.ItemDataRole.UserRole)
        if not meta:
            return
        kind = meta[0]

        if kind == "budget":
            _, cid, bid = meta
            text_value = str(item.text() or "")
            cleaned = text_value.replace(",", "").strip()
            if cleaned in ("", "-"):
                val = None
            else:
                try:
                    val = float(cleaned)
                except Exception:
                    return
            item.setBackground(QColor("#FFF3B0"))
            year = self.year_cb.currentText()
            annual_bid = None
            entries = self.per_year_entries.get(year, [])
            if entries:
                annual_bid = entries[0][0]
            if annual_bid is not None and int(bid) == int(annual_bid):
                period = self.edits.get((bid, cid), {}).get("period")
                if not period:
                    root = self.model.invisibleRootItem()
                    for r in range(root.rowCount()):
                        cat_item = root.child(r, 0)
                        if not cat_item:
                            continue
                        cat_meta = cat_item.data(Qt.ItemDataRole.UserRole)
                        if not cat_meta or cat_meta[1] != cid:
                            continue
                        for rr in range(cat_item.rowCount()):
                            first = cat_item.child(rr, 0)
                            if first and first.text() == "Budget":
                                period_cell = cat_item.child(rr, 2)
                                period = (period_cell.text() or "Monthly").strip()
                                break
                        break
                period = period or "Monthly"
            else:
                period = "Monthly"
            self.edits[(int(bid), int(cid))] = {"amount": val, "period": period}
            self._set_unsaved_changes(True)
            # Recalculate totals and colors for this category
            self.recalc_category(int(cid))

        elif kind == "budget_period":
            _, cid, _ = meta
            val = item.text().strip() or "Monthly"
            year = self.year_cb.currentText()
            bid = self.per_year_entries[year][0][0]

            amount_val = 0.0
            root = self.model.invisibleRootItem()
            for r in range(root.rowCount()):
                cat_item = root.child(r, 0)
                if not cat_item:
                    continue
                cat_meta = cat_item.data(Qt.ItemDataRole.UserRole)
                if not cat_meta or cat_meta[1] != cid:
                    continue
                for rr in range(cat_item.rowCount()):
                    first = cat_item.child(rr, 0)
                    if first and first.text() == "Budget":
                        year_cell = cat_item.child(rr, 1)
                        try:
                            amount_val = float(str(year_cell.text()).replace(",", "").strip() or 0.0)
                        except Exception:
                            amount_val = 0.0
                        break
                break

            item.setBackground(QColor("#FFF3B0"))
            self.edits[(int(bid), int(cid))] = {"period": val, "amount": amount_val}
            self._set_unsaved_changes(True)
            self.recalc_category(int(cid))

    def save_budgets(self):
        if not self.edits:
            QMessageBox.information(self, "No changes", "No budget changes to save.")
            return
        if (
            QMessageBox.question(
                self,
                "Confirm",
                f"Save {len(self.edits)} changes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            for (bid, cid), data in self.edits.items():
                amount = data.get("amount")
                period = data.get("period", "Monthly")
                if amount is None:
                    delete_budget_entry(bid, cid)
                else:
                    upsert_budget_entry(bid, cid, period, amount)
            QMessageBox.information(self, "Saved", "Budgets saved successfully.")
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_header_section_double_clicked(self, logical_index: int):
        if not self.current_headers:
            return
        if logical_index < 0 or logical_index >= len(self.current_headers):
            return
        if logical_index < 3 or logical_index >= len(self.current_headers) - 1:
            return
        header_name = self.current_headers[logical_index]
        if header_name in ("Period", "TOTAL"):
            return
        self.view.toggle_highlight_column(logical_index)
        highlights = self.view.highlighted_columns()
        self.summary_header.set_highlighted_sections(highlights)

    def on_view_double_clicked(self, index):
        try:
            first = index.siblingAtColumn(0)
        except Exception:
            first = index
        item = self.model.itemFromIndex(first)
        if not item:
            return
        meta = item.data(Qt.ItemDataRole.UserRole)
        if not meta or meta[0] != "category_label":
            return
        depth = meta[2] or 0
        if depth != 0:
            return
        try:
            cid = int(meta[1])
        except Exception:
            cid = meta[1]
        if cid in self._collapsed_main:
            self._collapsed_main.remove(cid)
        else:
            self._collapsed_main.add(cid)
        self._apply_main_collapse_states()

    def _apply_main_collapse_states(self):
        root = self.model.invisibleRootItem()
        for r in range(root.rowCount()):
            cat_item = root.child(r, 0)
            if not cat_item:
                continue
            meta = cat_item.data(Qt.ItemDataRole.UserRole)
            if not meta or meta[0] != "category_label":
                continue
            depth = meta[2] or 0
            if depth != 0:
                continue
            try:
                cid = int(meta[1])
            except Exception:
                cid = meta[1]
            collapse = cid in self._collapsed_main
            self.view.setExpanded(cat_item.index(), not collapse)
            self._hide_until_next_main(r, collapse)

    def _hide_until_next_main(self, start_row: int, hide: bool):
        root = self.model.invisibleRootItem()
        for rr in range(start_row + 1, root.rowCount()):
            next_item = root.child(rr, 0)
            if not next_item:
                continue
            meta = next_item.data(Qt.ItemDataRole.UserRole)
            if meta and meta[0] == "category_label" and (meta[2] or 0) == 0:
                break
            self.view.setRowHidden(rr, QModelIndex(), hide)

    def expand_all_main(self):
        self._collapsed_main.clear()
        self._apply_main_collapse_states()

    def collapse_all_main(self):
        root = self.model.invisibleRootItem()
        ids = set()
        for r in range(root.rowCount()):
            item = root.child(r, 0)
            if not item:
                continue
            meta = item.data(Qt.ItemDataRole.UserRole)
            if meta and meta[0] == "category_label" and (meta[2] or 0) == 0:
                try:
                    cid = int(meta[1])
                except Exception:
                    cid = meta[1]
                ids.add(cid)
        self._collapsed_main = ids
        self._apply_main_collapse_states()

    def closeEvent(self, event):
        if self.edits:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setWindowTitle("Modifiche non salvate")
            dialog.setText("Ci sono modifiche non salvate.")
            dialog.setInformativeText("Vuoi salvarle prima di uscire?")
            save_button = dialog.addButton("Salva", QMessageBox.ButtonRole.AcceptRole)
            discard_button = dialog.addButton("Esci senza salvare", QMessageBox.ButtonRole.DestructiveRole)
            cancel_button = dialog.addButton("Annulla", QMessageBox.ButtonRole.RejectRole)
            dialog.setDefaultButton(save_button)
            dialog.exec()
            clicked = dialog.clickedButton()
            if clicked == save_button:
                self.save_budgets()
                if self.edits:
                    event.ignore()
                    return
            elif clicked == cancel_button:
                event.ignore()
                return
            elif clicked != discard_button:
                event.ignore()
                return
        super().closeEvent(event)

    def select_db(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Select DB", str(Path.home()), "SQLite (*.mmb *.db)"
        )
        if file:
            config.DB_PATH = Path(file)
            config.save_last_db(config.DB_PATH)
            self._set_db_path_label(config.DB_PATH)
            self.years, self.per_year_entries, self.name_to_id = load_budgetyear_map()
            self.id2name, self.children_map, self.root_ids = load_categories()
            saved_year = config.load_last_budget_year()
            self.year_cb.blockSignals(True)
            self.year_cb.clear()
            self.year_cb.addItems(self.years or [])
            selected = ""
            if saved_year and saved_year in (self.years or []):
                self.year_cb.setCurrentText(saved_year)
                selected = saved_year
            elif self.years:
                self.year_cb.setCurrentIndex(0)
                selected = self.year_cb.currentText()
            self.year_cb.blockSignals(False)
            if selected:
                self._on_year_changed(selected)
            else:
                self.refresh()


def main():
    app = QApplication(sys.argv)
    icon_path = get_resource_path("money.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    w = BudgetApp()
    w.show()
    sys.exit(app.exec())

