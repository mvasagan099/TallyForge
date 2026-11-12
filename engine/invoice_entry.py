"""Sales and purchase invoices (and credit / debit notes) built the way TallyPrime's invoice entry builds them.

``build_invoice`` turns an invoice (party, item lines, extra charges, optional GST) into the voucher's postings, stock lines and
bill reference without saving anything; ``save_invoice`` saves exactly what was previewed. Checked on a real company by
rebuilding every real sales and purchase invoice from its party and items and comparing the postings (see the tests and the
status notes).

Sides: a Sales invoice debits the party and credits the sales ledger, charges and tax; a Purchase invoice is the mirror image;
a Credit Note (sales return) mirrors a Sales invoice and a Debit Note (purchase return) mirrors a Purchase invoice.
"""
import datetime
import json
from decimal import Decimal, ROUND_HALF_UP

from engine import orders
from engine.gst_entry import suggest_gst

CENT = Decimal("0.01")
ORDER_TYPES = orders.ORDER_TYPES
SUPPORTED = ("Sales", "Purchase", "Credit Note", "Debit Note") + ORDER_TYPES
ITEM_SIDE = {"Sales": "Cr", "Debit Note": "Cr", "Sales Order": "Cr", "Purchase": "Dr", "Credit Note": "Dr", "Purchase Order": "Dr"}


def _money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _qty(value):
    return Decimal(str(value))


def _flip(side):
    return "Dr" if side == "Cr" else "Cr"


def build_invoice(db, data):
    vtype = data.get("voucher_type")
    if vtype not in SUPPORTED:
        raise ValueError(f"Invoices are entered for {', '.join(SUPPORTED)}")
    date = data.get("date") or datetime.date.today().isoformat()
    if not db._valid_iso_date(date):
        raise ValueError(f"Invalid voucher date '{date}': use YYYY-MM-DD")
    party = data.get("party_ledger")
    book = data.get("sales_ledger") or data.get("purchase_ledger") or data.get("book_ledger")
    if not party or not db.conn.execute("SELECT 1 FROM ledger WHERE name = ?", (party,)).fetchone():
        raise ValueError(f"Party ledger '{party}' does not exist")
    if not book or not db.conn.execute("SELECT 1 FROM ledger WHERE name = ?", (book,)).fetchone():
        raise ValueError(f"{'Sales' if ITEM_SIDE[vtype] == 'Cr' else 'Purchase'} ledger '{book}' does not exist")
    raw_items = data.get("items") or []
    if not raw_items:
        raise ValueError("An invoice needs at least one item line")
    item_side = ITEM_SIDE[vtype]
    party_side = _flip(item_side)
    out_types = db.STOCK_OUT_TYPES
    direction = "Out" if vtype in out_types else "In"
    is_order = vtype in ORDER_TYPES
    if is_order and (data.get("charges") or data.get("gst") or data.get("round_off")):
        raise ValueError("An order has item lines only: no charges, GST or round off")

    items, item_total = [], Decimal("0.00")
    for n, line in enumerate(raw_items, 1):
        name = line.get("stock_item_name")
        row = db.conn.execute("SELECT uom_symbol FROM stock_item WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"Stock item '{name}' does not exist")
        qty = _qty(line.get("qty", 0))
        if qty < 0:
            raise ValueError(f"Quantity of '{name}' cannot be negative")
        rate = _money(line.get("rate", 0))
        discount = Decimal(str(line.get("discount_percent", 0) or 0))
        if not (Decimal("0") <= discount <= Decimal("100")):
            raise ValueError(f"Discount of '{name}' must be between 0 and 100 percent")
        gross = qty * rate
        amount = _money(line["amount"]) if line.get("amount") not in (None, "") else _money(gross * (Decimal("100") - discount) / Decimal("100"))
        if line.get("amount") not in (None, "") and qty > 0 and rate and discount == 0 \
                and abs(amount - gross) > max(Decimal("0.05"), qty * Decimal("0.005") + Decimal("0.01")):
            raise ValueError(f"Amount {amount} of '{name}' does not match quantity x rate ({gross:.2f})")
        item_total += amount
        due = line.get("due_date") or None
        if due and not db._valid_iso_date(due):
            raise ValueError(f"Invalid due date '{due}' for '{name}': use YYYY-MM-DD")
        items.append({"no": n, "due_date": due or "", "order_voucher_id": line.get("order_voucher_id"), "stock_item_name": name, "unit": row[0] or "", "qty": float(qty), "rate": float(rate),
                      "discount_percent": float(discount), "amount": float(amount), "godown_name": line.get("godown_name") or "Main Location",
                      "batch_number": line.get("batch_number") or "", "direction": direction})

    charges, charge_total = [], Decimal("0.00")
    for c in data.get("charges") or []:
        amount = _money(c.get("amount", 0))
        if c.get("percent") not in (None, ""):
            amount = _money(item_total * Decimal(str(c["percent"])) / Decimal("100"))
        if amount <= 0:
            continue
        if not db.conn.execute("SELECT 1 FROM ledger WHERE name = ?", (c.get("ledger_name"),)).fetchone():
            raise ValueError(f"Ledger '{c.get('ledger_name')}' does not exist")
        groups_c = {r["name"]: dict(r) for r in db.conn.execute("SELECT name, parent FROM account_group")}
        group_c = db.conn.execute("SELECT group_name FROM ledger WHERE name = ?", (c["ledger_name"],)).fetchone()[0]
        if db._under_group(groups_c, group_c, ("Sales Accounts", "Purchase Accounts")):
            raise ValueError(f"'{c['ledger_name']}' is a sales/purchase ledger: post freight, discount and other charges to an income or expense ledger")
        side = c.get("entry_type") or item_side                    # freight and the like follow the invoice; a discount ledger flips it
        if side not in ("Dr", "Cr"):
            raise ValueError("A charge is a debit (Dr) or credit (Cr)")
        charges.append({"ledger_name": c["ledger_name"], "entry_type": side, "amount": float(amount)})
        charge_total += amount if side == item_side else -amount

    warnings, tax_lines, tax_total = [], [], Decimal("0.00")
    gst = data.get("gst")
    if gst:
        suggestion = suggest_gst(db, vtype, party, [{"stock_item_name": i["stock_item_name"], "amount": i["amount"]} for i in items],
                                 place_of_supply=data.get("place_of_supply"))
        missing = [l["missing_ledger"] for l in suggestion["tax_lines"] if l.get("missing_ledger")]
        if missing:
            raise ValueError("Create the tax ledger(s) first: " + ", ".join(sorted(set(missing))))
        tax_lines = [{"ledger_name": l["ledger_name"], "entry_type": item_side, "amount": l["amount"], "kind": l["kind"], "rate": l["rate"]}
                     for l in suggestion["tax_lines"]]
        tax_total = sum((_money(l["amount"]) for l in tax_lines), Decimal("0.00"))
        warnings.extend(w for w in suggestion["warnings"] if "state is not known" in w)

    total = item_total + charge_total + tax_total
    round_off = Decimal("0.00")
    if data.get("round_off"):
        ledger = data.get("round_off_ledger") or next((r[0] for r in db.conn.execute("SELECT name FROM ledger") if "round" in r[0].lower()), None)
        if not ledger:
            raise ValueError("Create a 'Round Off' ledger first")
        round_off = total.quantize(Decimal("1"), rounding=ROUND_HALF_UP) - total
        if round_off != 0:
            charges.append({"ledger_name": ledger, "entry_type": item_side if round_off > 0 else _flip(item_side), "amount": float(abs(round_off))})
            total += round_off
    if total <= 0:
        raise ValueError("The invoice total must be positive")

    entries = [{"ledger_name": party, "entry_type": party_side, "amount": float(total)},
               {"ledger_name": book, "entry_type": item_side, "amount": float(item_total)}]
    entries += [{"ledger_name": c["ledger_name"], "entry_type": c["entry_type"], "amount": c["amount"]} for c in charges]
    entries += [{"ledger_name": t["ledger_name"], "entry_type": t["entry_type"], "amount": t["amount"]} for t in tax_lines]

    # Bill reference: a purchase is known by the supplier's invoice number, a sale by our voucher number; the due date is the
    # given one or the party's credit period counted from the invoice date.
    number = data.get("voucher_number") or db.preview_voucher_number(vtype, date)
    ref = (data.get("reference_no") if vtype in ("Purchase", "Debit Note") and data.get("reference_no") else None) or number
    due = data.get("due_date")
    if not due:
        days = db.conn.execute("SELECT credit_period_days FROM ledger WHERE name = ?", (party,)).fetchone()
        days = int(days[0] or 0) if days else 0
        if days:
            due = (datetime.date.fromisoformat(date) + datetime.timedelta(days=days)).isoformat()
    bills = []
    maintains = None if is_order else db.conn.execute("SELECT maintain_bill_by_bill, group_name FROM ledger WHERE name = ?", (party,)).fetchone()
    groups = {r["name"]: dict(r) for r in db.conn.execute("SELECT name, parent FROM account_group")}
    # Only a debtor or creditor keeps bills (a cash party does not), and only when its ledger is set to maintain them.
    if maintains and maintains[0] and db._under_group(groups, maintains[1], ("Sundry Debtors", "Sundry Creditors")):
        bills.append({"ledger_name": party, "ref_type": "New Ref", "ref_name": ref, "amount": float(total), "direction": party_side,
                      **({"due_date": due} if due else {})})
    allocations = []
    if not is_order:
        wanted = [{"order_voucher_id": i["order_voucher_id"], "stock_item_name": i["stock_item_name"], "qty": i["qty"]} for i in items if i["order_voucher_id"]]
        if wanted:
            lines = {}
            for i in items:
                lines[i["stock_item_name"]] = lines.get(i["stock_item_name"], 0.0) + i["qty"]
            allocations = orders.check_allocations(db, vtype, lines, wanted)
    return {"voucher_type": vtype, "date": date, "voucher_number": number, "allocations": allocations, "party_ledger": party, "book_ledger": book,
            "items": items, "item_total": float(item_total), "charges": charges, "tax_lines": tax_lines, "total_tax": float(tax_total),
            "round_off": float(round_off), "total": float(total), "entries": entries, "bill_allocations": bills, "reference_no": data.get("reference_no") or "",
            "warnings": warnings}


def save_invoice(db, data):
    built = build_invoice(db, data)
    # A discounted line is stored at its effective rate (net amount / quantity), so quantity x rate equals the amount posted.
    is_order = built["voucher_type"] in ORDER_TYPES
    inventory = []
    for i in built["items"]:
        line = {"stock_item_name": i["stock_item_name"], "qty": i["qty"],
                "rate": round(i["amount"] / i["qty"], 4) if i["discount_percent"] and i["qty"] else i["rate"], "amount": i["amount"],
                "godown_name": i["godown_name"], "batch_number": i["batch_number"]}
        if not is_order:                      # an order moves no stock, so its lines carry no direction
            line["direction"] = i["direction"]
        inventory.append(line)
    vid = db.add_voucher(built["voucher_type"], built["date"], data.get("narration", ""), built["entries"], inventory_items=inventory,
                         bill_allocations=built["bill_allocations"] or None, voucher_number=built["voucher_number"], is_optional=is_order)
    if is_order:
        orders.set_due_dates(db, vid, {i["stock_item_name"]: i["due_date"] for i in built["items"] if i["due_date"]})
        if not data.get("reference_no"):      # the order number defaults to the voucher number
            db.conn.execute("UPDATE voucher SET reference_no = ? WHERE id = ?", (built["voucher_number"], vid))
    elif built["allocations"]:
        orders.record_allocations(db, vid, built["allocations"])
    ref = data.get("reference_no")
    if ref or data.get("vehicle_no"):
        db.conn.execute("UPDATE voucher SET reference_no = COALESCE(NULLIF(?, ''), reference_no), vehicle_no = COALESCE(NULLIF(?, ''), vehicle_no) WHERE id = ?",
                        (ref or "", data.get("vehicle_no") or "", vid))
    details = {k: data[k] for k in ("place_of_supply", "order_ref", "shipped_by", "destination", "consignee_name", "consignee_gstin", "consignee_state")
               if data.get(k)}
    party = db.conn.execute("SELECT gstin, state FROM ledger WHERE name = ?", (built["party_ledger"],)).fetchone()
    if party and party[0]:
        details.setdefault("gstin", party[0])
    if party and party[1]:
        details.setdefault("state", party[1])
    company = db.get_company()
    if company.get("gstin"):
        details["company_gstin"] = company["gstin"]
    if company.get("state"):
        details["company_state"] = company["state"]
    if details:
        db.conn.execute("CREATE TABLE IF NOT EXISTS voucher_invoice (voucher_id INTEGER PRIMARY KEY REFERENCES voucher(id) ON DELETE CASCADE, details_json TEXT NOT NULL)")
        db.conn.execute("INSERT OR REPLACE INTO voucher_invoice(voucher_id, details_json) VALUES(?,?)",
                        (vid, json.dumps(details, ensure_ascii=False, sort_keys=True)))
    db.conn.commit()
    return {"voucher_id": vid, **built}
