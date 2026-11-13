"""Price lists like TallyPrime's price levels: a named list holds, per stock item, rates that take effect from a date, optionally
by quantity slab (from quantity up to, but not including, a limit), with an optional discount percent. A party ledger can be
assigned a list; an invoice line left without a rate is priced from the party's list (or the list chosen on the invoice) using
the latest rate in force on the invoice date for the quantity sold.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_list (
    name TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS price_list_rate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    price_list TEXT NOT NULL REFERENCES price_list(name) ON DELETE CASCADE,
    stock_item_name TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    qty_from REAL NOT NULL DEFAULT 0,
    qty_to REAL,
    rate REAL NOT NULL,
    discount_percent REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger_price_list (
    ledger_name TEXT PRIMARY KEY,
    price_list TEXT NOT NULL REFERENCES price_list(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_price_list_rate ON price_list_rate(price_list, stock_item_name, effective_from)
"""


def ensure_tables(db):
    # plain statements: executescript would commit an open transaction
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.conn.execute(statement)


def exists(db, name):
    ensure_tables(db)
    return db.conn.execute("SELECT 1 FROM price_list WHERE name = ?", (name,)).fetchone() is not None


def create(db, name):
    name = (name or "").strip()
    if not name:
        raise ValueError("A price list needs a name")
    ensure_tables(db)
    db.conn.execute("INSERT OR IGNORE INTO price_list(name) VALUES(?)", (name,))
    db.conn.commit()


def set_rates(db, name, rows):
    """Set rates on a list. Each row: {stock_item_name, effective_from, rate, qty_from (default 0), qty_to (optional),
    discount_percent (optional)}. A row with the same item, date and slab start replaces the earlier one."""
    ensure_tables(db)
    if not exists(db, name):
        raise ValueError(f"Price list '{name}' does not exist")
    clean = []
    for r in rows or []:
        item = r.get("stock_item_name")
        if not db.conn.execute("SELECT 1 FROM stock_item WHERE name = ?", (item,)).fetchone():
            raise ValueError(f"Stock item '{item}' does not exist")
        when = r.get("effective_from")
        if not db._valid_iso_date(when or ""):
            raise ValueError(f"Invalid effective date '{when}': use YYYY-MM-DD")
        rate = float(r.get("rate") or 0)
        if rate < 0:
            raise ValueError(f"Rate of '{item}' cannot be negative")
        lo = float(r.get("qty_from") or 0)
        hi = r.get("qty_to")
        hi = float(hi) if hi not in (None, "") else None
        if lo < 0 or (hi is not None and hi <= lo):
            raise ValueError(f"The quantity slab of '{item}' must run from a quantity up to a larger one")
        disc = float(r.get("discount_percent") or 0)
        if not 0 <= disc <= 100:
            raise ValueError(f"Discount of '{item}' must be between 0 and 100 percent")
        clean.append((item, when, lo, hi, rate, disc))
    for item, when, lo, hi, rate, disc in clean:
        db.conn.execute("DELETE FROM price_list_rate WHERE price_list = ? AND stock_item_name = ? AND effective_from = ? AND qty_from = ?",
                        (name, item, when, lo))
        db.conn.execute("INSERT INTO price_list_rate(price_list, stock_item_name, effective_from, qty_from, qty_to, rate, discount_percent) VALUES(?,?,?,?,?,?,?)",
                        (name, item, when, lo, hi, rate, disc))
    db.conn.commit()
    return len(clean)


def rate_for(db, name, item, date, qty):
    """The rate (and discount) in force for ``item`` on ``date`` at ``qty``: the latest effective date not after ``date`` among the
    slabs that contain the quantity, taking the highest slab start when several match. None when the list has no such price."""
    ensure_tables(db)
    row = db.conn.execute(
        """SELECT rate, discount_percent, effective_from FROM price_list_rate
           WHERE price_list = ? AND stock_item_name = ? AND effective_from <= ? AND qty_from <= ? AND (qty_to IS NULL OR ? < qty_to)
           ORDER BY effective_from DESC, qty_from DESC, id DESC LIMIT 1""", (name, item, date, qty, qty)).fetchone()
    return {"rate": row[0], "discount_percent": row[1], "effective_from": row[2]} if row else None


def assign(db, ledger_name, name):
    """Give a party ledger a price list (an empty name clears it)."""
    ensure_tables(db)
    if not db.conn.execute("SELECT 1 FROM ledger WHERE name = ?", (ledger_name,)).fetchone():
        raise ValueError(f"Ledger '{ledger_name}' does not exist")
    if not name:
        db.conn.execute("DELETE FROM ledger_price_list WHERE ledger_name = ?", (ledger_name,))
    else:
        if not exists(db, name):
            raise ValueError(f"Price list '{name}' does not exist")
        db.conn.execute("INSERT OR REPLACE INTO ledger_price_list(ledger_name, price_list) VALUES(?,?)", (ledger_name, name))
    db.conn.commit()


def party_price_list(db, ledger_name):
    ensure_tables(db)
    row = db.conn.execute("SELECT price_list FROM ledger_price_list WHERE ledger_name = ?", (ledger_name,)).fetchone()
    return row[0] if row else None


def summary(db):
    """Every list with its rates and the parties assigned to it."""
    ensure_tables(db)
    out = []
    for (name,) in db.conn.execute("SELECT name FROM price_list ORDER BY name").fetchall():
        rates = [dict(r) for r in db.conn.execute(
            "SELECT stock_item_name, effective_from, qty_from, qty_to, rate, discount_percent FROM price_list_rate WHERE price_list = ? ORDER BY stock_item_name, effective_from, qty_from", (name,))]
        parties = [r[0] for r in db.conn.execute("SELECT ledger_name FROM ledger_price_list WHERE price_list = ? ORDER BY ledger_name", (name,))]
        out.append({"name": name, "rates": rates, "parties": parties})
    return out
