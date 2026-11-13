"""Account books as TallyPrime shows them: a ledger's monthly summary, its vouchers for a month, and the Cash / Bank books.

A ledger's balance moves with the live vouchers (not cancelled, not optional) that have an entry on it. The monthly summary starts
from the ledger's opening balance plus everything before the period, so the closing balance of the last month is the ledger's
closing balance at the end of the period.
"""
import calendar
import datetime

CASH_BANK_GROUPS = ("Cash-in-Hand", "Bank Accounts", "Bank OD A/c")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _signed_opening(db, ledger):
    row = db.conn.execute("SELECT opening_balance, opening_balance_type FROM ledger WHERE name = ?", (ledger,)).fetchone()
    if row is None:
        raise ValueError(f"Ledger '{ledger}' does not exist")
    return float(row[0] or 0) * (1 if (row[1] or "Dr") == "Dr" else -1)


def _entries(db, ledger, start=None, end=None):
    sql = """SELECT v.id AS voucher_id, v.date, v.voucher_type, v.voucher_number,
                    SUM(CASE WHEN e.entry_type = 'Dr' THEN e.amount ELSE 0 END) AS debit,
                    SUM(CASE WHEN e.entry_type = 'Cr' THEN e.amount ELSE 0 END) AS credit
             FROM voucher_entry e JOIN voucher v ON v.id = e.voucher_id
             WHERE e.ledger_name = ? AND v.company_id = ? AND v.is_cancelled = 0 AND v.is_optional = 0"""
    args = [ledger, db.active_company_id]
    if start:
        sql += " AND v.date >= ?"
        args.append(start)
    if end:
        sql += " AND v.date <= ?"
        args.append(end)
    sql += " GROUP BY v.id ORDER BY v.date, v.id"
    return db.conn.execute(sql, args).fetchall()


def _period(db, start, end):
    company = db.get_company()
    return (start or company.get("financial_year_from") or company.get("books_from") or "2000-01-01",
            end or company.get("financial_year_to") or "2100-01-01")


def month_label(key):
    year, month = key.split("-")
    return f"{MONTHS[int(month) - 1]}-{year}"


def monthly_summary(db, ledger, start=None, end=None):
    """Opening, then debit / credit / closing for each month of the period."""
    start, end = _period(db, start, end)
    opening = _signed_opening(db, ledger)
    before = db.conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN e.entry_type = 'Dr' THEN e.amount ELSE -e.amount END), 0)
           FROM voucher_entry e JOIN voucher v ON v.id = e.voucher_id
           WHERE e.ledger_name = ? AND v.company_id = ? AND v.is_cancelled = 0 AND v.is_optional = 0 AND v.date < ?""",
        (ledger, db.active_company_id, start)).fetchone()[0]
    opening += float(before)
    totals = {}
    for r in _entries(db, ledger, start, end):
        key = r["date"][:7]
        d, c = totals.get(key, (0.0, 0.0))
        totals[key] = (d + float(r["debit"]), c + float(r["credit"]))
    first = datetime.date.fromisoformat(start)
    last = datetime.date.fromisoformat(end)
    months, running = [], opening
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        key = f"{y:04d}-{m:02d}"
        d, c = totals.get(key, (0.0, 0.0))
        running += d - c
        months.append({"month": key, "label": month_label(key), "debit": round(d, 2), "credit": round(c, 2), "closing": round(running, 2)})
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return {"ledger": ledger, "from": start, "to": end, "opening": round(opening, 2), "months": months,
            "debit": round(sum(x["debit"] for x in months), 2), "credit": round(sum(x["credit"] for x in months), 2), "closing": round(running, 2)}


def _particulars(db, voucher_id, ledger):
    """The other side of a voucher as TallyPrime names it: the ledger itself when there is one, the sales / purchase ledger of an
    invoice, otherwise "(as per details)"."""
    rows = db.conn.execute("SELECT ledger_name, entry_type, amount FROM voucher_entry WHERE voucher_id = ? AND ledger_name != ?", (voucher_id, ledger)).fetchall()
    names = []
    for r in rows:
        if r["ledger_name"] not in names:
            names.append(r["ledger_name"])
    if len(names) == 1:
        return names[0]
    groups = {r["name"]: dict(r) for r in db.conn.execute("SELECT name, parent FROM account_group")}
    book = {r["name"]: r["group_name"] for r in db.conn.execute("SELECT name, group_name FROM ledger")}
    items = [n for n in names if db._under_group(groups, book.get(n, ""), ("Sales Accounts", "Purchase Accounts"))]
    if len(items) == 1:
        return items[0]
    return "(as per details)" if names else ""


def month_vouchers(db, ledger, month):
    """The vouchers of one month ("YYYY-MM") with an entry on the ledger, with opening and closing for that month."""
    year, mon = map(int, month.split("-"))
    first = datetime.date(year, mon, 1).isoformat()
    last = datetime.date(year, mon, calendar.monthrange(year, mon)[1]).isoformat()
    summary = monthly_summary(db, ledger, first, last)
    rows = []
    for r in _entries(db, ledger, first, last):
        rows.append({"voucher_id": r["voucher_id"], "date": r["date"], "particulars": _particulars(db, r["voucher_id"], ledger),
                     "voucher_type": r["voucher_type"], "voucher_number": r["voucher_number"], "debit": round(float(r["debit"]), 2), "credit": round(float(r["credit"]), 2)})
    return {"ledger": ledger, "month": month, "label": month_label(month), "opening": summary["opening"], "vouchers": rows,
            "debit": summary["debit"], "credit": summary["credit"], "closing": summary["closing"]}


def ledger_list(db, groups=None):
    """Every ledger (optionally only those under the given groups) with its closing balance."""
    tree = {r["name"]: dict(r) for r in db.conn.execute("SELECT name, parent FROM account_group")}
    out = []
    for r in db.conn.execute("SELECT name, group_name FROM ledger ORDER BY name"):
        if groups and not db._under_group(tree, r["group_name"], groups):
            continue
        bal, side = db.get_ledger_closing_balance(r["name"])
        out.append({"name": r["name"], "group": r["group_name"], "closing": float(bal), "side": side})
    return out


def cash_bank_books(db, start=None, end=None):
    """Opening, debit, credit and closing for every cash and bank ledger over the period."""
    rows = []
    for item in ledger_list(db, CASH_BANK_GROUPS):
        s = monthly_summary(db, item["name"], start, end)
        rows.append({"ledger": item["name"], "group": item["group"], "opening": s["opening"], "debit": s["debit"], "credit": s["credit"], "closing": s["closing"]})
    return {"from": _period(db, start, end)[0], "to": _period(db, start, end)[1], "ledgers": rows}
