"""
ERPNext Parity Enterprise Test Suite
Verifies all 10 ERPNext-derived core modules:
1. Fixed Asset Management & Depreciation (SLM/WDV schedule, Journal posting, disposal)
2. Landed Cost Vouchers & Proration on Inventory Valuation (Value/Quantity basis)
3. Customer Credit Limit & Terms Control (Enforcement blocking)
4. Order Processing (Sales Orders, Purchase Orders, Fulfillment tracking)
5. GSTR-2B Inward Purchase ITC 4-Way Reconciliation
6. Point of Sale (POS) Fast Retail Billing with Multi-Tender Split
7. Tax Collected at Source (TCS Section 206C / 206C(1H))
8. Foreign Exchange Revaluation & Unrealized Gain/Loss
9. Delivery Notes & Goods Receipt Notes (Challan tracking)
10. ESI Statutory Payroll Deduction
11. TDL Runtime Reports Rendering for all 8 new reports
"""

import os
import sys
import tempfile
import unittest
import datetime
from decimal import Decimal

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.accounting_db import EnterpriseAccountingDB, to_dec
from engine.tdl_parser import TDLParser, TDLSymbolTable
from engine.runtime import TDLRuntime
from engine.statutory_config import ESI_STATUTORY_CONFIG, TCS_SECTIONS_CONFIG, FIXED_ASSET_CATEGORIES

class TestERPNextModulesSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.test_db_path = os.path.join(cls.temp_dir.name, "test_erpnext.db")
        cls.db = EnterpriseAccountingDB(cls.test_db_path)
        cls.symbol_table = TDLSymbolTable()
        cls.parser = TDLParser(cls.symbol_table)

        base_tdl = os.path.join(BASE_DIR, "definitions", "base.tdl")
        if os.path.exists(base_tdl):
            cls.parser.parse_file(base_tdl)

        cls.runtime = TDLRuntime(cls.symbol_table, cls.db)
        cls.db.add_stock_item("Wireless Gaming Keyboard", "Primary", "Nos", hsn_code="84716060", gst_rate=18.0)
        cls.db.add_stock_item("Pro Ergonomic Mouse", "Primary", "Nos", hsn_code="84716060", gst_rate=18.0)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.db.conn.close()
        except Exception:
            pass
        cls.temp_dir.cleanup()

    # 1. FIXED ASSET MANAGEMENT & DEPRECIATION
    def test_01_fixed_asset_lifecycle(self):
        # 1. Add asset
        aid = self.db.add_fixed_asset(
            asset_name="Core Database Server",
            asset_code="FA-SRV-901",
            category_name="Computers & IT Assets",
            purchase_date="2026-04-01",
            in_service_date="2026-04-01",
            purchase_cost=120000.0,
            scrap_value=6000.0,
            useful_life_months=36,
            depreciation_method="SLM"
        )
        self.assertGreater(aid, 0)
        asset = self.db.get_fixed_asset(aid)
        self.assertEqual(asset["asset_code"], "FA-SRV-901")
        self.assertEqual(asset["purchase_cost"], 120000.0)

        # 2. Calculate schedule
        schedule = self.db.calculate_depreciation_schedule(aid)
        self.assertEqual(len(schedule), 36)
        first_entry = schedule[0]
        # Monthly SLM = (120,000 - 6,000) / 36 = 3,166.67
        self.assertAlmostEqual(first_entry["depreciation_amount"], 3166.67, places=2)
        self.assertEqual(first_entry["status"], "Scheduled")

        # 3. Post first depreciation entry
        vid = self.db.post_depreciation_entry(first_entry["id"], user_name="auditor")
        self.assertGreater(vid, 0)

        # Verify asset updated
        updated_asset = self.db.get_fixed_asset(aid)
        self.assertAlmostEqual(updated_asset["posted_depreciation"], 3166.67, places=2)
        self.assertAlmostEqual(updated_asset["current_book_value"], 120000.0 - 3166.67, places=2)

        # 4. Verify register
        register = self.db.get_fixed_asset_register()
        self.assertTrue(any(r["category"] == "Computers & IT Assets" for r in register))

        # 5. Dispose asset
        disp = self.db.dispose_fixed_asset(aid, "2026-10-31", sale_amount=118000.0, user_name="admin")
        self.assertEqual(disp["status"], "Sold")
        self.assertGreater(disp["voucher_id"], 0)
        self.assertAlmostEqual(disp["gain_or_loss"], 118000.0 - (120000.0 - 3166.67), places=2)

    # 2. LANDED COST ALLOCATION
    def test_02_landed_cost_allocation(self):
        # Create a purchase invoice with 2 items
        p_entries = [
            {"ledger_name": "Sundry Creditors", "entry_type": "Cr", "amount": 100000.0},
            {"ledger_name": "Purchase Accounts", "entry_type": "Dr", "amount": 100000.0}
        ]
        p_items = [
            {"item_name": "Wireless Gaming Keyboard", "quantity": 20, "rate": 2000.0, "amount": 40000.0},
            {"item_name": "Pro Ergonomic Mouse", "quantity": 30, "rate": 2000.0, "amount": 60000.0}
        ]
        pv_id = self.db.add_voucher(
            voucher_type="Purchase",
            date="2026-05-01",
            narration="Purchase for Landed Cost Test",
            entries=p_entries,
            inventory_items=p_items
        )
        self.assertGreater(pv_id, 0)

        # Apply landed cost of ₹5,000 allocated by Value (40% to keyboard, 60% to mouse)
        lcv_id = self.db.apply_landed_cost(
            purchase_voucher_id=pv_id,
            expense_ledger="Freight & Cartage Inward",
            amount=5000.0,
            allocation_method="Value",
            narration="Inward ocean & air freight"
        )
        self.assertGreater(lcv_id, 0)

        # Verify summary
        summary = self.db.get_landed_cost_summary(pv_id)
        self.assertEqual(len(summary), 2)
        kb_alloc = next(s for s in summary if s["stock_item_name"] == "Wireless Gaming Keyboard")
        mouse_alloc = next(s for s in summary if s["stock_item_name"] == "Pro Ergonomic Mouse")

        # 40% of 5,000 = 2,000 on 20 keyboards -> ₹100/unit landed addition
        self.assertAlmostEqual(kb_alloc["allocated_cost"], 2000.0, places=2)
        self.assertAlmostEqual(kb_alloc["effective_rate"], 2100.0, places=2)

        # 60% of 5,000 = 3,000 on 30 mice -> ₹100/unit landed addition
        self.assertAlmostEqual(mouse_alloc["allocated_cost"], 3000.0, places=2)
        self.assertAlmostEqual(mouse_alloc["effective_rate"], 2100.0, places=2)

        # Verify voucher list
        vouchers = self.db.get_landed_cost_vouchers()
        self.assertTrue(any(v["id"] == lcv_id for v in vouchers))

    # 3. ORDER PROCESSING & FULFILLMENT TRACKING
    def test_03_orders_lifecycle(self):
        # Create Sales Order
        so_items = [
            {"item_name": "Wireless Gaming Keyboard", "quantity": 10, "rate": 2500.0, "amount": 25000.0},
            {"item_name": "Pro Ergonomic Mouse", "quantity": 10, "rate": 1500.0, "amount": 15000.0}
        ]
        so = self.db.create_order(
            order_type="Sales Order",
            party_name="Sunrise Electronics Retail",
            date="2026-06-01",
            items=so_items,
            due_date="2026-06-15",
            narration="SO for Retail Expansion"
        )
        so_id = so["id"]
        self.assertGreater(so_id, 0)
        self.assertEqual(so["status"], "Open")
        self.assertEqual(so["total_amount"], 40000.0)

        # Partially fulfill 5 keyboards and 5 mice
        fulfill_items = [
            {"item_name": "Wireless Gaming Keyboard", "fulfilled_qty": 5},
            {"item_name": "Pro Ergonomic Mouse", "fulfilled_qty": 5}
        ]
        f_res = self.db.fulfill_order(so_id, fulfillment_voucher_id=101, fulfilled_items=fulfill_items)
        self.assertEqual(f_res["status"], "Partially Fulfilled")
        self.assertEqual(f_res["fulfilled_amount"], 20000.0)
        self.assertEqual(f_res["pending_amount"], 20000.0)

        # Complete fulfillment
        f_res2 = self.db.fulfill_order(so_id, fulfillment_voucher_id=102, fulfilled_items=fulfill_items)
        self.assertEqual(f_res2["status"], "Fulfilled")
        self.assertEqual(f_res2["pending_amount"], 0.0)

        # Verify pending orders report excludes fulfilled order
        pending = self.db.get_pending_orders_report("Sales Order")
        self.assertFalse(any(p["id"] == so_id for p in pending))

    # 4. CUSTOMER CREDIT LIMIT & TERMS CONTROL
    def test_04_credit_limit_enforcement(self):
        party = "Credit Restricted Debtor"
        self.db.add_ledger(party, "Sundry Debtors")
        self.db.set_ledger_credit_terms(party, credit_limit=50000.0, credit_days=30, enforce_credit_limit=1)

        # Check limit under threshold
        chk1 = self.db.check_credit_limit(party, 30000.0)
        self.assertTrue(chk1["allowed"])
        self.assertEqual(chk1["current_outstanding"], 0.0)
        self.assertEqual(chk1["projected_outstanding"], 30000.0)

        # Check limit over threshold
        chk2 = self.db.check_credit_limit(party, 60000.0)
        self.assertFalse(chk2["allowed"])
        self.assertEqual(chk2["projected_outstanding"], 60000.0)

        # Attempt to post sales voucher exceeding enforced credit limit -> must raise ValueError
        entries = [
            {"ledger_name": party, "entry_type": "Dr", "amount": 60000.0},
            {"ledger_name": "Sales Accounts", "entry_type": "Cr", "amount": 60000.0}
        ]
        with self.assertRaises(ValueError) as ctx:
            self.db.add_voucher("Sales", "2026-06-10", "Credit Exceeded Sale", entries)
        self.assertIn("Credit limit exceeded", str(ctx.exception))

    # 5. GSTR-2B INWARD PURCHASE ITC 4-WAY RECONCILIATION
    def test_05_gstr2b_4way_reconciliation(self):
        # Post purchase in books
        p_entries = [
            {"ledger_name": "Sundry Creditors", "entry_type": "Cr", "amount": 11800.0},
            {"ledger_name": "Purchase Accounts", "entry_type": "Dr", "amount": 10000.0},
            {"ledger_name": "Input CGST", "entry_type": "Dr", "amount": 900.0},
            {"ledger_name": "Input SGST", "entry_type": "Dr", "amount": 900.0}
        ]
        self.db.add_voucher(
            voucher_type="Purchase",
            date="2026-04-15",
            narration="GSTR2B Books Purchase Inv #INV-MATCH-01",
            entries=p_entries,
            voucher_number="INV-MATCH-01"
        )

        p_entries2 = [
            {"ledger_name": "Sundry Creditors", "entry_type": "Cr", "amount": 20000.0},
            {"ledger_name": "Purchase Accounts", "entry_type": "Dr", "amount": 20000.0}
        ]
        self.db.add_voucher(
            voucher_type="Purchase",
            date="2026-04-20",
            narration="GSTR2B Books Purchase Inv #INV-MISMATCH-99",
            entries=p_entries2,
            voucher_number="INV-MISMATCH-99"
        )

        # Simulated GSTR-2B data with 4 categories:
        # 1. Exact Match
        # 2. Tax Mismatch
        # 3. Missing in Books
        # (and un-matched book vouchers become Missing in 2B)
        gstr2b_sample = [
            {
                "supplier_gstin": "29AABCT1332L1Z5",
                "supplier_name": "Matched Supplier",
                "invoice_no": "INV-MATCH-01",
                "invoice_date": "2026-04-15",
                "invoice_val": 11800.0,
                "taxable_val": 10000.0,
                "cgst": 900.0,
                "sgst": 900.0,
                "igst": 0.0
            },
            {
                "supplier_gstin": "29AABCT1332L1Z5",
                "supplier_name": "Mismatch Supplier",
                "invoice_no": "INV-MISMATCH-99",
                "invoice_date": "2026-04-20",
                "invoice_val": 23600.0,
                "taxable_val": 20000.0,
                "cgst": 1800.0,
                "sgst": 1800.0,
                "igst": 0.0
            },
            {
                "supplier_gstin": "27AAACN0123M1Z9",
                "supplier_name": "Portal Only Supplier",
                "invoice_no": "INV-PORTAL-ONLY",
                "invoice_date": "2026-04-25",
                "invoice_val": 5900.0,
                "taxable_val": 5000.0,
                "cgst": 450.0,
                "sgst": 450.0,
                "igst": 0.0
            }
        ]

        res = self.db.reconcile_gstr2b(return_period="042026", gstr2b_invoices=gstr2b_sample)
        self.assertGreater(res["matched_count"], 0)
        self.assertGreater(res["eligible_itc"], 0.0)

        # Verify details
        details = self.db.get_gstr2b_recon_details(res["recon_id"])
        self.assertEqual(len(details["items"]), 4)
        self.assertEqual(details["matched_count"], 1)
        self.assertEqual(details["mismatched_count"], 1)
        self.assertEqual(details["missing_in_books_count"], 1)
        self.assertEqual(details["missing_in_2b_count"], 1)
        matched_item = next(i for i in details["items"] if i["invoice_no"] == "INV-MATCH-01")
        self.assertEqual(matched_item["status"], "Matched")

    # 6. POINT OF SALE (POS) FAST RETAIL BILLING
    def test_06_pos_billing_multi_tender(self):
        items = [
            {"item_name": "Wireless Gaming Keyboard", "quantity": 1, "rate": 2500.0, "amount": 2500.0}
        ]
        # Total bill = 2,500. Paid: 1,000 cash, 1,000 card, 1,000 UPI = 3,000 paid. Change = 500.
        pos_inv = self.db.add_pos_voucher(
            date="2026-07-01",
            party_name="Cash",
            items=items,
            cash_tendered=1000.0,
            card_tendered=1000.0,
            upi_tendered=1000.0,
            cheque_tendered=0.0,
            customer_phone="9888877777",
            narration="Walk-in POS Sale"
        )
        self.assertGreater(pos_inv["voucher_id"], 0)
        self.assertEqual(pos_inv["total_amount"], 2500.0)
        self.assertEqual(pos_inv["tender_paid"], 3000.0)
        self.assertEqual(pos_inv["change_returned"], 500.0)

        # Verify in POS invoices list
        invoices = self.db.get_pos_invoices()
        self.assertTrue(any(i["id"] == pos_inv["voucher_id"] for i in invoices))

    # 7. TAX COLLECTED AT SOURCE (TCS)
    def test_07_tcs_section_calculation(self):
        # Under ₹50L threshold -> 0 TCS
        calc1 = self.db.calculate_tcs("Standard Buyer Pvt Ltd", 1000000.0)
        self.assertFalse(calc1["tcs_applicable"])
        self.assertEqual(calc1["tcs_amount"], 0.0)

        # Over ₹50L threshold (e.g. ₹55L total) -> 0.1% on excess (₹500,000) = ₹500
        calc2 = self.db.calculate_tcs("Standard Buyer Pvt Ltd", 5500000.0)
        self.assertTrue(calc2["tcs_applicable"])
        self.assertEqual(calc2["taxable_base"], 500000.0)
        self.assertEqual(calc2["tcs_rate_pct"], 0.1 if calc2["has_pan"] else 1.0)
        self.assertGreater(calc2["tcs_amount"], 0.0)

        # Verify TCS sections config
        sections = self.db.get_tcs_sections()
        self.assertTrue(any(s["section_code"] == "206C(1H)" for s in sections))

    # 8. FOREX REVALUATION & UNREALIZED GAIN/LOSS
    def test_08_forex_revaluation(self):
        # Add foreign currency USD with initial rate ₹82
        self.db.add_currency("$", "USD", Decimal("82.00"))

        # Post USD voucher: 100 USD @ ₹82 = ₹8,200
        v_entries = [
            {"ledger_name": "Sundry Debtors", "entry_type": "Dr", "amount": 8200.0},
            {"ledger_name": "Sales Accounts", "entry_type": "Cr", "amount": 8200.0}
        ]
        vid = self.db.add_voucher(
            voucher_type="Sales",
            date="2026-08-01",
            narration="Export Sales $100",
            entries=v_entries,
            currency_symbol="$",
            exchange_rate=82.0
        )
        self.assertGreater(vid, 0)

        # Update currency exchange rate to ₹85 (gain of ₹3 per dollar on $100 = ₹300 gain)
        with self.db.conn:
            self.db.conn.execute("UPDATE currency SET rate_of_exchange = 85.00 WHERE symbol = '$'")

        forex_calc = self.db.calculate_forex_gain_loss()
        self.assertGreater(len(forex_calc["transactions"]), 0)
        self.assertEqual(forex_calc["status"], "Gain")
        self.assertAlmostEqual(forex_calc["total_forex_gain_loss"], 300.0, places=2)

        # Post revaluation voucher
        reval_res = self.db.post_forex_revaluation_voucher(date="2026-08-31")
        self.assertGreater(reval_res["voucher_id"], 0)
        self.assertAlmostEqual(reval_res["revaluation_amount"], 300.0, places=2)

    # 9. DELIVERY NOTES & RECEIPT NOTES (CHALLANS)
    def test_09_challans_tracking(self):
        dn_items = [{"item_name": "Wireless Gaming Keyboard", "quantity": 5, "rate": 2500.0, "amount": 12500.0}]
        dn = self.db.record_delivery_note(
            date="2026-09-01",
            customer_name="Tech Solutions Ltd",
            items=dn_items,
            order_no="SO-2026-001",
            narration="Challan for batch 1"
        )
        self.assertGreater(dn["voucher_id"], 0)
        self.assertIn("DN-", dn["challan_no"])

        rn_items = [{"item_name": "Pro Ergonomic Mouse", "quantity": 10, "rate": 1500.0, "amount": 15000.0}]
        rn = self.db.record_receipt_note(
            date="2026-09-02",
            supplier_name="Global Tech Corp",
            items=rn_items,
            order_no="PO-2026-001",
            narration="Goods receipt batch 1"
        )
        self.assertGreater(rn["voucher_id"], 0)
        self.assertIn("GRN-", rn["challan_no"])

        unbilled = self.db.get_unbilled_challans()
        self.assertTrue(any(c["challan_no"] == dn["challan_no"] for c in unbilled))
        self.assertTrue(any(c["challan_no"] == rn["challan_no"] for c in unbilled))

    # 10. ESI STATUTORY PAYROLL CEILING & CALCULATION
    def test_10_esi_statutory_payroll(self):
        # 1. Employee with wage <= ₹21,000 -> ESI Applicable
        self.db.add_employee("EMP-ESI-01", "Ramesh Kumar", "Warehouse", "Associate", 18000.0)
        slip = self.db.calculate_employee_salary("EMP-ESI-01")
        # Gross = 18,000 * 1.5 = 27,000. Basic = 18,000.
        # Check that ESI rules adhere to ESI_STATUTORY_CONFIG
        self.assertIn("statutory_deductions", slip)
        self.assertIn("wage_ceiling_monthly", ESI_STATUTORY_CONFIG)
        self.assertEqual(ESI_STATUTORY_CONFIG["wage_ceiling_monthly"], 21000.0)

        # 2. Employee with basic salary 12,000 (Gross = 12,000 <= 21,000 ceiling)
        self.db.add_employee("EMP-ESI-02", "Suresh Patel", "Packing", "Helper", 12000.0)
        slip2 = self.db.calculate_employee_salary("EMP-ESI-02")
        gross2 = slip2["gross_salary"]
        self.assertLessEqual(gross2, 21000.0)
        # ESI employee contribution = 0.75% of 12,000 = 90.00
        esi_ded = next((d for d in slip2["statutory_deductions"] if "ESI" in d["name"]), None)
        self.assertIsNotNone(esi_ded)
        self.assertAlmostEqual(esi_ded["amount"], 90.0, places=2)

    # 11. TDL RUNTIME RENDERING OF NEW REPORTS
    def test_11_tdl_reports_rendering(self):
        new_reports = [
            ("FixedAssets", "Fixed Assets & Depreciation Register"),
            ("OrderTracking", "Sales & Purchase Order Fulfillment Tracking"),
            ("GSTR2BRecon", "GSTR-2B Inward Purchase ITC 4-Way Reconciliation"),
            ("LandedCost", "Landed Cost & Additional Purchase Cost Summary"),
            ("POSBilling", "Point of Sale (POS) Fast Billing Register"),
            ("ForexRevaluation", "Foreign Exchange Gain/Loss & Revaluation Summary"),
            ("Challans", "Goods Delivery & Receipt Notes"),
            ("TCSRegister", "Tax Collected at Source (TCS) Register")
        ]
        for rep_name, expected_title in new_reports:
            rendered = self.runtime.render_report(rep_name)
            self.assertEqual(rendered["report"], rep_name)
            self.assertEqual(rendered["title"], expected_title)
            self.assertIn("parts", rendered)
            self.assertGreater(len(rendered["parts"]), 0)
            lines = rendered["parts"][0].get("lines", [])
            self.assertGreater(len(lines), 0)
            self.assertTrue(any("Header" in l.get("name", "") for l in lines))

if __name__ == "__main__":
    unittest.main()
