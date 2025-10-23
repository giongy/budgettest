# budget_app.py
# PyQt6 Budget Manager — tema chiaro, righe Actual e Diff allineate, colonna Diff rimossa

import sys
import sqlite3
from pathlib import Path
from collections import defaultdict
import configparser
import pandas as pd

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTreeView, QHeaderView, QMessageBox, QStyledItemDelegate,
    QLineEdit, QFileDialog
)
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QColor, QFont, QBrush
from PyQt6.QtCore import Qt


# ---------------- CONFIG ----------------
CONFIG_FILE = Path(__file__).with_suffix(".ini")
PERIOD_CHOICES = ["Monthly", "Yearly", "Weekly"]


# ---------------- DB PATH ----------------
def load_last_db():
    if CONFIG_FILE.exists():
        cfg = configparser.ConfigParser()
        cfg.read(CONFIG_FILE)
        if "app" in cfg and "db_path" in cfg["app"]:
            path = Path(cfg["app"]["db_path"])
            if path.exists():
                return path
    return Path(r"D:\budgettest\mmex_casa.mmb")


def save_last_db(path: Path):
    cfg = configparser.ConfigParser()
    cfg["app"] = {"db_path": str(path)}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        cfg.write(f)


DB_PATH = load_last_db()


# ---------------- DB HELPERS ----------------
def get_conn():
    return sqlite3.connect(str(DB_PATH))


def load_budgetyear_map():
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT BUDGETYEARID, BUDGETYEARNAME FROM budgetyear_v1 ORDER BY BUDGETYEARNAME DESC", conn
        )
    if df.empty:
        return [], {}, {}
    names = df["BUDGETYEARNAME"].astype(str).tolist()
    years = sorted({n for n in names if len(n) == 4 and n.isdigit()}, reverse=True)
    name_to_id = dict(zip(df["BUDGETYEARNAME"], df["BUDGETYEARID"]))
    per_year = {}
    for y in years:
        entries = []
        if y in name_to_id:
            entries.append((name_to_id[y], y))
        for m in range(1, 13):
            mn = f"{y}-{m:02d}"
            if mn in name_to_id:
                entries.append((name_to_id[mn], mn))
        per_year[y] = entries
    return years, per_year, name_to_id


def load_categories():
    with get_conn() as conn:
        df = pd.read_sql_query("SELECT CATEGID, CATEGNAME, PARENTID FROM category_v1", conn)
    df["CATEGNAME"] = df["CATEGNAME"].fillna("—")
    id2name = dict(zip(df["CATEGID"], df["CATEGNAME"]))
    parent = dict(zip(df["CATEGID"], df["PARENTID"]))
    children = defaultdict(list)
    for cid, pid in parent.items():
        children[pid].append(cid)
    roots = [cid for cid, pid in parent.items() if pid in (-1, None, 0) or pid not in id2name]
    return id2name, children, roots


def fetch_actuals_for_year(year):
    sql = """
    WITH wd AS (
        SELECT t1.transdate AS date,
               CASE WHEN t1.STATUS = 'V' THEN 0
                    WHEN t1.TRANSCODE = 'Withdrawal' THEN -1 * t2.splittransamount
                    WHEN t1.TRANSCODE = 'Transfer' AND t1.TOACCOUNTID <> t1.ACCOUNTID THEN -1 * t2.splittransamount
                    ELSE t2.splittransamount END AS amount,
               t2.categid AS categid
        FROM splittransactions_v1 t2
        JOIN checkingaccount_v1 t1 ON t1.TRANSID = t2.TRANSID
        UNION ALL
        SELECT ca.transdate AS date,
               CASE WHEN ca.STATUS = 'V' THEN 0
                    WHEN ca.TRANSCODE = 'Withdrawal' THEN -1 * ca.TRANSAMOUNT
                    WHEN ca.TRANSCODE = 'Transfer' AND ca.TOACCOUNTID <> ca.ACCOUNTID THEN -1 * ca.TRANSAMOUNT
                    ELSE ca.TRANSAMOUNT END AS amount,
               ca.categid AS categid
        FROM checkingaccount_v1 ca
        WHERE ca.categid <> -1 AND ca.transcode <> 'Transfer'
    )
    SELECT substr(date,1,7) AS month, categid, SUM(amount) AS amount
    FROM wd WHERE substr(date,1,4) = ? GROUP BY month, categid
    """
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=[year])
    return df if not df.empty else pd.DataFrame(columns=["month", "categid", "amount"])


def load_budgets_for_year(year, name_to_id, per_year_entries):
    if year not in per_year_entries:
        return pd.DataFrame(columns=["BUDGETENTRYID", "BUDGETYEARID", "CATEGID", "PERIOD", "AMOUNT"])
    ids = [bid for bid, _ in per_year_entries[year]]
    sql = f"SELECT BUDGETENTRYID,BUDGETYEARID,CATEGID,PERIOD,AMOUNT FROM budgettable_v1 WHERE BUDGETYEARID IN ({','.join('?'*len(ids))})"
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=ids)
    if df.empty:
        return pd.DataFrame(columns=["BUDGETENTRYID", "BUDGETYEARID", "CATEGID", "PERIOD", "AMOUNT"])
    id_to_name = {bid: name for bid, name in per_year_entries[year]}
    df["BUDGETYEARNAME"] = df["BUDGETYEARID"].map(id_to_name)
    return df


def upsert_budget_entry(budgetyearid, categid, period, amount):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT BUDGETENTRYID FROM budgettable_v1 WHERE BUDGETYEARID=? AND CATEGID=?",
            (budgetyearid, categid),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE budgettable_v1 SET AMOUNT=?, PERIOD=? WHERE BUDGETENTRYID=?",
                (float(amount), str(period), row[0]),
            )
        else:
            cur.execute(
                "INSERT INTO budgettable_v1 (BUDGETYEARID,CATEGID,PERIOD,AMOUNT,ACTIVE) VALUES (?,?,?,?,1)",
                (budgetyearid, categid, str(period), float(amount)),
            )
        conn.commit()


# ---------------- UI ----------------
def make_item(text="", editable=False, meta=None, bold=False, color=None):
    item = QStandardItem(str(text))
    item.setEditable(editable)
    font = QFont("Segoe UI", 11)
    if bold:
        font.setBold(True)
        font.setPointSize(13)
    item.setFont(font)
    if color:
        item.setForeground(QBrush(color))
    if meta and isinstance(meta, tuple) and meta[0] == "category_label":
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft)
    else:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight)
    if meta:
        item.setData(meta, Qt.ItemDataRole.UserRole)
    return item


class PeriodDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(PERIOD_CHOICES)
        return combo

    def setEditorData(self, editor, index):
        val = index.model().data(index, Qt.ItemDataRole.EditRole) or ""
        editor.setCurrentText(val if val in PERIOD_CHOICES else PERIOD_CHOICES[0])

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class BudgetApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Budget Manager — Tema Chiaro")
        self.resize(1300, 800)
        self.years, self.per_year_entries, self.name_to_id = load_budgetyear_map()
        self.id2name, self.children_map, self.root_ids = load_categories()
        self.edits = {}

        layout = QVBoxLayout(self)
        self.db_label = QLabel(f"Database: {DB_PATH}")
        layout.addWidget(self.db_label)

        top = QHBoxLayout()
        self.select_db_btn = QPushButton("📂 Select DB")
        self.select_db_btn.clicked.connect(self.select_db)
        top.addWidget(self.select_db_btn)
        top.addWidget(QLabel("Year:"))
        self.year_cb = QComboBox()
        self.year_cb.addItems(self.years or [])
        self.year_cb.currentTextChanged.connect(self.refresh)
        top.addWidget(self.year_cb)
        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)
        self.save_btn = QPushButton("💾 Save Budgets")
        self.save_btn.clicked.connect(self.save_budgets)
        top.addWidget(self.save_btn)
        layout.addLayout(top)

        self.view = QTreeView()
        self.model = QStandardItemModel()
        self.view.setModel(self.model)
        self.view.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.view)

        self.period_delegate = PeriodDelegate()
        self.apply_light_theme()
        self.refresh()

    def apply_light_theme(self):
        self.setStyleSheet("""
            QWidget { background-color: white; color: black; }
            QPushButton { background-color: #f3f4f6; border: 1px solid #ccc; border-radius: 5px; padding: 4px 10px; }
            QPushButton:hover { background-color: #e2e6ea; }
            QHeaderView::section { background-color: #f2f2f2; color: #111; font-weight: bold; }
            QTreeView { alternate-background-color: #fafafa; gridline-color: #eee; }
        """)

    # --- REFRESH COMPLETO CON ALLINEAMENTI CORRETTI ---
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
        self.view.setItemDelegateForColumn(2, self.period_delegate)

        df_actual = fetch_actuals_for_year(year)
        df_bud = load_budgets_for_year(year, self.name_to_id, self.per_year_entries)
        colname_to_bid = {name: bid for bid, name in entries}

        actual_map = {(int(r["categid"]), colname_to_bid.get(r["month"])): float(r["amount"])
                      for _, r in df_actual.iterrows() if colname_to_bid.get(r["month"])}
        budget_map = {(int(r["CATEGID"]), int(r["BUDGETYEARID"])): (float(r["AMOUNT"] or 0), r["PERIOD"] or "Monthly")
                      for _, r in df_bud.iterrows()}

        def add_category(cid, depth=0):
            cname = ("    " * depth) + self.id2name.get(cid, f"(id:{cid})")
            cat_item = make_item(cname, False, bold=True, color=QColor("#000"), meta=("category_label", cid, None))
            self.model.appendRow([cat_item])

            # ----- ACTUAL (allineato) -----
            act_row = [make_item("Actual", False)]
            total_act = 0.0
            # annuale
            year_bid = header_ids[0]
            val_year = actual_map.get((cid, year_bid), 0.0)
            total_act += val_year
            col = QColor("#1b5e20") if val_year > 0 else QColor("#b71c1c") if val_year < 0 else QColor("#000")
            act_row.append(make_item(f"{val_year:,.2f}", False, ("actual", cid, year_bid), color=col))
            # colonna Period
            act_row.append(make_item("", False))
            # mensili
            for bid in header_ids[1:]:
                val = actual_map.get((cid, bid), 0.0)
                total_act += val
                col = QColor("#1b5e20") if val > 0 else QColor("#b71c1c") if val < 0 else QColor("#000")
                act_row.append(make_item(f"{val:,.2f}", False, ("actual", cid, bid), color=col))
            # totale
            act_row.append(make_item(f"{total_act:,.2f}", False))
            cat_item.appendRow(act_row)

            # ----- BUDGET (allineato) -----
            bud_row = [make_item("Budget", False)]
            first_bid = header_ids[0]
            year_amt, year_per = budget_map.get((cid, first_bid), (0.0, "Monthly"))
            bud_row.append(make_item(f"{year_amt:,.2f}" if year_amt else "", True, ("budget", cid, first_bid),
                                     color=QColor("#01579b")))
            bud_row.append(make_item(year_per, True, ("budget_period", cid, None)))
            total_bud = 0.0
            monthly_budgets = {}
            missing_months = 0
            for bid in header_ids[1:]:
                amt, _ = budget_map.get((cid, bid), (None, "Monthly"))
                if amt is not None:
                    total_bud += amt
                    monthly_budgets[bid] = amt
                else:
                    missing_months += 1
                bud_row.append(make_item(f"{amt:,.2f}" if amt is not None else "", True,
                                         ("budget", cid, bid), color=QColor("#01579b")))
            if year_amt:
                if year_per == "Yearly":
                    total_bud += (year_amt / 12.0) * missing_months
                elif year_per == "Weekly":
                    total_bud += (year_amt * 52.0 / 12.0) * missing_months
                else:
                    total_bud += year_amt * missing_months
            bud_row.append(make_item(f"{total_bud:,.2f}", False))
            cat_item.appendRow(bud_row)

            # ----- DIFF (allineato) -----
            diff_row = [make_item("Diff", False)]
            diff_font = QFont("Segoe UI", 10)
            diff_font.setItalic(True)
            total_diff = 0.0
            # annuale
            year_diff = actual_map.get((cid, header_ids[0]), 0.0) - budget_map.get((cid, header_ids[0]), (0.0,))[0]
            year_cell = make_item(f"{year_diff:,.2f}" if year_diff else "", False)
            year_cell.setFont(diff_font)
            year_cell.setBackground(QBrush(QColor("#D1F0D1") if year_diff > 0 else QColor("#F8D6D6") if year_diff < 0 else QColor("#EEE")))
            diff_row.append(year_cell)
            # colonna Period
            diff_row.append(make_item("", False))
            # mensili
            for bid in header_ids[1:]:
                a = actual_map.get((cid, bid), 0.0)
                b = budget_map.get((cid, bid), (0.0,))[0]
                d = a - b
                total_diff += d
                cell = make_item(f"{d:,.2f}" if d else "", False)
                cell.setFont(diff_font)
                cell.setBackground(QBrush(QColor("#D1F0D1") if d > 0 else QColor("#F8D6D6") if d < 0 else QColor("#EEE")))
                diff_row.append(cell)
            tot_cell = make_item(f"{total_diff:,.2f}", False)
            tot_cell.setFont(diff_font)
            tot_cell.setBackground(QBrush(QColor("#D1F0D1") if total_diff > 0 else QColor("#F8D6D6") if total_diff < 0 else QColor("#EEE")))
            diff_row.append(tot_cell)
            cat_item.appendRow(diff_row)

            # figli
            for ch in sorted(self.children_map.get(cid, []), key=lambda x: self.id2name.get(x, "")):
                add_category(ch, depth + 1)

        for r in self.root_ids:
            add_category(r)

        self.view.expandAll()
        try:
            self.model.itemChanged.disconnect()
        except:
            pass
        self.model.itemChanged.connect(self.on_item_changed)

    def on_item_changed(self, item):
        meta = item.data(Qt.ItemDataRole.UserRole)
        if not meta:
            return
        kind = meta[0]

        if kind == "budget":
            _, cid, bid = meta
            try:
                val = float(str(item.text()).replace(",", "").strip() or 0.0)
            except:
                return
            item.setBackground(QColor("#E3F2FD"))
            # conserva il period già impostato per l'annuale
            period = self.edits.get((bid, cid), {}).get("period")
            if not period:
                # prova a leggerlo dalla riga Budget
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
                            period_cell = cat_item.child(rr, 2)  # dopo Yearly
                            period = (period_cell.text() or "Monthly").strip()
                            break
                    break
            self.edits[(int(bid), int(cid))] = {"amount": val, "period": period or "Monthly"}

        elif kind == "budget_period":
            _, cid, _ = meta
            val = item.text().strip() or "Monthly"
            year = self.year_cb.currentText()
            bid = self.per_year_entries[year][0][0]  # BUDGETYEARID della colonna annuale

            # leggi importo annuale corrente dalla riga Budget (colonna annuale, index 1)
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
                        except:
                            amount_val = 0.0
                        break
                break

            item.setBackground(QColor("#E3F2FD"))
            self.edits[(int(bid), int(cid))] = {"period": val, "amount": amount_val}

    def save_budgets(self):
        if not self.edits:
            QMessageBox.information(self, "No changes", "No budget changes to save.")
            return
        if QMessageBox.question(
            self, "Confirm", f"Save {len(self.edits)} changes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            for (bid, cid), data in self.edits.items():
                upsert_budget_entry(bid, cid, data.get("period", "Monthly"), data.get("amount", 0.0))
            QMessageBox.information(self, "Saved", "Budgets saved successfully.")
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def select_db(self):
        file, _ = QFileDialog.getOpenFileName(self, "Select DB", str(Path.home()), "SQLite (*.mmb *.db)")
        if file:
            global DB_PATH
            DB_PATH = Path(file)
            save_last_db(DB_PATH)
            self.db_label.setText(f"Database: {DB_PATH}")
            # ricarica metadati (anni, categorie) per il nuovo DB
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


if __name__ == "__main__":
    main()


