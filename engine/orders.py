"""Order processing like TallyPrime's: Sales Order and Purchase Order vouchers with a due date per line, quantities delivered
or received against each order line by later vouchers (invoices, delivery / receipt notes), and the pending-orders report.

An order voucher posts nothing and moves no stock (the importer and the entry code both keep it out of the books). Its lines
are items with quantity, rate and a due date. A later voucher can allocate quantity to an order line; the line's pending
quantity is its ordered quantity less everything allocated by vouchers that are not cancelled.

Checked on a real company against TallyPrime's own *Purchase Orders* report: order number, item, pending quantity, rate, value
and due date agree for every order (see the tests and the status notes).
"""
import datetime

ORDER_TYPES = ("Sales Order", "Purchase Order")
NOTE_TYPES = ("Delivery Note", "Receipt Note")
TRACKED = ORDER_TYPES + NOTE_TYPES
# the voucher types whose lines may be allocated to each kind of document: an order is fulfilled by a note or an invoice, and a
# note (stock moved before the bill) is invoiced later by a sales / purchase invoice raised against it
FULFILS = {"Sales Order": ("Sales", "Delivery Note"), "Purchase Order": ("Purchase", "Receipt Note"),
           "Delivery Note": ("Sales",), "Receipt Note": ("Purchase",)}

SCHEMA = """
CREATE TABLE IF NOT EXISTS order_line (
    voucher_id INTEGER NOT NULL REFERENCES voucher(id) ON DELETE CASCADE,
    stock_item_name TEXT NOT NULL,
    due_date TEXT,
    PRIMARY KEY (voucher_id, stock_item_name)
);
CREATE TABLE IF NOT EXISTS order_allocation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_voucher_id INTEGER NOT NULL REFERENCES voucher(id) ON DELETE CASCADE,
    stock_item_name TEXT NOT NULL,
    against_voucher_id INTEGER NOT NULL REFERENCES voucher(id) ON DELETE CASCADE,
    qty REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS note_original (
    voucher_id INTEGER NOT NULL REFERENCES voucher(id) ON DELETE CASCADE,
    stock_item_name TEXT NOT NULL,
    qty REAL NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY (voucher_id, stock_item_name)
);
CREATE INDEX IF NOT EXISTS idx_order_allocation_order ON order_allocation(order_voucher_id, stock_item_name);
CREATE INDEX IF NOT EXISTS idx_order_allocation_against ON order_allocation(against_voucher_id);
"""


def ensure_tables(db):
    # Plain statements, not executescript: executescript commits the open transaction first, which would make a refused
    # alteration permanent when this runs in the middle of one.
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.conn.execute(statement)


def _party(db, groups, ledgers, voucher_id):
    for e in db.conn.execute("SELECT ledger_name FROM voucher_entry WHERE voucher_id = ?", (voucher_id,)):
        if db._under_group(groups, ledgers.get(e[0], ""), ("Sundry Debtors", "Sundry Creditors")):
            return e[0]
    first = db.conn.execute("SELECT ledger_name FROM voucher_entry WHERE voucher_id = ? ORDER BY id LIMIT 1", (voucher_id,)).fetchone()
    return first[0] if first else ""


def ensure_note_original(db, note_voucher_id):
    """Remember what a note originally carried (its stock lines shrink as invoices are raised against it)."""
    ensure_tables(db)
    for r in db.conn.execute("SELECT stock_item_name, SUM(qty), SUM(amount) FROM voucher_inventory WHERE voucher_id = ? GROUP BY stock_item_name", (note_voucher_id,)).fetchall():
        db.conn.execute("INSERT OR IGNORE INTO note_original(voucher_id, stock_item_name, qty, amount) VALUES(?,?,?,?)", (note_voucher_id, r[0], r[1], r[2]))


def _ordered(db, voucher_id, item):
    ensure_tables(db)
    row = db.conn.execute("SELECT qty FROM note_original WHERE voucher_id = ? AND stock_item_name = ?", (voucher_id, item)).fetchone()
    if row:
        return row[0]
    return db.conn.execute("SELECT SUM(qty) FROM voucher_inventory WHERE voucher_id = ? AND stock_item_name = ?", (voucher_id, item)).fetchone()[0]


def order_lines(db, order_type, party=None):
    """Every line of every live order (or delivery / receipt note) of this type with what has been allocated and what is pending."""
    if order_type not in TRACKED:
        raise ValueError("Order type must be a Sales Order, Purchase Order, Delivery Note or Receipt Note")
    ensure_tables(db)
    groups = {r["name"]: dict(r) for r in db.conn.execute("SELECT name, parent FROM account_group")}
    ledgers = {r["name"]: r["group_name"] for r in db.conn.execute("SELECT name, group_name FROM ledger")}
    done = {}
    for r in db.conn.execute(
            """SELECT a.order_voucher_id, a.stock_item_name, SUM(a.qty) AS qty FROM order_allocation a JOIN voucher av ON av.id = a.against_voucher_id
               WHERE av.is_cancelled = 0 GROUP BY a.order_voucher_id, a.stock_item_name"""):
        done[(r[0], r[1])] = r[2]
    out, seen_party = [], {}
    for r in db.conn.execute(
            """SELECT v.id, v.date, v.voucher_number, v.reference_no, vi.id AS line_id, vi.stock_item_name, vi.qty, vi.rate, vi.amount,
                      l.due_date, si.uom_symbol
               FROM voucher v JOIN voucher_inventory vi ON vi.voucher_id = v.id
               LEFT JOIN order_line l ON l.voucher_id = v.id AND l.stock_item_name = vi.stock_item_name
               LEFT JOIN stock_item si ON si.name = vi.stock_item_name
               WHERE v.voucher_type = ? AND v.is_cancelled = 0 ORDER BY v.date, v.id, vi.id""", (order_type,)):
        vid = r["id"]
        if vid not in seen_party:
            seen_party[vid] = _party(db, groups, ledgers, vid)
        if party and seen_party[vid] != party:
            continue
        delivered = done.get((vid, r["stock_item_name"]), 0.0)
        ordered = _ordered(db, vid, r["stock_item_name"]) if order_type in NOTE_TYPES else r["qty"]
        out.append({"voucher_id": vid, "date": r["date"], "voucher_number": r["voucher_number"], "order_no": r["reference_no"] or r["voucher_number"],
                    "party": seen_party[vid], "stock_item_name": r["stock_item_name"], "unit": r["uom_symbol"] or "", "ordered_qty": ordered,
                    "delivered_qty": delivered, "pending_qty": round(ordered - delivered, 6), "rate": r["rate"], "amount": r["amount"],
                    "due_date": r["due_date"] or ""})
    return out


def pending_orders(db, order_type, as_of=None, party=None):
    """TallyPrime's order report: pending lines grouped by order number, party, item, rate and due date (several vouchers
    carrying the same order number and terms are one row), with value and days overdue at ``as_of``."""
    as_of = as_of or datetime.date.today().isoformat()
    groups = {}
    for line in order_lines(db, order_type, party):
        if line["pending_qty"] <= 0:
            continue
        key = (line["order_no"], line["party"], line["stock_item_name"], line["rate"], line["due_date"])
        row = groups.setdefault(key, {"order_no": line["order_no"], "party": line["party"], "stock_item_name": line["stock_item_name"],
                                      "unit": line["unit"], "date": line["date"], "rate": line["rate"], "due_date": line["due_date"],
                                      "ordered_qty": 0.0, "pending_qty": 0.0, "voucher_ids": []})
        row["ordered_qty"] += line["ordered_qty"]
        row["pending_qty"] += line["pending_qty"]
        row["voucher_ids"].append(line["voucher_id"])
        row["date"] = min(row["date"], line["date"])
    rows = []
    for row in groups.values():
        row["pending_value"] = round(row["pending_qty"] * row["rate"], 2)
        row["overdue_days"] = max(0, (datetime.date.fromisoformat(as_of) - datetime.date.fromisoformat(row["due_date"])).days) if row["due_date"] else 0
        rows.append(row)
    return sorted(rows, key=lambda r: (r["date"], r["order_no"], r["stock_item_name"]))


def set_due_dates(db, order_voucher_id, dues):
    """{item name: due date} for the lines of an order voucher."""
    ensure_tables(db)
    for item, due in dues.items():
        if due and not db._valid_iso_date(due):
            raise ValueError(f"Invalid due date '{due}': use YYYY-MM-DD")
        db.conn.execute("INSERT OR REPLACE INTO order_line(voucher_id, stock_item_name, due_date) VALUES(?,?,?)", (order_voucher_id, item, due or None))


def sync_note(db, note_voucher_id):
    """Set a note's stock lines to what it carried less what invoices raised against it have taken over, so stock is never counted
    twice: the note holds the undelivered / unreceived balance and the invoices hold the rest."""
    ensure_tables(db)
    ensure_note_original(db, note_voucher_id)
    for item, original, amount in db.conn.execute("SELECT stock_item_name, qty, amount FROM note_original WHERE voucher_id = ?", (note_voucher_id,)).fetchall():
        lines = db.conn.execute("SELECT id FROM voucher_inventory WHERE voucher_id = ? AND stock_item_name = ? ORDER BY id", (note_voucher_id, item)).fetchall()
        if len(lines) != 1:
            continue                                    # a note with the same item on several lines is left as it is
        taken = db.conn.execute(
            """SELECT COALESCE(SUM(a.qty), 0) FROM order_allocation a JOIN voucher av ON av.id = a.against_voucher_id
               WHERE a.order_voucher_id = ? AND a.stock_item_name = ? AND av.is_cancelled = 0""", (note_voucher_id, item)).fetchone()[0]
        remaining = max(0.0, original - taken)
        db.conn.execute("UPDATE voucher_inventory SET qty = ?, amount = ? WHERE id = ?",
                        (remaining, round(amount * remaining / original, 2) if original else 0.0, lines[0][0]))


def reset_note_after_alter(db, note_voucher_id):
    """A note shows what is still undelivered; when someone alters it, what they typed is the new remaining quantity, so the
    original is that plus what invoices already took."""
    ensure_tables(db)
    db.conn.execute("DELETE FROM note_original WHERE voucher_id = ?", (note_voucher_id,))
    for item, qty, amount in db.conn.execute("SELECT stock_item_name, SUM(qty), SUM(amount) FROM voucher_inventory WHERE voucher_id = ? GROUP BY stock_item_name", (note_voucher_id,)).fetchall():
        taken = db.conn.execute(
            """SELECT COALESCE(SUM(a.qty), 0) FROM order_allocation a JOIN voucher av ON av.id = a.against_voucher_id
               WHERE a.order_voucher_id = ? AND a.stock_item_name = ? AND av.is_cancelled = 0""", (note_voucher_id, item)).fetchone()[0]
        original = qty + taken
        db.conn.execute("INSERT INTO note_original(voucher_id, stock_item_name, qty, amount) VALUES(?,?,?,?)",
                        (note_voucher_id, item, original, round(amount * original / qty, 2) if qty else amount))


def notes_touched_by(db, voucher_id):
    """The delivery / receipt notes this voucher has allocations against."""
    ensure_tables(db)
    return [r[0] for r in db.conn.execute(
        "SELECT DISTINCT a.order_voucher_id FROM order_allocation a JOIN voucher n ON n.id = a.order_voucher_id WHERE a.against_voucher_id = ? AND n.voucher_type IN ('Delivery Note','Receipt Note')",
        (voucher_id,))]


def assert_allocations_hold(db, voucher_id):
    """An altered voucher must still carry at least what has been allocated from it."""
    ensure_tables(db)
    for item, allocated in db.conn.execute(
            "SELECT stock_item_name, SUM(qty) FROM order_allocation WHERE against_voucher_id = ? GROUP BY stock_item_name", (voucher_id,)).fetchall():
        carried = db.conn.execute("SELECT COALESCE(SUM(qty), 0) FROM voucher_inventory WHERE voucher_id = ? AND stock_item_name = ?", (voucher_id, item)).fetchone()[0]
        if carried + 1e-9 < allocated:
            raise ValueError(f"'{item}' cannot go below {allocated:g}: that much is allocated to orders or notes")


def check_allocations(db, against_type, lines, allocations, against_voucher_id=None):
    """Validate allocations without writing. ``lines`` = {item: quantity on the fulfilling voucher}. Returns the cleaned list.
    A line cannot be over-delivered, the voucher type must fulfil the order, and the item must be on both documents. Quantities
    this voucher already allocated (when it is being altered) do not count against the order."""
    cleaned, used = [], {}
    for a in allocations or []:
        qty = float(a.get("qty", 0) or 0)
        if qty <= 0:
            continue
        order = db.conn.execute("SELECT voucher_type, is_cancelled FROM voucher WHERE id = ?", (a.get("order_voucher_id"),)).fetchone()
        if not order or order[0] not in TRACKED or order[1]:
            raise ValueError("Order not found")
        if against_type not in FULFILS[order[0]]:
            raise ValueError(f"A {against_type} cannot be allocated to a {order[0]}")
        item = a.get("stock_item_name")
        if item not in lines:
            raise ValueError(f"'{item}' is not on this {against_type}")
        used[item] = used.get(item, 0.0) + qty
        if used[item] > lines[item] + 1e-9:
            raise ValueError(f"More of '{item}' is allocated to orders than the {against_type} carries")
        if order[0] in NOTE_TYPES:
            ensure_note_original(db, a["order_voucher_id"])
        ordered = _ordered(db, a["order_voucher_id"], item)
        if ordered is None:
            raise ValueError(f"'{item}' is not on that order")
        taken = db.conn.execute(
            """SELECT COALESCE(SUM(a.qty), 0) FROM order_allocation a JOIN voucher av ON av.id = a.against_voucher_id
               WHERE a.order_voucher_id = ? AND a.stock_item_name = ? AND av.is_cancelled = 0 AND a.against_voucher_id IS NOT ?""",
            (a["order_voucher_id"], item, against_voucher_id)).fetchone()[0]
        if taken + qty > ordered + 1e-9:
            raise ValueError(f"Only {ordered - taken:g} of '{item}' is pending on that order")
        cleaned.append({"order_voucher_id": a["order_voucher_id"], "stock_item_name": item, "qty": qty})
    return cleaned


def record_allocations(db, against_voucher_id, allocations):
    """Allocate quantities of the voucher ``against_voucher_id`` to order lines (replacing its earlier allocations).
    Each allocation: {order_voucher_id, stock_item_name, qty}."""
    ensure_tables(db)
    against = db.conn.execute("SELECT voucher_type FROM voucher WHERE id = ?", (against_voucher_id,)).fetchone()
    if not against:
        raise ValueError("Voucher not found")
    lines = {r[0]: r[1] for r in db.conn.execute(
        "SELECT stock_item_name, SUM(qty) FROM voucher_inventory WHERE voucher_id = ? GROUP BY stock_item_name", (against_voucher_id,))}
    cleaned = check_allocations(db, against[0], lines, allocations, against_voucher_id)
    touched = set(notes_touched_by(db, against_voucher_id))
    db.conn.execute("DELETE FROM order_allocation WHERE against_voucher_id = ?", (against_voucher_id,))
    for a in cleaned:
        db.conn.execute("INSERT INTO order_allocation(order_voucher_id, stock_item_name, against_voucher_id, qty) VALUES(?,?,?,?)",
                        (a["order_voucher_id"], a["stock_item_name"], against_voucher_id, a["qty"]))
    touched |= set(notes_touched_by(db, against_voucher_id))
    for note in touched:
        sync_note(db, note)
    db.conn.commit()
