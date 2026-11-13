"""What an invoice still lacks before an e-invoice (IRN) or an e-way bill can be prepared from it.

Nothing is invented: the check only lists the fields that are empty on the voucher, the party and company masters, and the items, so
a person can fill them in. Generating the JSON itself stays with ``generate_einvoice_json`` / ``generate_eway_bill_json``.
"""
import re

from engine.invoice_print import build_print

TAX_INVOICES = ("Sales", "Credit Note", "Debit Note")
_VEHICLE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}$|^[A-Z]{3}\d{4}$|^TM\d{6}[A-Z]$")


def clean_vehicle_no(value):
    """Upper-case a vehicle number without spaces or hyphens; an obviously malformed one is refused."""
    text = re.sub(r"[\s-]", "", str(value or "")).upper()
    if text and not _VEHICLE.match(text):
        raise ValueError(f"'{value}' does not look like a vehicle number (for example TN01AB1234)")
    return text


def readiness(db, voucher_id):
    doc = build_print(db, voucher_id)
    row = db.conn.execute("SELECT vehicle_no, transporter_id, distance_km, voucher_type FROM voucher WHERE id = ?", (voucher_id,)).fetchone()
    if doc["voucher_type"] not in TAX_INVOICES:
        return {"voucher_id": voucher_id, "applicable": False, "reason": f"A {doc['voucher_type']} is not a tax invoice, so no e-invoice or e-way bill is made from it."}
    einv = []
    if not doc["company"]["gstin"]:
        einv.append("seller GSTIN (company)")
    if not doc["company"]["address"] or not doc["company"]["pincode"]:
        einv.append("seller address and pincode (company)")
    if not doc["party"]["gstin"]:
        einv.append("buyer GSTIN (party)")
    if not doc["party"]["address"]:
        einv.append("buyer address (party)")
    if not (doc["place_of_supply"] or doc["party"]["state"]):
        einv.append("place of supply")
    no_hsn = [i["name"] for i in doc["items"] if not str(i["hsn"]).isdigit()]
    if no_hsn:
        einv.append("HSN / SAC code for: " + ", ".join(no_hsn[:5]) + (" and more" if len(no_hsn) > 5 else ""))
    eway = list(einv)
    if not (row and row["vehicle_no"]):
        eway.append("vehicle number")
    if not (row and (row["distance_km"] or 0) > 0):
        eway.append("transport distance (km)")
    return {"voucher_id": voucher_id, "applicable": True,
            "einvoice": {"ready": not einv, "missing": einv},
            "eway": {"ready": not eway, "missing": eway, "transporter_id": (row["transporter_id"] if row else "") or ""}}
