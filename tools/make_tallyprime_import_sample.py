"""Write a small synthetic company as Tally XML so you can try importing it into a *new, empty test company* in TallyPrime.

    python tools/make_tallyprime_import_sample.py [output.xml]

Nothing here touches TallyPrime and nothing comes from your real books: the company, ledgers and vouchers are made up.
After the import, compare the figures printed below with TallyPrime's Trial Balance (see docs/TALLYPRIME_IMPORT_TEST.md).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.invoice_entry import save_invoice  # noqa: E402
from engine.tally_xml_export import export_company_xml  # noqa: E402


def main(target):
    with tempfile.TemporaryDirectory() as tmp:
        db = EnterpriseAccountingDB(str(Path(tmp) / "sample.db"), seed_demo=False)
        db.select_company(db.create_company(name="TallyForge Import Sample", state="Tamil Nadu", state_code="33"))
        for name, group in (("Sales", "Sales Accounts"), ("Purchases", "Purchase Accounts"), ("Sample Buyer", "Sundry Debtors"),
                            ("Sample Supplier", "Sundry Creditors"), ("Cash", "Cash-in-Hand"), ("Sample Bank", "Bank Accounts"),
                            ("Cgst 9%", "Duties & Taxes"), ("Sgst 9%", "Duties & Taxes"), ("Rent", "Indirect Expenses")):
            db.add_ledger(name, group)
        db.conn.execute("UPDATE ledger SET state = 'Tamil Nadu' WHERE name = 'Sample Buyer'")      # same state: CGST + SGST
        db.add_stock_item("Sample Rice", uom_symbol="Kg", gst_rate=18)
        db.set_negative_stock_policy(True)
        save_invoice(db, {"voucher_type": "Purchase", "date": "2026-05-02", "party_ledger": "Sample Supplier", "purchase_ledger": "Purchases",
                          "items": [{"stock_item_name": "Sample Rice", "qty": 100, "rate": 40}]})
        save_invoice(db, {"voucher_type": "Sales", "date": "2026-05-10", "party_ledger": "Sample Buyer", "sales_ledger": "Sales", "gst": True,
                          "items": [{"stock_item_name": "Sample Rice", "qty": 30, "rate": 60}]})
        db.add_voucher("Receipt", "2026-05-20", "part payment", [{"ledger_name": "Sample Bank", "entry_type": "Dr", "amount": 1000},
                                                                  {"ledger_name": "Sample Buyer", "entry_type": "Cr", "amount": 1000}])
        db.add_voucher("Payment", "2026-05-25", "rent", [{"ledger_name": "Rent", "entry_type": "Dr", "amount": 500},
                                                         {"ledger_name": "Cash", "entry_type": "Cr", "amount": 500}])
        xml = export_company_xml(db)
        Path(target).write_bytes(xml if isinstance(xml, bytes) else xml.encode("utf-8"))
        print(f"Wrote {target}\nFigures to compare in TallyPrime after importing into an empty test company:")
        for row in db.get_trial_balance():
            if row.get("closing_balance"):
                print(f"  {row['name']}: {row['closing_balance']:,.2f} {row['closing_balance_type']}")
        db.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "TallyForge_import_sample.xml")
