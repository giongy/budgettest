from collections import defaultdict
import pandas as pd

from .db import get_conn


def load_budgetyear_map():
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT BUDGETYEARID, BUDGETYEARNAME FROM budgetyear_v1 ORDER BY BUDGETYEARNAME DESC",
            conn,
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
    df["CATEGNAME"] = df["CATEGNAME"].fillna("")
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
    sql = (
        f"SELECT BUDGETENTRYID,BUDGETYEARID,CATEGID,PERIOD,AMOUNT FROM budgettable_v1 WHERE BUDGETYEARID IN ({','.join('?'*len(ids))})"
    )
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


def delete_budget_entry(budgetyearid, categid):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM budgettable_v1 WHERE BUDGETYEARID=? AND CATEGID=?",
            (budgetyearid, categid),
        )
        conn.commit()
