"""
Test Suite: Statutory Compliance & Taxation Verification
Covers:
- Versioned statutory configuration (GST slabs, state codes, TDS sections)
- MCA Audit Trail legal basis & citation verification (Rule 3(1))
- GSTR-1 Outward Supplies JSON schema compliance
- GSTR-3B Monthly Return calculation
- GSTR-9 Annual Return table aggregation
- Offline NIC v1.1 e-Invoice JSON payload generation
- Offline NIC e-Way Bill JSON payload generation
- Section 194J TDS deduction and TDS Register audit
"""

import unittest
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.accounting_db import EnterpriseAccountingDB
from engine.statutory_config import (
    STATUTORY_CONFIG_VERSION,
    GST_STATE_CODES,
    GST_TAX_SLABS,
    TDS_SECTIONS_CONFIG,
    STATUTORY_LEGAL_BASIS
)

class TestStatutoryCompliance(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_statutory.db")
        self.db = EnterpriseAccountingDB(self.db_path)
        # NIC exports now require explicit party details instead of fabricated fallbacks.
        with self.db.conn:
            self.db.conn.execute("UPDATE ledger SET address='Buyer test address',pincode='560001' WHERE name='Acme Technologies (Debtor)'")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_01_statutory_versioned_config(self):
        """Verifies statutory config version, GST state codes (37 states/UTs), and tax slabs."""
        self.assertEqual(STATUTORY_CONFIG_VERSION, "2026.1")
        self.assertGreaterEqual(len(GST_STATE_CODES), 37)
        self.assertEqual(GST_STATE_CODES["29"], "Karnataka")
        self.assertEqual(GST_STATE_CODES["27"], "Maharashtra")
        self.assertEqual(GST_STATE_CODES["07"], "Delhi")

        # Standard GST tax slabs (0%, 5%, 12%, 18%, 28%)
        rates = [s["rate"] for s in GST_TAX_SLABS]
        self.assertIn(18.0, rates)
        self.assertIn(5.0, rates)
        self.assertIn(12.0, rates)
        self.assertIn(28.0, rates)

        # Standard TDS sections (194C, 194J, 194I, 194Q)
        self.assertIn("194C", TDS_SECTIONS_CONFIG)
        self.assertIn("194J", TDS_SECTIONS_CONFIG)
        self.assertIn("194I", TDS_SECTIONS_CONFIG)
        self.assertIn("194Q", TDS_SECTIONS_CONFIG)

    def test_02_mca_audit_trail_legal_basis(self):
        """Verifies statutory legal citations for MCA audit trail and e-invoicing."""
        self.assertIn("MCA_EDIT_LOG", STATUTORY_LEGAL_BASIS)
        mca = STATUTORY_LEGAL_BASIS["MCA_EDIT_LOG"]
        self.assertEqual(mca["law"], "Companies Act, 2013")
        self.assertIn("Rule 3(1) of Companies (Accounts) Rules, 2014", mca["rule"])

        self.assertIn("E_INVOICE", STATUTORY_LEGAL_BASIS)
        einv_law = STATUTORY_LEGAL_BASIS["E_INVOICE"]
        self.assertIn("Rule 48(4) of CGST Rules, 2017", einv_law["law"])
        self.assertEqual(einv_law["schema_version"], "Version 1.1")

        self.assertIn("E_WAY_BILL", STATUTORY_LEGAL_BASIS)
        eway_law = STATUTORY_LEGAL_BASIS["E_WAY_BILL"]
        self.assertIn("Rule 138 of CGST Rules, 2017", eway_law["law"])

    def test_03_gstr1_outward_supplies_json(self):
        """Verifies GSTR-1 outward supplies return schema."""
        # Record a B2B sales invoice
        self.db.add_voucher(
            "Sales", "2026-05-10", "Taxable B2B Sale",
            [
                {"ledger_name": "Acme Technologies (Debtor)", "entry_type": "Dr", "amount": 118000.0},
                {"ledger_name": "Sales A/c", "entry_type": "Cr", "amount": 100000.0},
                {"ledger_name": "CGST", "entry_type": "Cr", "amount": 9000.0},
                {"ledger_name": "SGST", "entry_type": "Cr", "amount": 9000.0}
            ],
            inventory_items=[
                {"stock_item_name": "HP Laser Printer 1020", "qty": 8.0, "rate": 12500.0, "amount": 100000.0, "direction": "Out"}
            ]
        )

        gstr1 = self.db.generate_gstr1_json()
        self.assertEqual(gstr1["gstin"], "29AABCT1332L1Z5")
        self.assertIn("b2b", gstr1)
        self.assertIn("hsn", gstr1)
        self.assertIn("doc_issue", gstr1)
        self.assertGreaterEqual(len(gstr1["b2b"]), 1)

    def test_04_gstr3b_monthly_summary(self):
        """Verifies monthly GSTR-3B tax liability and ITC summary."""
        # 1. Outward Sale: 10,000 + 900 CGST + 900 SGST
        self.db.add_voucher(
            "Sales", "2026-06-05", "Monthly Outward",
            [
                {"ledger_name": "Acme Technologies (Debtor)", "entry_type": "Dr", "amount": 11800.0},
                {"ledger_name": "Sales A/c", "entry_type": "Cr", "amount": 10000.0},
                {"ledger_name": "CGST", "entry_type": "Cr", "amount": 900.0},
                {"ledger_name": "SGST", "entry_type": "Cr", "amount": 900.0}
            ]
        )

        # 2. Inward Purchase: 5,000 + 450 CGST Input + 450 SGST Input
        self.db.add_voucher(
            "Purchase", "2026-06-08", "Monthly Inward ITC",
            [
                {"ledger_name": "Purchase A/c", "entry_type": "Dr", "amount": 5000.0},
                {"ledger_name": "CGST", "entry_type": "Dr", "amount": 450.0},
                {"ledger_name": "SGST", "entry_type": "Dr", "amount": 450.0},
                {"ledger_name": "Apex Systems Ltd (Creditor)", "entry_type": "Cr", "amount": 5900.0}
            ]
        )

        summary = self.db.get_gst_summary()
        self.assertGreaterEqual(summary["taxable_turnover"], 10000.0)
        self.assertEqual(summary["cgst_liability"], 450.0)
        self.assertEqual(summary["sgst_liability"], 450.0)

    def test_05_gstr9_annual_return_aggregation(self):
        """Verifies annual GSTR-9 aggregation of Table 4, Table 6, and Table 9."""
        gstr9 = self.db.get_gstr9_summary("2026-27")
        self.assertEqual(gstr9["financial_year"], "2026-27")
        self.assertIn("table_4_outward_supplies", gstr9)
        self.assertIn("table_6_itc_availed", gstr9)
        self.assertIn("table_9_tax_paid_and_payable", gstr9)

    def test_06_nic_einvoice_json_schema_v1_1(self):
        """Verifies offline generation of NIC Schema v1.1 compliant e-Invoice JSON."""
        vid = self.db.add_voucher(
            "Sales", "2026-07-15", "e-Invoice Sale",
            [
                {"ledger_name": "Acme Technologies (Debtor)", "entry_type": "Dr", "amount": 59000.0},
                {"ledger_name": "Sales A/c", "entry_type": "Cr", "amount": 50000.0},
                {"ledger_name": "CGST", "entry_type": "Cr", "amount": 4500.0},
                {"ledger_name": "SGST", "entry_type": "Cr", "amount": 4500.0}
            ],
            inventory_items=[
                {"stock_item_name": 'Dell 24" FHD Monitor', "qty": 5.0, "rate": 10000.0, "amount": 50000.0, "direction": "Out"}
            ]
        )

        einv = self.db.generate_einvoice_json(vid)
        self.assertEqual(einv["Version"], "1.1")
        self.assertIn("TranDtls", einv)
        self.assertIn("DocDtls", einv)
        self.assertIn("SellerDtls", einv)
        self.assertIn("BuyerDtls", einv)
        self.assertIn("ItemList", einv)
        self.assertIn("ValDtls", einv)

        self.assertEqual(einv["DocDtls"]["Typ"], "INV")
        self.assertEqual(einv["ValDtls"]["TotInvVal"], 59000.0)
        self.assertEqual(einv["ItemList"][0]["HsnCd"], "85285200")

    def test_07_nic_eway_bill_json_payload(self):
        """Verifies offline preparation of e-Way Bill JSON payload."""
        vid = self.db.add_voucher(
            "Sales", "2026-08-20", "Goods in transit sale",
            [
                {"ledger_name": "Acme Technologies (Debtor)", "entry_type": "Dr", "amount": 59000.0},
                {"ledger_name": "Sales A/c", "entry_type": "Cr", "amount": 50000.0},
                {"ledger_name": "CGST", "entry_type": "Cr", "amount": 4500.0},
                {"ledger_name": "SGST", "entry_type": "Cr", "amount": 4500.0}
            ],
            inventory_items=[
                {"stock_item_name": 'Dell 24" FHD Monitor', "qty": 5.0, "rate": 10000.0, "amount": 50000.0, "direction": "Out"}
            ]
        )

        with self.db.conn:
            self.db.conn.execute("UPDATE voucher SET vehicle_no='KA01AB1234',distance_km=50 WHERE id=?", (vid,))
        eway = self.db.generate_eway_bill_json(vid)
        self.assertEqual(eway["supplyType"], "O")
        self.assertEqual(eway["subSupplyType"], "1")
        self.assertEqual(eway["docType"], "INV")
        self.assertEqual(eway["totalValue"], 59000.0)
        self.assertEqual(eway["transDistance"], 50.0)
        self.assertIn("KA01AB1234", eway["vehicleNo"])

    def test_08_tds_sections_and_withholding_register(self):
        """Verifies TDS Section 194J deduction and TDS withholding register."""
        # Post Professional Fees ₹50,000, TDS 10% under Sec 194J (₹5,000), Net payable ₹45,000
        vid = self.db.add_voucher(
            "Journal", "2026-09-01", "Legal & Professional Consultancy",
            [
                {"ledger_name": "Office Rent", "entry_type": "Dr", "amount": 50000.0}, # Expense
                {"ledger_name": "TDS Payable", "entry_type": "Cr", "amount": 5000.0},
                {"ledger_name": "Apex Systems Ltd (Creditor)", "entry_type": "Cr", "amount": 45000.0}
            ]
        )
        self.assertGreater(vid, 0)

        tds_reg = self.db.get_tds_register()
        self.assertGreaterEqual(len(tds_reg), 1)
        entry = next((e for e in tds_reg if e["voucher_id"] == vid), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["tds_amount"], 5000.0)
        self.assertEqual(entry["tds_ledger"], "TDS Payable")

if __name__ == "__main__":
    unittest.main()
