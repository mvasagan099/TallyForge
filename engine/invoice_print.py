"""Printable voucher / tax invoice laid out from stored data, like TallyPrime's invoice print.

Everything printed comes from the voucher and its masters. Nothing is invented: a field Tally did not
supply (HSN, bank details, company address lines) is simply left out of the document.
"""
import json
from decimal import Decimal

INVOICE_TYPES = ("Sales", "Purchase", "Credit Note", "Debit Note")
INVOICE_TITLES = {"Sales": "TAX INVOICE", "Purchase": "PURCHASE INVOICE", "Credit Note": "CREDIT NOTE", "Debit Note": "DEBIT NOTE"}
PARTY_GROUPS = ("Sundry Debtors", "Sundry Creditors")
ITEM_LEDGER_GROUPS = ("Sales Accounts", "Purchase Accounts")

_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
         "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_thousand(n):
    words = []
    if n >= 100:
        words.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))
    elif n:
        words.append(_ONES[n])
    return " ".join(words)


def amount_in_words(amount, currency="Indian Rupee", minor="Paise"):
    """Amount in words with Indian grouping (thousand, lakh, crore): 1,05,000.50 -> One Lakh Five Thousand and Fifty Paise Only."""
    value = Decimal(str(amount)).copy_abs().quantize(Decimal("0.01"))
    whole, paise = int(value), int((value - int(value)) * 100)
    if whole == 0 and paise == 0:
        return f"{currency} Zero Only"
    parts = []
    for size, label in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand")):
        if whole >= size:
            parts.append(_below_thousand(whole // size) + " " + label)
            whole %= size
    if whole:
        parts.append(_below_thousand(whole))
    text = f"{currency} {' '.join(parts)}".strip() if parts else ""
    if paise:
        text = (text + " and " if parts else f"{currency} ") + f"{_below_thousand(paise)} {minor}"
    return text + " Only"


def _json(text):
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {}


def build_print(db, voucher_id):
    voucher = db.get_voucher(voucher_id)
    if voucher is None:
        raise ValueError("Voucher not found")
    conn = db.conn
    try:
        row = conn.execute("SELECT details_json FROM voucher_invoice WHERE voucher_id=?", (voucher_id,)).fetchone()
        details = _json(row[0]) if row else {}
    except Exception:                                   # a company that was never imported has no such table
        details = {}
    groups = {r["name"]: dict(r) for r in conn.execute("SELECT name, parent FROM account_group")}
    ledgers = {r["name"]: dict(r) for r in conn.execute(
        "SELECT name, group_name, gstin, state, address, pincode, pan, udf_data FROM ledger")}

    def under(name, targets):
        return db._under_group(groups, ledgers.get(name, {}).get("group_name", ""), targets)

    entries = voucher["entries"]
    party_entry = next((e for e in entries if under(e["ledger_name"], PARTY_GROUPS)), None)
    party_name = party_entry["ledger_name"] if party_entry else None
    party = ledgers.get(party_name, {})
    party_extra = _json(party.get("udf_data"))
    company = db.get_company()

    is_invoice = voucher["voucher_type"] in INVOICE_TYPES and bool(voucher["inventory"])
    stock = {r["name"]: dict(r) for r in conn.execute("SELECT name, hsn_code, uom_symbol FROM stock_item")}
    items = []
    for i, line in enumerate(voucher["inventory"], 1):
        info = stock.get(line["stock_item_name"], {})
        items.append({"no": i, "name": line["stock_item_name"], "hsn": info.get("hsn_code") or "",
                      "qty": line["qty"], "unit": info.get("uom_symbol") or "", "rate": line["rate"], "amount": line["amount"],
                      "godown": line.get("godown_name") or ""})

    # Tax / charge ledgers: everything that is neither the party nor the sales / purchase ledger carrying the items.
    charges = []
    for e in entries:
        if e is party_entry or under(e["ledger_name"], PARTY_GROUPS) or (is_invoice and under(e["ledger_name"], ITEM_LEDGER_GROUPS)):
            continue
        charges.append({"name": e["ledger_name"], "amount": e["amount"], "type": e["entry_type"]})

    total = party_entry["amount"] if party_entry else voucher["total_amount"]
    seller_state = details.get("company_state") or company.get("state") or ""
    doc = {
        "kind": "invoice" if is_invoice else "voucher",
        "title": INVOICE_TITLES.get(voucher["voucher_type"], "TAX INVOICE") if is_invoice else voucher["voucher_type"].upper() + " VOUCHER",
        "voucher_id": voucher_id, "voucher_type": voucher["voucher_type"], "number": voucher["voucher_number"], "date": voucher["date"],
        "cancelled": bool(voucher["is_cancelled"]), "optional": bool(voucher["is_optional"]),
        "company": {"name": company.get("name", ""), "gstin": details.get("company_gstin") or company.get("gstin") or "",
                    "state": seller_state, "address": company.get("address") or "", "pincode": company.get("pincode") or ""},
        "party": {"name": details.get("party_mailing_name") or party_extra.get("mailing") or party_name or "",
                  "ledger": party_name or "", "address": party.get("address") or "", "state": details.get("state") or party.get("state") or "",
                  "gstin": details.get("gstin") or party.get("gstin") or "", "pan": party.get("pan") or "",
                  "country": party_extra.get("country") or ""},
        "consignee": {"name": details.get("consignee_name") or "", "gstin": details.get("consignee_gstin") or "",
                      "state": details.get("consignee_state") or ""},
        "place_of_supply": details.get("place_of_supply") or "",
        "order_ref": details.get("order_ref") or voucher.get("order_no") or "", "reference": voucher.get("reference_no") or "",
        "shipped_by": details.get("shipped_by") or "", "destination": details.get("destination") or "",
        "vehicle_no": voucher.get("vehicle_no") or "",
        "items": items, "charges": charges, "items_total": round(sum(i["amount"] for i in items), 2),
        "total": round(total, 2), "amount_in_words": amount_in_words(total),
        "bills": [{"name": b["ref_name"], "type": b["ref_type"], "amount": b["amount"]} for b in voucher["bills"]],
        "narration": voucher["narration"] or "",
        "entries": [{"ledger": e["ledger_name"], "type": e["entry_type"], "amount": e["amount"]} for e in entries],
    }
    return doc
