"""
Statutory & Legal Compliance Validation Suite (Phase 5.6)
Verifies official GSTN GSTR-1 JSON schema, NIC e-Invoice v1.1 JSON format,
NIC e-Way Bill JSON format, GSTR-9 Annual Return table aggregation,
TDS Section 194J register, and MCA Rule 3(1) legal citations.
"""

import unittest
import os
import tempfile
from engine.accounting_db import EnterpriseAccountingDB
from engine.statutory_config import (
    GST_TAX_SLABS, GST_STATE_CODES, TDS_SECTIONS_CONFIG,
    STATUTORY_LEGAL_BASIS, STATUTORY_CONFIG_VERSION
)


class TestStatutoryValidation(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix="_statutory.db")
        os.close(self.temp_db_fd)
        self.db = EnterpriseAccountingDB(self.temp_db_path)

        # Seed sample sales invoice with inventory for e-invoice / e-way bill
        self.sales_vch_id = self.db.add_voucher(
            voucher_type="Sales",
            date="2026-06-15",
            narration="B2B E-Invoice Supply",
            entries=[
                {"ledger_name": "Customer ABC Pvt Ltd", "entry_type": "Dr", "amount": 23600.0},
                {"ledger_name": "Sales", "entry_type": "Cr", "amount": 20000.0},
                {"ledger_name": "CGST", "entry_type": "Cr", "amount": 1800.0},
                {"ledger_name": "SGST", "entry_type": "Cr", "amount": 1800.0}
            ],
            inventory_items=[{
                "stock_item_name": "Dell 24\" FHD Monitor",
                "godown_name": "Main Location",
                "qty": 2.0,
                "rate": 10000.0,
                "amount": 20000.0,
                "direction": "Out"
            }]
        )

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    # 1. GSTR-1 JSON Offline Schema
    def test_01_gstr1_json_schema(self):
        gstr1 = self.db.generate_gstr1_json()
        self.assertIn("gstin", gstr1)
        self.assertIn("fp", gstr1)
        self.assertIn("b2b", gstr1)
        self.assertIn("hsn", gstr1)
        self.assertIn("doc_issue", gstr1)
        self.assertIsInstance(gstr1["b2b"], list)
        self.assertIsInstance(gstr1["hsn"]["data"], list)
        self.assertIsInstance(gstr1["doc_issue"]["doc_det"], list)

    # 2. NIC e-Invoice v1.1 JSON Schema
    def test_02_einvoice_nic_v1_1_json(self):
        einv = self.db.generate_einvoice_json(self.sales_vch_id)
        self.assertEqual(einv["Version"], "1.1")
        self.assertEqual(einv["TranDtls"]["TaxSch"], "GST")
        self.assertEqual(einv["TranDtls"]["SupTyp"], "B2B")
        self.assertEqual(einv["DocDtls"]["Typ"], "INV")
        self.assertIn("SellerDtls", einv)
        self.assertIn("BuyerDtls", einv)
        self.assertIn("ItemList", einv)
        self.assertIn("ValDtls", einv)
        self.assertAlmostEqual(einv["ValDtls"]["TotInvVal"], 23600.0, places=2)

    # 3. NIC e-Way Bill JSON Format
    def test_03_eway_bill_nic_json(self):
        eway = self.db.generate_eway_bill_json(self.sales_vch_id)
        self.assertEqual(eway["supplyType"], "O")
        self.assertEqual(eway["docType"], "INV")
        self.assertIn("fromGstin", eway)
        self.assertIn("toGstin", eway)
        self.assertIn("transDistance", eway)
        self.assertIn("vehicleNo", eway)
        self.assertAlmostEqual(eway["totalValue"], 23600.0, places=2)

    # 4. GSTR-9 Annual Return Aggregation
    def test_04_gstr9_annual_return_aggregation(self):
        gstr9 = self.db.get_gstr9_summary("2026-27")
        self.assertEqual(gstr9["financial_year"], "2026-27")
        self.assertIn("table_4_outward_supplies", gstr9)
        self.assertIn("table_6_itc_availed", gstr9)
        self.assertIn("table_9_tax_paid_and_payable", gstr9)
        self.assertGreaterEqual(gstr9["table_4_outward_supplies"]["b2b_taxable_turnover"], 20000.0)

    # 5. TDS Section 194J Register & Deductions
    def test_05_tds_section_194j_register(self):
        tds_items = self.db.get_tds_register()
        self.assertIsInstance(tds_items, list)
        self.assertIn("194J", TDS_SECTIONS_CONFIG)
        self.assertEqual(TDS_SECTIONS_CONFIG["194J"]["rate_individual"], 10.0)
        self.assertEqual(TDS_SECTIONS_CONFIG["194J"]["rate_other"], 10.0)

    # 6. Statutory Legal Basis Citations
    def test_06_statutory_legal_basis_citations(self):
        self.assertIn("MCA_EDIT_LOG", STATUTORY_LEGAL_BASIS)
        self.assertIn("Rule 3(1)", STATUTORY_LEGAL_BASIS["MCA_EDIT_LOG"]["rule"])
        self.assertIn("GST_INVOICE_ITC", STATUTORY_LEGAL_BASIS)
        self.assertIn("Section 16", STATUTORY_LEGAL_BASIS["GST_INVOICE_ITC"]["sections"])
        self.assertIn("E_INVOICE", STATUTORY_LEGAL_BASIS)


if __name__ == "__main__":
    unittest.main()
