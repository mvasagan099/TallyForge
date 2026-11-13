"""GST summary for a period, laid out like the outward / inward tables of GSTR-3B and the rate-wise tables of GSTR-1.

Everything comes from the posted tax ledgers (ledgers under Duties & Taxes whose name carries CGST, SGST/UTGST, IGST or CESS and
a rate, such as "Cgst 9%"), the way a company that books GST on its invoices has it. Sales and credit notes feed the outward
table (a credit note reduces it), purchases and debit notes the inward table. Tax ledger postings on any other voucher type are
listed apart so that the figures always close against the ledgers.

Limits, stated on the report itself: eligibility of input credit, reverse charge, adjustments, interest, HSN-wise and invoice-wise
return tables are not worked out, and item GST rates / HSN codes are not available from TallyPrime, so a voucher's rate is the
rate in the name of the tax ledgers posted on it.
"""
import json
import re
from collections import defaultdict

HEADS = ("cgst", "sgst", "igst", "cess")
OUTWARD = {"Sales": 1, "POS": 1, "Credit Note": -1}
INWARD = {"Purchase": 1, "Debit Note": -1}
_RATE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _head(name):
    low = name.lower()
    for key, head in (("cgst", "cgst"), ("sgst", "sgst"), ("utgst", "sgst"), ("igst", "igst"), ("cess", "cess")):
        if key in low:
            return head
    return None


def _rate(name):
    m = _RATE.search(name)
    return float(m.group(1)) if m else 0.0


def _blank():
    return {"taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "cess": 0.0, "vouchers": 0}


def gst_summary(db, from_date, to_date):
    for d in (from_date, to_date):
        if not db._valid_iso_date(d or ""):
            raise ValueError("Give the period as YYYY-MM-DD dates")
    groups = {r["name"]: dict(r) for r in db.conn.execute("SELECT name, parent FROM account_group")}
    ledgers = {r["name"]: dict(r) for r in db.conn.execute("SELECT name, group_name, gstin FROM ledger")}
    tax_ledgers = {n: (_head(n), _rate(n)) for n, l in ledgers.items()
                   if _head(n) and db._under_group(groups, l["group_name"], ("Duties & Taxes",))}
    outward, inward = defaultdict(_blank), defaultdict(_blank)
    other = {h: 0.0 for h in HEADS}
    other_vouchers = []
    b2b, b2c = _blank(), _blank()
    warnings = []
    ledger_net = {h: 0.0 for h in HEADS}              # (credit - debit) on every tax ledger in the period, for the closing check
    if not tax_ledgers:
        return {"from": from_date, "to": to_date, "outward": [], "inward": [], "tax_ledgers": [], "warnings": ["No GST ledgers (under Duties & Taxes, named CGST / SGST / IGST with a rate) were found."]}

    for v in db.conn.execute("SELECT id, voucher_type, voucher_number, date FROM voucher WHERE date >= ? AND date <= ? AND is_cancelled = 0 AND is_optional = 0 ORDER BY date, id", (from_date, to_date)):
        entries = db.conn.execute("SELECT ledger_name, entry_type, amount FROM voucher_entry WHERE voucher_id = ?", (v["id"],)).fetchall()
        taxed = [e for e in entries if e["ledger_name"] in tax_ledgers]
        if not taxed:
            continue
        for e in taxed:
            ledger_net[tax_ledgers[e["ledger_name"]][0]] += e["amount"] if e["entry_type"] == "Cr" else -e["amount"]
        vtype = v["voucher_type"]
        if vtype in OUTWARD:
            table, side = outward, "Cr"      # a credit note posts the other way, so it comes out negative by itself
        elif vtype in INWARD:
            table, side = inward, "Dr"
        else:
            for e in taxed:
                other[tax_ledgers[e["ledger_name"]][0]] += e["amount"] if e["entry_type"] == "Cr" else -e["amount"]
            other_vouchers.append({"voucher_id": v["id"], "type": vtype, "number": v["voucher_number"], "date": v["date"]})
            continue
        # tax per rate group (a group is the set of tax ledgers carrying the same rate)
        per_rate = defaultdict(lambda: {h: 0.0 for h in HEADS})
        for e in taxed:
            head, rate = tax_ledgers[e["ledger_name"]]
            per_rate[rate][head] += (e["amount"] if e["entry_type"] == side else -e["amount"])
        # taxable value: what the voucher books on the supply side apart from the party, the tax and round off (income, sales,
        # purchase or expense ledgers alike: a service is not necessarily under Sales Accounts)
        items = sum((e["amount"] if e["entry_type"] == side else -e["amount"]) for e in entries
                    if e["ledger_name"] not in tax_ledgers and "round" not in e["ledger_name"].lower()
                    and not db._under_group(groups, ledgers.get(e["ledger_name"], {}).get("group_name", ""),
                                            ("Sundry Debtors", "Sundry Creditors", "Cash-in-Hand", "Bank Accounts", "Bank OD A/c")))
        mixed = len(per_rate) > 1
        if mixed:
            warnings.append(f"{vtype} {v['voucher_number']} ({v['date']}) carries more than one rate: its taxable value is worked back from the tax.")
        voucher_taxable = 0.0
        for rate, amounts in per_rate.items():
            combined = rate * 2 if (amounts["cgst"] or amounts["sgst"]) else rate
            tax_sum = sum(amounts.values()) if rate else 0.0
            taxable = (tax_sum / (combined / 100.0)) if (mixed and combined) else items
            key = combined
            row = table[key]
            row["taxable"] += taxable
            row["vouchers"] += 1
            for h in HEADS:
                row[h] += amounts[h]
            voucher_taxable += taxable
        # buyer / supplier registration (outward only): the party's GSTIN on the voucher or the ledger
        if table is outward:
            party = next((e["ledger_name"] for e in entries if e["ledger_name"] not in tax_ledgers and
                          db._under_group(groups, ledgers.get(e["ledger_name"], {}).get("group_name", ""), ("Sundry Debtors",))), None)
            try:
                row = db.conn.execute("SELECT details_json FROM voucher_invoice WHERE voucher_id = ?", (v["id"],)).fetchone()
                gstin = (json.loads(row[0]).get("gstin") if row else None) or (ledgers.get(party, {}).get("gstin") if party else None)
            except Exception:
                gstin = ledgers.get(party, {}).get("gstin") if party else None
            bucket = b2b if gstin else b2c
            bucket["taxable"] += voucher_taxable
            bucket["vouchers"] += 1
            for h in HEADS:
                bucket[h] += sum(a[h] for a in per_rate.values())

    def finish(table):
        rows = []
        for rate in sorted(table):
            r = table[rate]
            rows.append({"rate": rate, **{k: round(r[k], 2) for k in ("taxable", *HEADS)}, "vouchers": r["vouchers"]})
        return rows

    def total(rows):
        return {k: round(sum(r[k] for r in rows), 2) for k in ("taxable", *HEADS)}

    out_rows, in_rows = finish(outward), finish(inward)
    out_total, in_total = total(out_rows), total(in_rows)
    for label, t in (("outward", out_total), ("inward", in_total)):
        if abs(t["cgst"] - t["sgst"]) >= 1:
            warnings.append(f"Outward and inward CGST and SGST should match: the {label} totals differ by {abs(t['cgst'] - t['sgst']):,.2f}; look for vouchers that post only one of them.")
    payable = {h: round(out_total[h] - in_total[h], 2) for h in HEADS}
    check = {h: round(ledger_net[h] - (out_total[h] - in_total[h] + other[h]), 2) for h in HEADS}
    return {"from": from_date, "to": to_date,
            "outward": out_rows, "outward_total": out_total, "inward": in_rows, "inward_total": in_total,
            "outward_registered": {k: round(v, 2) if isinstance(v, float) else v for k, v in b2b.items()},
            "outward_unregistered": {k: round(v, 2) if isinstance(v, float) else v for k, v in b2c.items()},
            "net_tax": payable, "other_postings": {h: round(v, 2) for h, v in other.items()}, "other_vouchers": other_vouchers,
            "ledger_check": check, "closes": all(abs(v) < 0.01 for v in check.values()),
            "tax_ledgers": sorted(tax_ledgers), "warnings": warnings,
            "note": "Posted-books summary, not a return: input credit eligibility, reverse charge, adjustments, interest and the invoice / HSN tables are not worked out, "
                    "and a voucher's rate is read from the names of the tax ledgers posted on it."}
