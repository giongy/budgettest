import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTreeView, QHeaderView, QMessageBox, QFileDialog,
    QSizePolicy, QAbstractItemView,
)
from PyQt6.QtGui import QStandardItemModel, QColor, QFont, QBrush
from PyQt6.QtCore import Qt, QTimer

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
from .ui import make_item, PeriodDelegate, ButtonDelegate

CATEGORY_COLUMN_WIDTH = 250  # width for category/label column
PERIOD_COLUMN_WIDTH = 60     # width for the period column
NUMERIC_COLUMN_WIDTH = 80    # width for budget/actual numeric columns (adjust to taste)
MIN_COLUMN_WIDTH = 10        # hard floor so small widths like 20 stay effective


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

    over_limit = False
    if annual_total:
        over_limit = abs(sum_overrides) > abs(annual_total)

    values = {}
    if over_limit:
        total_display = sum_overrides
        for bid in month_bids:
            values[bid] = overrides.get(bid, 0.0)
    else:
        total_display = annual_total
        if not missing_bids:
            for bid in month_bids:
                values[bid] = overrides.get(bid, 0.0)
            diff = annual_total - sum_overrides
            should_distribute_across_present = expected_count and len(month_bids) < expected_count
            if not should_distribute_across_present or not overrides or abs(diff) <= 1e-6:
                if not should_distribute_across_present:
                    total_display = sum_overrides
                return values, total_display, over_limit, set(overrides.keys())
            override_bids = [bid for bid in month_bids if bid in overrides]
            if override_bids:
                share = diff / len(override_bids)
                accumulated = 0.0
                for bid in override_bids[:-1]:
                    values[bid] += share
                    accumulated += share
                values[override_bids[-1]] += diff - accumulated
            return values, total_display, over_limit, set(overrides.keys())

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
        self.setWindowTitle("Budget Manager - Tema Chiaro")
        self.resize(1300, 800)
        self.years, self.per_year_entries, self.name_to_id = load_budgetyear_map()
        self.id2name, self.children_map, self.root_ids = load_categories()
        self.edits = {}
        self._recalc_guard = False  # prevents saving of auto-calculated updates

        layout = QVBoxLayout(self)
        self.db_label = QLabel(f"Database: {config.DB_PATH}")
        layout.addWidget(self.db_label)

        top = QHBoxLayout()
        self.select_db_btn = QPushButton("Select DB")
        self.select_db_btn.clicked.connect(self.select_db)
        top.addWidget(self.select_db_btn)
        top.addWidget(QLabel("Year:"))
        self.year_cb = QComboBox()
        self.year_cb.addItems(self.years or [])
        self.year_cb.currentTextChanged.connect(self.refresh)
        top.addWidget(self.year_cb)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)
        self.save_btn = QPushButton("Save Budgets")
        self.save_btn.clicked.connect(self.save_budgets)
        top.addWidget(self.save_btn)
        layout.addLayout(top)

        self.figure = Figure(figsize=(6, 2.2))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(220)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.canvas)

        self.view = QTreeView()
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
        self.apply_light_theme()
        self.refresh()

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

    def apply_light_theme(self):
        self.setStyleSheet(
            """
            QWidget { background-color: white; color: black; font-size: 11px; }
            QPushButton { background-color: #f3f4f6; border: 1px solid #ccc; border-radius: 5px; padding: 4px 10px; font-size: 11px; }
            QPushButton:hover { background-color: #e2e6ea; }
            QHeaderView::section { background-color: #f2f2f2; color: #111; font-weight: bold; font-size: 11px; }
            QTreeView { alternate-background-color: #fafafa; gridline-color: #eee; font-size: 11px; }
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
            header_names.append(year_name)
            header_names.append("Period")
            header_ids.append(year_id)
            for bid, name in entries[1:]:
                header_names.append(name)
                header_ids.append(bid)
        header_names.append("TOTAL")

        self.model.clear()
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
            budget_columns.append(1)
            for idx in range(3, len(header_names) - 1):
                budget_columns.append(idx)
        for col in budget_columns:
            if 0 <= col < self.model.columnCount():
                self.view.setItemDelegateForColumn(col, self.budget_button_delegate)
        total_col = len(header_names) - 1
        if 0 <= total_col < self.model.columnCount():
            self.view.setItemDelegateForColumn(total_col, self.default_delegate)

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
            self.model.appendRow([cat_item])
            # Light gray background for top-level categories (main categories)
            if depth == 0:
                cat_item.setBackground(QBrush(QColor("#f0f0f0")))

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
            if depth == 0:
                # shade entire row for main categories
                for it in act_row:
                    it.setBackground(QBrush(QColor("#f0f0f0")))
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
            if depth == 0:
                for it in bud_row:
                    it.setBackground(QBrush(QColor("#f0f0f0")))
            # Highlight in red if explicit monthly budgets exceed annual budget in absolute value
            if over_limit:
                tot_item.setBackground(QBrush(QColor("#F8D6D6")))
            cat_item.appendRow(bud_row)

            # Diff row
            diff_row = [make_item("Diff", False)]
            diff_font = QFont("Segoe UI", 9)
            diff_font.setItalic(True)
            total_diff = 0.0
            year_diff = (
                actual_map.get((cid, header_ids[0]), 0.0) - budget_map.get((cid, header_ids[0]), (0.0,))[0]
            )
            year_cell = make_item(f"{year_diff:,.2f}" if year_diff else "", False)
            year_cell.setFont(diff_font)
            year_cell.setBackground(
                QBrush(QColor("#D1F0D1") if year_diff > 0 else QColor("#F8D6D6") if year_diff < 0 else QColor("#EEE"))
            )
            diff_row.append(year_cell)
            diff_row.append(make_item("", False))
            for bid in header_ids[1:]:
                a = actual_map.get((cid, bid), 0.0)
                b = monthly_value_for_diff.get(bid)
                if b is None:
                    b = budget_map.get((cid, bid), (0.0,))[0] or 0.0
                d = a - b
                total_diff += d
                cell = make_item(f"{d:,.2f}" if d else "", False)
                cell.setFont(diff_font)
                cell.setBackground(
                    QBrush(QColor("#D1F0D1") if d > 0 else QColor("#F8D6D6") if d < 0 else QColor("#EEE"))
                )
                diff_row.append(cell)
            total_diff_adjusted = total_act - display_total
            tot_cell = make_item(f"{total_diff_adjusted:,.2f}", False)
            tot_cell.setFont(diff_font)
            tot_cell.setBackground(
                QBrush(
                    QColor("#D1F0D1")
                    if total_diff_adjusted > 0
                    else QColor("#F8D6D6")
                    if total_diff_adjusted < 0
                    else QColor("#EEE")
                )
            )
            diff_row.append(tot_cell)
            if depth == 0:
                # keep per-cell diff coloring; only shade the label cell
                diff_row[0].setBackground(QBrush(QColor("#f0f0f0")))
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

        self.view.expandAll()
        header = self.view.header()
        for col in range(1, self.model.columnCount()):
            header.resizeSection(col, 250)
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
            ("Entrate", actual_income, "#2e7d32"),
            ("Uscite", abs(actual_expense), "#c62828"),
            ("Differenza", actual_income + actual_expense, "#1565c0"),
        ]
        budget_bars = [
            ("Entrate", budget_income, "#81c784"),
            ("Uscite", abs(budget_expense), "#ef5350"),
            ("Differenza", budget_income + budget_expense, "#5e35b1"),
        ]

        self.figure.clear()
        ax_actual = self.figure.add_subplot(1, 2, 1)
        ax_budget = self.figure.add_subplot(1, 2, 2)

        def render_panel(ax, title, items):
            labels = [lbl for lbl, _, _ in items]
            values = [val for _, val, _ in items]
            colors = [clr for _, _, clr in items]
            y_pos = list(range(len(items)))
            ax.barh(y_pos, values, color=colors)
            ax.set_yticks(y_pos)
            ax.set_yticklabels(labels, fontsize=9)
            ax.axvline(0, color="#444", linewidth=0.8)
            ax.set_title(title, fontsize=11)
            max_width = max((abs(v) for v in values), default=1.0) or 1.0
            offset = max_width * 0.03
            for idx, value in enumerate(values):
                x_pos = value + offset if value >= 0 else value - offset
                ax.text(
                    x_pos,
                    idx,
                    f"{value:,.2f}",
                    va="center",
                    ha="left" if value >= 0 else "right",
                    fontsize=8,
                )
            ax.set_xlim(-max_width * 1.3, max_width * 1.3)
            ax.set_xlabel("Valore", fontsize=9)

        render_panel(ax_actual, "Actual", actual_bars)
        render_panel(ax_budget, "Budget", budget_bars)

        max_limit = max(
            [abs(v) for _, v, _ in actual_bars + budget_bars],
            default=1.0,
        ) or 1.0
        for ax in (ax_actual, ax_budget):
            ax.set_xlim(-max_limit * 1.3, max_limit * 1.3)

        self.figure.tight_layout()
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
                        tot_item.setBackground(QBrush(QColor("#f0f0f0")))
                    else:
                        tot_item.setBackground(QBrush())

            # Recompute Diff row
            # Year diff
            year_act = actual_map.get((cid, year_bid), 0.0)
            year_bud = year_amt or 0.0
            year_diff = year_act - year_bud
            year_cell = target.child(diff_row_idx, 1)
            if year_cell:
                year_cell.setText(f"{year_diff:,.2f}" if year_diff else "")
                f = QFont("Segoe UI", 9)
                f.setItalic(True)
                year_cell.setFont(f)
                year_cell.setBackground(
                    QBrush(QColor("#D1F0D1") if year_diff > 0 else QColor("#F8D6D6") if year_diff < 0 else QColor("#EEE"))
                )
            # monthly diffs
            total_diff = 0.0
            for idx, bid in enumerate(month_bids, start=3):
                a = actual_map.get((cid, bid), 0.0)
                b = monthly_value_for_diff.get(bid)
                if b is None:
                    b = base_budget_map.get((cid, bid), (0.0,))[0] or 0.0
                d = a - b
                total_diff += d
                cell = target.child(diff_row_idx, idx)
                if cell:
                    cell.setText(f"{d:,.2f}" if d else "")
                    f = QFont("Segoe UI", 9)
                    f.setItalic(True)
                    cell.setFont(f)
                    cell.setBackground(
                        QBrush(QColor("#D1F0D1") if d > 0 else QColor("#F8D6D6") if d < 0 else QColor("#EEE"))
                    )
            tot_diff_cell = target.child(diff_row_idx, tot_col)
            if tot_diff_cell:
                total_act = actual_map.get((cid, year_bid), 0.0)
                for bid in month_bids:
                    total_act += actual_map.get((cid, bid), 0.0)
                total_diff_adjusted = total_act - display_total
                tot_diff_cell.setText(f"{total_diff_adjusted:,.2f}")
                f = QFont("Segoe UI", 9)
                f.setItalic(True)
                tot_diff_cell.setFont(f)
                tot_diff_cell.setBackground(
                    QBrush(
                        QColor("#D1F0D1")
                        if total_diff_adjusted > 0
                        else QColor("#F8D6D6")
                        if total_diff_adjusted < 0
                        else QColor("#EEE")
                    )
                )
            if not self.children_map.get(cid):
                total_act = actual_map.get((cid, year_bid), 0.0)
                for bid in month_bids:
                    total_act += actual_map.get((cid, bid), 0.0)
                self.category_totals[cid] = {"actual": total_act, "budget": display_total}
                self.update_summary_chart()
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
            item.setBackground(QColor("#E3F2FD"))
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

            item.setBackground(QColor("#E3F2FD"))
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

    def select_db(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Select DB", str(Path.home()), "SQLite (*.mmb *.db)"
        )
        if file:
            config.DB_PATH = Path(file)
            config.save_last_db(config.DB_PATH)
            self.db_label.setText(f"Database: {config.DB_PATH}")
            self.years, self.per_year_entries, self.name_to_id = load_budgetyear_map()
            self.id2name, self.children_map, self.root_ids = load_categories()
            self.year_cb.clear()
            self.year_cb.addItems(self.years or [])
            self.refresh()


def main():
    app = QApplication(sys.argv)
    w = BudgetApp()
    w.show()
    sys.exit(app.exec())
