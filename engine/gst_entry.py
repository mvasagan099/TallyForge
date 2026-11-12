"""GST on a voucher being entered: which tax ledgers to post to, and how much, like TallyPrime's invoice.

* Intra-state supply (party in the company's state): CGST and SGST, each half the rate. Inter-state: IGST at the full rate.
* Tax ledgers are found by name the way TallyPrime users keep them - one ledger per tax and rate ('Cgst 9%', 'Sgst 9%',
  'Igst 18%') - and a missing ledger is reported, never invented.
* Each tax amount is the taxable value x rate, rounded to the paisa (half up).
* Side: output tax is credited on Sales and Debit Notes, input tax debited on Purchases and Credit Notes.
"""
import re
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
TAX_NAME = re.compile(r"\b(cgst|sgst|utgst|igst)\b\D{0,12}?(\d+(?:\.\d+)?)\s*%", re.I)
CREDIT_TAX = ("Sales", "Debit Note")
SUPPORTED = ("Sales", "Purchase", "Credit Note", "Debit Note")


def _money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _rate_text(rate):
    rate = Decimal(str(rate)).normalize()
    return format(rate, "f")


def _tax_ledgers(db):
    """{(kind, rate text): ledger name} for rate-specific tax ledgers, plus {kind: name} for rate-less ones."""
    rated, plain = {}, {}
    for row in db.conn.execute("SELECT name FROM ledger"):
        name = row[0]
        match = TAX_NAME.search(name)
        if match:
            rated[(match.group(1).lower(), _rate_text(match.group(2)))] = name
        else:
            for kind in ("cgst", "sgst", "utgst", "igst"):
                if re.search(rf"\b{kind}\b", name, re.I):
                    plain.setdefault(kind, name)
    return rated, plain


def suggest_gst(db, voucher_type, party_ledger=None, items=None, taxable=None, place_of_supply=None):
    """Tax lines for a voucher. ``items`` = [{'stock_item_name', 'amount'}] (rate from the item master) or
    ``taxable`` = {rate: taxable value} for vouchers without items."""
    if voucher_type not in SUPPORTED:
        raise ValueError(f"GST is computed for {', '.join(SUPPORTED)} vouchers")
    company = db.get_company()
    company_state = (company.get("state") or "").strip().lower()
    state = (place_of_supply or "").strip()
    if not state and party_ledger:
        row = db.conn.execute("SELECT state FROM ledger WHERE name = ?", (party_ledger,)).fetchone()
        state = (row[0] or "").strip() if row else ""
    # Without a known state the supply is taken as intra-state, and the answer says so.
    inter = bool(state and company_state and state.lower() != company_state)
    by_rate = {}
    if taxable:
        for rate, amount in taxable.items():
            by_rate[Decimal(str(rate))] = by_rate.get(Decimal(str(rate)), Decimal("0")) + _money(amount)
    for item in items or []:
        row = db.conn.execute("SELECT gst_rate FROM stock_item WHERE name = ?", (item.get("stock_item_name"),)).fetchone()
        if row is None:
            raise ValueError(f"Stock item '{item.get('stock_item_name')}' does not exist")
        rate = Decimal(str(row[0] or 0))
        by_rate[rate] = by_rate.get(rate, Decimal("0")) + _money(item.get("amount", 0))
    rated, plain = _tax_ledgers(db)
    side = "Cr" if voucher_type in CREDIT_TAX else "Dr"
    lines, warnings = [], []
    base = sum(by_rate.values(), Decimal("0.00"))
    for rate in sorted(by_rate):
        value = by_rate[rate]
        if rate <= 0 or value == 0:
            continue
        parts = (("igst", rate),) if inter else (("cgst", rate / 2), ("sgst", rate / 2))
        for kind, part in parts:
            amount = _money(value * part / 100)
            ledger = rated.get((kind, _rate_text(part))) or plain.get(kind)
            line = {"kind": kind.upper(), "rate": float(part), "taxable_value": float(value), "amount": float(amount),
                    "ledger_name": ledger, "entry_type": side}
            if ledger is None:
                line["missing_ledger"] = f"{kind.capitalize()} {_rate_text(part)}%"
                warnings.append(f"No ledger found for {kind.upper()} at {_rate_text(part)}%: create '{line['missing_ledger']}' under Duties & Taxes")
            lines.append(line)
    tax = sum((Decimal(str(l["amount"])) for l in lines), Decimal("0.00"))
    total = base + tax
    if not state:
        warnings.append("The party's state is not known: treated as an intra-state supply (CGST + SGST)")
    return {"voucher_type": voucher_type, "supply": "Inter-state (IGST)" if inter else "Intra-state (CGST + SGST)",
            "company_state": company.get("state") or "", "party_state": state, "taxable_value": float(base), "tax_lines": lines,
            "total_tax": float(tax), "invoice_total": float(total), "round_off": float((total.quantize(Decimal("1"), rounding=ROUND_HALF_UP) - total)),
            "party_entry_type": "Cr" if side == "Dr" else "Dr", "warnings": warnings}
