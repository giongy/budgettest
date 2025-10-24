import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTreeView, QHeaderView, QMessageBox, QFileDialog,
    QSizePolicy, QAbstractItemView, QToolButton, QStyle, QFrame,
)
from PyQt6.QtGui import QStandardItemModel, QColor, QFont, QBrush, QIcon
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
)
from .ui import make_item, PeriodDelegate, ButtonDelegate, DividerDelegate, SummaryHeaderView
from .style import (
    CATEGORY_COLUMN_WIDTH,
    PERIOD_COLUMN_WIDTH,
    NUMERIC_COLUMN_WIDTH,
    MIN_COLUMN_WIDTH,
    MAIN_CATEGORY_BG,
    DIFF_POSITIVE_COLOR,
    DIFF_NEGATIVE_COLOR,
    UI_FONT_FAMILY,
    DIFF_FONT_SIZE,
    WINDOW_SCALE_RATIO,
    CHART_HEIGHT,
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
        self.save_btn.setStyleSheet(
            "QPushButton { background-color: #ffeb3b; border: 1px solid #bfa400; color: #000; font-weight: bold; } "
            "QPushButton:hover { background-color: #ffe066; }"
        )
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

        self.view = QTreeView()
        self.summary_header = SummaryHeaderView(self.view)
        self.view.setHeader(self.summary_header)
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
        self._collapsed_main: set[int] = set()
        self.view.doubleClicked.connect(self.on_view_double_clicked)
        self.current_headers: list[str] = []
        self.apply_light_theme()
        self._db_label_fulltext = ""
        self._set_db_path_label(config.DB_PATH)
        QTimer.singleShot(0, self._update_db_label_text)
        self.refresh()

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

    def _on_year_changed(self, year: str):
        if year:
            config.save_last_budget_year(year)
        self.refresh()

    def _compute_summary_budget_totals(self, header_names: list[str]) -> dict[int, float]:
        totals: dict[int, float] = {}
        root = self.model.invisibleRootItem()
        column_count = self.model.columnCount()
        for r in range(root.rowCount()):
            category_item = root.child(r, 0)
            if not category_item:
                continue
            budget_row_idx = None
            for rr in range(category_item.rowCount()):
                label_item = category_item.child(rr, 0)
                if label_item and label_item.text() == "Budget":
                    budget_row_idx = rr
                    break
            if budget_row_idx is None:
                continue
            for col in range(1, column_count):
                if col >= len(header_names):
                    continue
                if header_names[col] == "Period":
                    continue
                cell = category_item.child(budget_row_idx, col)
                if not cell:
                    continue
                txt = (cell.text() or "").replace(" ", "").replace(",", "")
                try:
                    val = float(txt) if txt else 0.0
                except ValueError:
                    val = 0.0
                totals[col] = totals.get(col, 0.0) + val
        return totals

    def _update_summary_header(self, header_names: list[str] | None = None):
        if header_names is None:
            header_names = self.current_headers
        if not header_names:
            self.summary_header.set_summary({})
            return
        totals = self._compute_summary_budget_totals(header_names)
        summary: dict[int, tuple[str, QBrush, Qt.AlignmentFlag]] = {}
        summary[0] = (
            "Totale Budget",
            QBrush(QColor("#E8EAED")),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        for col in range(1, len(header_names)):
            if header_names[col] == "Period":
                continue
            val = totals.get(col, 0.0)
            summary[col] = (
                format_diff_value(val),
                diff_background(val),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
        # Ensure TOTAL column gets included if it lies beyond header_names length due to model column
        total_col_index = self.model.columnCount() - 1
        if total_col_index not in summary:
            val = totals.get(total_col_index, 0.0)
            summary[total_col_index] = (
                format_diff_value(val),
                diff_background(val),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
        self.summary_header.set_summary(summary)

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
        year = self.year_cb.currentText()
        if not year:
            return

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
        self.model.setHorizontalHeaderLabels(header_names)
        for col in range(self.model.columnCount()):
            self.view.setItemDelegateForColumn(col, self.default_delegate)
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

        def add_category(cid, depth=0):
            cname = ("    " * depth) + self.id2name.get(cid, f"(id:{cid})")
            cat_item = make_item(
                cname, False, bold=True, color=QColor("#000"), meta=("category_label", cid, depth)
            )

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
                    add_category(ch, depth + 1)
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
                color = QColor("#01579b") if is_explicit else QColor("#6b7280") if val else QColor("#4b5563")
                text = f"{val:,.2f}" if val else ""
                bud_row.append(
                    make_item(
                        text,
                        True,
                        ("budget", cid, bid),
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
            diff_font.setItalic(True)
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
                add_category(ch, depth + 1)

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
                height=0.55,
                color=colors,
                edgecolor="#d2d8e5",
                linewidth=0.6,
            )
            ax.set_yticks(list(y_pos))
            ax.set_yticklabels(labels, fontsize=9, color="#1f2933")
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
                magnitude = abs(value)
                text = f"{value:,.2f}"
                digits = len(f"{int(magnitude)}")
                estimated_text_w = max(len(text) * 0.014, 0.08)
                inner_threshold = limit * 0.12
                min_inside = max(inner_threshold, estimated_text_w)
                show_inside = abs(width) > min_inside

                # Adjust offset if the space outside is limited
                outside_pad = max(offset, estimated_text_w * 0.4)

                if width >= 0:
                    if show_inside:
                        x_pos = width - offset
                        ha = "right"
                        text_color = "#f8fafc"
                    else:
                        x_pos = width + outside_pad
                        ha = "left"
                        text_color = "#1f2937"
                else:
                    if show_inside:
                        x_pos = width + offset
                        ha = "left"
                        text_color = "#f8fafc"
                    else:
                        x_pos = width - outside_pad
                        ha = "right"
                        text_color = "#991b1b"
                ax.text(
                    x_pos,
                    bar.get_y() + bar.get_height() / 2,
                    text,
                    va="center",
                    ha=ha,
                    fontsize=9,
                    fontweight="bold",
                    color=text_color,
                )
            ax.margins(y=0.28)
            ax.text(
                ax.get_xlim()[1],
                -0.7,
                "Valore (EUR)",
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

            # Effective annual amount (edits override base)
            year_amt = self.edits.get((year_bid, cid), {}).get("amount")
            if year_amt is None:
                year_amt = base_budget_map.get((cid, year_bid), (0.0,))[0]

            # Explicit months map (edits override base)
            month_bids = list(header_ids[1:])
            overrides = {}
            for bid in month_bids:
                edit_entry = self.edits.get((bid, cid))
                if edit_entry and "amount" in edit_entry:
                    overrides[bid] = float(edit_entry["amount"])
                else:
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
                color = QColor("#01579b") if is_explicit else QColor("#6b7280") if val else QColor("#4b5563")
                item = target.child(budget_row_idx, idx)
                if item:
                    item.setText(f"{val:,.2f}" if val else "")
                    item.setForeground(QBrush(color))

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
                    f.setItalic(True)
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
                f.setItalic(True)
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
            try:
                val = float(str(item.text()).replace(",", "").strip() or 0.0)
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
                upsert_budget_entry(
                    bid, cid, data.get("period", "Monthly"), data.get("amount", 0.0)
                )
            QMessageBox.information(self, "Saved", "Budgets saved successfully.")
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

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
