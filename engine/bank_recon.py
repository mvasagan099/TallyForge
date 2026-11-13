"""Bank reconciliation like TallyPrime's: every entry on a bank ledger can carry the date the bank cleared it; the statement shows the
balance as per the company books, the entries the bank has not reflected (by date), and the balance as per the bank.

TallyPrime keeps the bank date on each bank entry but does not give it to an XML export of vouchers, so a company imported from
TallyPrime starts with every entry unreconciled here; dates are recorded in TallyForge by hand or from a statement.
"""
import datetime

BANK_GROUPS = ("Bank Accounts", "Bank OD A/c")


def bank_ledgers(db):
    groups = {r["name"]: dict(r) for r in db.conn.execute("SELECT name, parent FROM account_group")}
    names = [r["name"] for r in db.conn.execute("SELECT name, group_name FROM ledger") if db._under_group(groups, r["group_name"], BANK_GROUPS)]
    counts = {n: db.conn.execute("SELECT COUNT(*) FROM voucher_entry WHERE ledger_name = ?", (n,)).fetchone()[0] for n in names}
    return sorted(names, key=lambda n: (-counts[n], n))


def _check_ledger(db, ledger):
    if ledger not in bank_ledgers(db):
        raise ValueError(f"'{ledger}' is not a bank ledger")


def sync(db, ledger):
    """Make sure every live voucher with an entry on the bank ledger has a reconciliation row."""
    _check_ledger(db, ledger)
    db.conn.execute(
        """INSERT INTO bank_reconciliation(voucher_id, bank_ledger_name, instrument_no, instrument_date, status)
           SELECT v.id, ?, COALESCE(NULLIF(v.reference_no, ''), ''), v.date, 'Unreconciled' FROM voucher v
           WHERE v.company_id = ? AND EXISTS (SELECT 1 FROM voucher_entry e WHERE e.voucher_id = v.id AND e.ledger_name = ?)
             AND NOT EXISTS (SELECT 1 FROM bank_reconciliation b WHERE b.voucher_id = v.id AND b.bank_ledger_name = ?)""",
        (ledger, db.active_company_id, ledger, ledger))
    db.conn.commit()


def statement(db, ledger, as_of=None):
    """Bank reconciliation statement at ``as_of`` (default: today). Signed amounts are debit positive."""
    as_of = as_of or datetime.date.today().isoformat()
    if not db._valid_iso_date(as_of):
        raise ValueError("Invalid date: use YYYY-MM-DD")
    sync(db, ledger)
    row = db.conn.execute("SELECT opening_balance, opening_balance_type FROM ledger WHERE name = ?", (ledger,)).fetchone()
    opening = float(row[0] or 0) * (1 if (row[1] or "Dr") == "Dr" else -1)
    rows = db.conn.execute(
        """SELECT b.id, b.voucher_id, b.instrument_no, b.bank_date, v.voucher_number, v.voucher_type, v.date,
                  SUM(CASE WHEN e.entry_type = 'Dr' THEN e.amount ELSE -e.amount END) AS signed
           FROM bank_reconciliation b JOIN voucher v ON v.id = b.voucher_id
           JOIN voucher_entry e ON e.voucher_id = v.id AND e.ledger_name = b.bank_ledger_name
           WHERE b.bank_ledger_name = ? AND v.company_id = ? AND v.is_cancelled = 0 AND v.is_optional = 0 AND v.date <= ?
           GROUP BY b.id ORDER BY v.date, v.id""", (ledger, db.active_company_id, as_of)).fetchall()
    book = opening + sum(r["signed"] for r in rows)
    pending, cleared = [], 0
    for r in rows:
        item = {"recon_id": r["id"], "voucher_id": r["voucher_id"], "date": r["date"], "voucher_number": r["voucher_number"], "voucher_type": r["voucher_type"],
                "instrument_no": r["instrument_no"] or "", "bank_date": r["bank_date"] or "", "amount": round(abs(r["signed"]), 2),
                "side": "Dr" if r["signed"] >= 0 else "Cr"}
        if r["bank_date"] and r["bank_date"] <= as_of:
            cleared += 1
        else:
            pending.append({**item, "signed": round(r["signed"], 2)})
    dr = round(sum(p["amount"] for p in pending if p["side"] == "Dr"), 2)
    cr = round(sum(p["amount"] for p in pending if p["side"] == "Cr"), 2)
    bank = round(book - dr + cr, 2)
    return {"bank_ledger": ledger, "as_of": as_of, "book_balance": round(book, 2), "unreflected_debits": dr, "unreflected_credits": cr,
            "bank_balance": bank, "pending": pending, "cleared_count": cleared, "pending_count": len(pending),
            "note": "Balance as per bank = balance as per books less debits not yet cleared plus credits not yet cleared (debit positive)."}


def mark(db, voucher_id, ledger, bank_date):
    """Record the date the bank cleared this voucher's entry on the ledger (an empty date makes it unreconciled again)."""
    _check_ledger(db, ledger)
    if bank_date and not db._valid_iso_date(bank_date):
        raise ValueError("Invalid bank date: use YYYY-MM-DD")
    sync(db, ledger)
    row = db.conn.execute("SELECT id FROM bank_reconciliation WHERE voucher_id = ? AND bank_ledger_name = ?", (voucher_id, ledger)).fetchone()
    if not row:
        raise ValueError("That voucher has no entry on this bank ledger")
    db.conn.execute("UPDATE bank_reconciliation SET bank_date = ?, status = ? WHERE id = ?", (bank_date or None, "Reconciled" if bank_date else "Unreconciled", row[0]))
    db._log_edit("BRS", str(voucher_id), "ALTER", f"{ledger}: bank date {bank_date or 'cleared'}")
    db.conn.commit()
    return {"voucher_id": voucher_id, "bank_date": bank_date or ""}


def match_statement(db, ledger, lines, window_days=7):
    """Match statement lines ({date, amount, ref, side}) to pending entries: the amount must agree to the paisa, and when several
    entries share it the one with the same reference, else the nearest date within ``window_days``, is taken. A line that cannot be
    matched to exactly one entry is left for a person to decide. ``side`` Dr / Cr is used when given (a bank credit is our debit)."""
    sync(db, ledger)
    pending = statement(db, ledger, "9999-12-31")["pending"]
    taken, matched, unmatched = set(), [], []
    for line in lines:
        try:
            amount = round(abs(float(str(line.get("amount", 0)).replace(",", ""))), 2)
        except ValueError:
            unmatched.append({**line, "reason": "amount is not a number"})
            continue
        when = line.get("date") or ""
        ref = str(line.get("ref") or "").strip().lower()
        want = {"Dr": "Dr", "Cr": "Cr"}.get(line.get("side"))
        pool = [p for p in pending if p["recon_id"] not in taken and abs(p["amount"] - amount) < 0.01 and (not want or p["side"] == want)]
        if ref:
            by_ref = [p for p in pool if p["instrument_no"].lower() == ref]
            pool = by_ref or pool
        several = len(pool) > 1
        if when and len(pool) > 1:
            def gap(p):
                return abs((datetime.date.fromisoformat(p["date"]) - datetime.date.fromisoformat(when)).days) if db._valid_iso_date(when) else 0
            near = sorted(pool, key=gap)
            pool = [near[0]] if gap(near[0]) <= window_days and (len(near) == 1 or gap(near[0]) < gap(near[1])) else []
        if len(pool) == 1 and db._valid_iso_date(when):
            taken.add(pool[0]["recon_id"])
            mark(db, pool[0]["voucher_id"], ledger, when)
            matched.append({**line, "voucher_number": pool[0]["voucher_number"]})
        else:
            unmatched.append({**line, "reason": "more than one possible entry" if (pool or several) else "no entry with this amount"})
    return {"matched": len(matched), "total_lines": len(lines), "matched_lines": matched, "unmatched_lines": unmatched}
