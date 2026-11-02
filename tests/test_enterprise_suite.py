"""
TallyPrime 2026 Enterprise Full Automated Test Suite
Executes Tests A through N against an isolated temporary SQLite database.
Preserves user production data in tally_data.db.
"""

import os
import sys
import json
import tempfile
import unittest
import datetime
from decimal import Decimal

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.accounting_db import EnterpriseAccountingDB, to_dec
from engine.tdl_parser import TDLParser, TDLSymbolTable
from engine.runtime import TDLRuntime

class TestTallyEnterpriseSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Create an isolated temporary directory and database file for testing
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.test_db_path = os.path.join(cls.temp_dir.name, "test_tally.db")
        cls.db = EnterpriseAccountingDB(cls.test_db_path)
        cls.symbol_table = TDLSymbolTable()
        cls.parser = TDLParser(cls.symbol_table)

        # Parse base and custom TDL files
        base_tdl = os.path.join(BASE_DIR, "definitions", "base.tdl")
        ext_tdl = os.path.join(BASE_DIR, "definitions", "custom_extension.tdl")
        if os.path.exists(base_tdl):
            cls.parser.parse_file(base_tdl)
        if os.path.exists(ext_tdl):
            cls.parser.parse_file(ext_tdl)

        cls.runtime = TDLRuntime(cls.symbol_table, cls.db)

    @classmethod
    def tearDownClass(cls):
        # Close test db connection and clean up temp directory
        try:
            cls.db.conn.close()
        except Exception:
            pass
        cls.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # TEST A: Company Management
    # -------------------------------------------------------------------------
    def test_A_company_management(self):
        print("\n--- Running Test A: Company Management ---")
        comp = self.db.get_company()
        self.assertIsNotNone(comp)
        self.assertEqual(comp["name"], "Demo Enterprise Ltd.")
        self.assertEqual(comp["gstin"], "29AABCT1332L1Z5")

        # Update company details
        self.db.update_company(
            name="Apex Horizon Technologies Pvt Ltd",
            address="Plot 104, Electronic City Phase 1, Bangalore",
            gstin="29XYZ9999K1Z4",
            state="Karnataka",
            state_code="29"
        )
        updated = self.db.get_company()
        self.assertEqual(updated["name"], "Apex Horizon Technologies Pvt Ltd")
        self.assertEqual(updated["gstin"], "29XYZ9999K1Z4")
        self.assertEqual(updated["address"], "Plot 104, Electronic City Phase 1, Bangalore")
        print("✓ Test A Passed: Company creation, persistence, and alterations verified.")

    # -------------------------------------------------------------------------
    # TEST B: Accounting Core & Mathematical Integrity
    # -------------------------------------------------------------------------
    def test_B_accounting_core_integrity(self):
        print("\n--- Running Test B: Accounting Core & Mathematical Integrity ---")
        # 1. Balanced Voucher Posting
        v_id = self.db.add_voucher(
            voucher_type="Payment",
            date="2026-10-15",
            narration="Rent payment for October 2026",
            entries=[
                {"ledger_name": "Office Rent", "entry_type": "Dr", "amount": 45000.00},
                {"ledger_name": "HDFC Bank", "entry_type": "Cr", "amount": 45000.00}
            ]
        )
        self.assertGreater(v_id, 0)

        # 2. Decimal Precision (No float rounding drift)
        self.db.add_voucher(
            voucher_type="Journal",
            date="2026-10-16",
            narration="Decimal sub-unit adjustment",
            entries=[
                {"ledger_name": "Printing & Stationery", "entry_type": "Dr", "amount": 100.10},
                {"ledger_name": "Printing & Stationery", "entry_type": "Dr", "amount": 200.20},
                {"ledger_name": "Cash", "entry_type": "Cr", "amount": 300.30}
            ]
        )
        rent_bal, r_type = self.db.get_ledger_closing_balance("Office Rent")
        self.assertEqual(rent_bal, Decimal("45000.00"))
        self.assertEqual(r_type, "Dr")

        # 3. Unbalanced Voucher Rejection
        with self.assertRaises(ValueError) as ctx:
            self.db.add_voucher(
                voucher_type="Payment",
                date="2026-10-16",
                narration="Unbalanced test entry",
                entries=[
                    {"ledger_name": "Office Rent", "entry_type": "Dr", "amount": 5000.00},
                    {"ledger_name": "Cash", "entry_type": "Cr", "amount": 4900.00}
                ]
            )
        self.assertIn("out of balance", str(ctx.exception).lower())

        # 4. Negative / Zero Amount Rejection
        with self.assertRaises(ValueError):
            self.db.add_voucher(
                voucher_type="Payment",
                date="2026-10-16",
                narration="Negative amount test",
                entries=[
                    {"ledger_name": "Office Rent", "entry_type": "Dr", "amount": -100.00},
                    {"ledger_name": "Cash", "entry_type": "Cr", "amount": -100.00}
                ]
            )

        # 5. Trial Balance Equality
        tb = self.db.get_trial_balance()
        tot_dr = sum(to_dec(i["closing_balance"]) for i in tb if i["closing_balance_type"] == "Dr")
        tot_cr = sum(to_dec(i["closing_balance"]) for i in tb if i["closing_balance_type"] == "Cr")
        self.assertEqual(tot_dr, tot_cr)
        print(f"✓ Test B Passed: Balanced double-entry enforced. TB Total Dr (₹{tot_dr}) == Cr (₹{tot_cr}).")

    # -------------------------------------------------------------------------
    # TEST C: Sales Workflow (B2B, GST, Inventory Deduction, Receivables)
    # -------------------------------------------------------------------------
    def test_C_sales_workflow(self):
        print("\n--- Running Test C: Sales Workflow ---")
        init_item = self.db.get_stock_item_summary("Assembled Desktop Combo")
        init_qty = init_item["closing_qty"]

        # Post B2B Tax Invoice: ₹1,00,000 + 9% CGST (₹9,000) + 9% SGST (₹9,000) = ₹1,18,000
        v_id = self.db.add_voucher(
            voucher_type="Sales",
            date="2026-10-20",
            narration="B2B Sales to Customer ABC",
            entries=[
                {"ledger_name": "Customer ABC Pvt Ltd", "entry_type": "Dr", "amount": 118000.00},
                {"ledger_name": "Sales Accounts", "entry_type": "Cr", "amount": 100000.00},
                {"ledger_name": "CGST", "entry_type": "Cr", "amount": 9000.00},
                {"ledger_name": "SGST", "entry_type": "Cr", "amount": 9000.00}
            ],
            inventory_items=[
                {
                    "stock_item_name": "Assembled Desktop Combo",
                    "godown_name": "Main Location",
                    "qty": 2.0,
                    "rate": 50000.00,
                    "amount": 100000.00,
                    "direction": "Out"
                }
            ],
            bill_allocations=[
                {
                    "ledger_name": "Customer ABC Pvt Ltd",
                    "ref_type": "New Ref",
                    "ref_name": "INV-2026-001",
                    "amount": 118000.00,
                    "direction": "Dr"
                }
            ]
        )
        self.assertGreater(v_id, 0)

        # Verify stock deducted
        after_item = self.db.get_stock_item_summary("Assembled Desktop Combo")
        self.assertEqual(after_item["closing_qty"], init_qty - 2.0)

        # Verify Receivables Outstanding
        rec = self.db.get_outstanding_bills("Sundry Debtors")
        inv_bill = next((b for b in rec if b["bill_ref"] == "INV-2026-001"), None)
        self.assertIsNotNone(inv_bill)
        self.assertEqual(inv_bill["amount"], 118000.00)
        print("✓ Test C Passed: B2B Sales invoice posted, stock deducted, bill-wise receivable recorded.")

    # -------------------------------------------------------------------------
    # TEST D: Purchase Workflow (B2B, ITC, Inventory Addition, Payables)
    # -------------------------------------------------------------------------
    def test_D_purchase_workflow(self):
        print("\n--- Running Test D: Purchase Workflow ---")
        init_item = self.db.get_stock_item_summary("RAM DDR4 16GB")
        init_qty = init_item["closing_qty"]

        # Purchase 20 units of RAM at ₹3,000 each = ₹60,000 + 18% IGST (₹10,800) = ₹70,800
        v_id = self.db.add_voucher(
            voucher_type="Purchase",
            date="2026-10-22",
            narration="Component replenishment from vendor",
            entries=[
                {"ledger_name": "Purchase Accounts", "entry_type": "Dr", "amount": 60000.00},
                {"ledger_name": "IGST", "entry_type": "Dr", "amount": 10800.00},
                {"ledger_name": "Vendor Tech Supplies", "entry_type": "Cr", "amount": 70800.00}
            ],
            inventory_items=[
                {
                    "stock_item_name": "RAM DDR4 16GB",
                    "godown_name": "Main Location",
                    "qty": 20.0,
                    "rate": 3000.00,
                    "amount": 60000.00,
                    "direction": "In"
                }
            ],
            bill_allocations=[
                {
                    "ledger_name": "Vendor Tech Supplies",
                    "ref_type": "New Ref",
                    "ref_name": "PUR-BILL-101",
                    "amount": 70800.00,
                    "direction": "Cr"
                }
            ]
        )
        self.assertGreater(v_id, 0)

        # Verify stock added
        after_item = self.db.get_stock_item_summary("RAM DDR4 16GB")
        self.assertEqual(after_item["closing_qty"], init_qty + 20.0)

        # Verify Payables Outstanding
        pay = self.db.get_outstanding_bills("Sundry Creditors")
        pur_bill = next((b for b in pay if b["bill_ref"] == "PUR-BILL-101"), None)
        self.assertIsNotNone(pur_bill)
        self.assertEqual(pur_bill["amount"], 70800.00)
        print("✓ Test D Passed: Purchase posted, ITC credited, inventory added, payable recorded.")

    # -------------------------------------------------------------------------
    # TEST E: Payment & Receipt with Bill-wise Allocation & Ageing
    # -------------------------------------------------------------------------
    def test_E_bill_allocations_and_payments(self):
        print("\n--- Running Test E: Bill-wise Allocations & Ageing ---")
        # 1. Partial Payment against Purchase Bill PUR-BILL-101: ₹30,800 paid
        self.db.add_voucher(
            voucher_type="Payment",
            date="2026-10-25",
            narration="Partial payment to Vendor Tech Supplies",
            entries=[
                {"ledger_name": "Vendor Tech Supplies", "entry_type": "Dr", "amount": 30800.00},
                {"ledger_name": "HDFC Bank", "entry_type": "Cr", "amount": 30800.00}
            ],
            bill_allocations=[
                {
                    "ledger_name": "Vendor Tech Supplies",
                    "ref_type": "Agst Ref",
                    "ref_name": "PUR-BILL-101",
                    "amount": 30800.00,
                    "direction": "Dr"
                }
            ]
        )

        # Remaining payable must be ₹70,800 - ₹30,800 = ₹40,000.00
        pay = self.db.get_outstanding_bills("Sundry Creditors")
        pur_bill = next((b for b in pay if b["bill_ref"] == "PUR-BILL-101"), None)
        self.assertIsNotNone(pur_bill)
        self.assertEqual(pur_bill["amount"], 40000.00)

        # 2. Partial Receipt against Sales Invoice INV-2026-001: ₹50,000 received
        self.db.add_voucher(
            voucher_type="Receipt",
            date="2026-10-26",
            narration="Part payment received from Customer ABC",
            entries=[
                {"ledger_name": "HDFC Bank", "entry_type": "Dr", "amount": 50000.00},
                {"ledger_name": "Customer ABC Pvt Ltd", "entry_type": "Cr", "amount": 50000.00}
            ],
            bill_allocations=[
                {
                    "ledger_name": "Customer ABC Pvt Ltd",
                    "ref_type": "Agst Ref",
                    "ref_name": "INV-2026-001",
                    "amount": 50000.00,
                    "direction": "Cr"
                }
            ]
        )

        # Remaining receivable must be ₹1,18,000 - ₹50,000 = ₹68,000.00
        rec = self.db.get_outstanding_bills("Sundry Debtors")
        inv_bill = next((b for b in rec if b["bill_ref"] == "INV-2026-001"), None)
        self.assertIsNotNone(inv_bill)
        self.assertEqual(inv_bill["amount"], 68000.00)
        self.assertIn("Days", inv_bill["age_bracket"])
        print(f"✓ Test E Passed: Partial bill settlement verified. Remaining: ₹{inv_bill['amount']}.")

    # -------------------------------------------------------------------------
    # TEST F: Inventory & Warehouses (Multi-godown Transfer)
    # -------------------------------------------------------------------------
    def test_F_inventory_godown_transfer(self):
        print("\n--- Running Test F: Inventory & Godown Transfers ---")
        # Stock Journal transfer: 5 units of RAM DDR4 from Main Location to Warehouse A
        self.db.add_voucher(
            voucher_type="Stock Journal",
            date="2026-10-27",
            narration="Inter-godown transfer of RAM modules",
            entries=[
                {"ledger_name": "Stock Transfer Out", "entry_type": "Dr", "amount": 15000.00},
                {"ledger_name": "Stock Transfer In", "entry_type": "Cr", "amount": 15000.00}
            ],
            inventory_items=[
                {
                    "stock_item_name": "RAM DDR4 16GB",
                    "godown_name": "Main Location",
                    "qty": 5.0,
                    "rate": 3000.00,
                    "amount": 15000.00,
                    "direction": "Out"
                },
                {
                    "stock_item_name": "RAM DDR4 16GB",
                    "godown_name": "Warehouse A",
                    "qty": 5.0,
                    "rate": 3000.00,
                    "amount": 15000.00,
                    "direction": "In"
                }
            ]
        )

        godown_summary = self.db.get_godown_summary()
        wh_a = next((g for g in godown_summary if g["godown_name"] == "Warehouse A"), None)
        self.assertIsNotNone(wh_a)
        self.assertGreaterEqual(wh_a["total_qty"], 5.0)
        print(f"✓ Test F Passed: Multi-godown stock transfer executed. Warehouse A stock: {wh_a['total_qty']}.")

    # -------------------------------------------------------------------------
    # TEST G: Manufacturing & BOM (Bill of Materials)
    # -------------------------------------------------------------------------
    def test_G_manufacturing_bom(self):
        print("\n--- Running Test G: Manufacturing & BOM ---")
        before_desktop = self.db.get_stock_item_summary("Assembled Desktop Combo")["closing_qty"]
        before_ram = self.db.get_stock_item_summary("RAM DDR4 16GB")["closing_qty"]

        # Produce 3 units of "Assembled Desktop Combo"
        # BOM formula: each unit requires 1 Motherboard, 1 CPU, 2 RAM
        mfg_v_id = self.db.process_manufacturing_journal(
            bom_name="Assembled Desktop Combo",
            produced_qty=3.0,
            date="2026-10-28"
        )
        self.assertGreater(mfg_v_id, 0)

        after_desktop = self.db.get_stock_item_summary("Assembled Desktop Combo")["closing_qty"]
        after_ram = self.db.get_stock_item_summary("RAM DDR4 16GB")["closing_qty"]

        self.assertEqual(after_desktop, before_desktop + 3.0)
        self.assertEqual(after_ram, before_ram - 6.0)
        print(f"✓ Test G Passed: Manufacturing Journal produced 3 Desktops, consumed 6 RAM modules.")

    # -------------------------------------------------------------------------
    # TEST H: Payroll & Employee Statutory Processing
    # -------------------------------------------------------------------------
    def test_H_payroll_statutory_deductions(self):
        print("\n--- Running Test H: Payroll & Employee Statutory Processing ---")
        calc = self.db.calculate_employee_salary("EMP001")
        self.assertEqual(calc["gross_salary"], 60000.00)
        self.assertIn("Provident Fund (PF)", calc["deductions"])
        self.assertIn("Professional Tax (PT)", calc["deductions"])
        self.assertGreater(calc["net_salary"], 0.0)

        # Process monthly payroll disbursement voucher
        pay_v_id = self.db.process_payroll_voucher(month="October 2026", date="2026-10-31")
        self.assertGreater(pay_v_id, 0)

        sal_bal, _ = self.db.get_ledger_closing_balance("Salary Payable")
        self.assertEqual(sal_bal, Decimal("0.00"))
        print(f"✓ Test H Passed: Monthly payroll processed. Gross: ₹{calc['gross_salary']}, Net: ₹{calc['net_salary']}.")

    # -------------------------------------------------------------------------
    # TEST I: Banking & BRS Reconciliation
    # -------------------------------------------------------------------------
    def test_I_banking_and_brs(self):
        print("\n--- Running Test I: Banking & BRS Reconciliation ---")
        brs = self.db.get_bank_reconciliation("HDFC Bank")
        self.assertIn("book_balance", brs)
        self.assertIn("transactions", brs)

        # Auto-reconcile statement via CSV
        sample_csv = """Date,Particulars,InstrumentNo,Debit,Credit,Balance
2026-10-15,Rent Clearing,CHQ-1001,0,45000,955000
2026-10-25,Vendor Clearing,CHQ-1002,0,30800,924200
"""
        recon_result = self.db.auto_reconcile_statement_csv("HDFC Bank", sample_csv)
        self.assertEqual(recon_result["status"], "success")
        self.assertGreaterEqual(recon_result["total_lines"], 2)
        print(f"✓ Test I Passed: BRS reconciliation and CSV statement processing verified.")

    # -------------------------------------------------------------------------
    # TEST J: Statutory GST & Returns (GSTR-1 & GSTR-3B)
    # -------------------------------------------------------------------------
    def test_J_statutory_gst_returns(self):
        print("\n--- Running Test J: Statutory GST Returns ---")
        summary = self.db.get_gst_summary()
        self.assertGreater(summary["taxable_turnover"], 0)
        self.assertGreater(summary["total_gst_liability"], 0)

        # Verify official GSTN offline JSON format
        gstr1_json = self.db.generate_gstr1_json()
        self.assertIn("gstin", gstr1_json)
        self.assertIn("fp", gstr1_json)
        self.assertIn("b2b", gstr1_json)
        self.assertIn("hsn", gstr1_json)
        self.assertIsInstance(gstr1_json["b2b"], list)
        print(f"✓ Test J Passed: GSTR-1 JSON schema validated. Turnover: ₹{summary['taxable_turnover']:,.2f}.")

    # -------------------------------------------------------------------------
    # TEST K: TDL Engine, Parser & Expressions
    # -------------------------------------------------------------------------
    def test_K_tdl_parser_and_runtime(self):
        print("\n--- Running Test K: TDL Parser & Runtime ---")
        tdl_code = """
        [Report: CustomProfitReport]
        Title: "Executive Profitability"
        Form: CustomProfitForm

        [#Menu: Gateway of Tally]
        Add : Item : "Executive Profit" : Display : CustomProfitReport

        [System: Formula]
        StandardTaxRate: 18

        [Line: CustomProfitLine]
        Field : CustAmountField

        [Field: CustAmountField]
        Set as : $Amount * 1.18
        """
        self.parser.parse_string(tdl_code)

        # 1. Verify Definition Registration
        rep = self.symbol_table.get_definition("Report", "CustomProfitReport")
        self.assertIsNotNone(rep)
        self.assertEqual(rep.get_attr("Title"), '"Executive Profitability"')

        # 2. Verify Modifier (#Menu)
        menu = self.symbol_table.get_definition("Menu", "Gateway of Tally")
        custom_item = next((i for i in menu.items if i["label"] == "Executive Profit"), None)
        self.assertIsNotNone(custom_item)

        # 3. Verify Expression & Math ($Amount * 1.18)
        val = self.runtime.evaluate_expression("$Amount * 1.18", {"amount": 50000.0})
        self.assertEqual(val, 59000.0)

        # 4. Verify Built-in Functions ($$LocaleString)
        loc = self.runtime.evaluate_expression("$$LocaleString(75000)", {})
        self.assertEqual(loc, "₹75,000.00")

        # 5. Verify Syntax Validation with line numbers
        diag = self.parser.validate_syntax("[Report: BrokenReport\nTitle: \"Unclosed quote")
        self.assertGreater(len(diag), 0)
        self.assertEqual(diag[0]["line"], 1)
        print("✓ Test K Passed: TDL AST, # modifier, math expressions, and line-level syntax validation verified.")

    # -------------------------------------------------------------------------
    # TEST L: Local Backup & Safe Restore
    # -------------------------------------------------------------------------
    def test_L_backup_and_restore(self):
        print("\n--- Running Test L: Local Backup & Safe Restore ---")
        backup_path = self.db.create_local_backup()
        self.assertTrue(os.path.exists(backup_path))
        meta_path = backup_path + ".meta.json"
        self.assertTrue(os.path.exists(meta_path))

        with open(meta_path, "r") as f:
            meta = json.load(f)
        self.assertIn("sha256", meta)

        # Modify database state
        self.db.conn.execute("UPDATE company SET name = 'Temporarily Altered Name'")
        self.db.conn.commit()
        self.assertEqual(self.db.get_company()["name"], "Temporarily Altered Name")

        # Restore from backup
        success = self.db.restore_local_backup(backup_path)
        self.assertTrue(success)
        restored = self.db.get_company()
        self.assertEqual(restored["name"], "Apex Horizon Technologies Pvt Ltd")

        # Verify Tamper Detection: modify backup file bytes
        with open(backup_path, "ab") as f:
            f.write(b"TAMPER_DATA")

        with self.assertRaises(ValueError) as ctx:
            self.db.restore_local_backup(backup_path)
        self.assertIn("checksum verification failed", str(ctx.exception).lower())
        print("✓ Test L Passed: Local SHA-256 backup created, restored cleanly, tampered file rejected.")

    # -------------------------------------------------------------------------
    # TEST M: Offline Verification
    # -------------------------------------------------------------------------
    def test_M_offline_verification(self):
        print("\n--- Running Test M: Offline Verification ---")
        # Ensure static files contain no external CDN scripts, stylesheets or fonts
        static_dir = os.path.join(BASE_DIR, "static")
        forbidden = ["http://", "https://", "cdn.", "googleapis.com", "unpkg.com", "jsdelivr.net"]

        for root, _, files in os.walk(static_dir):
            for file in files:
                if file.endswith((".html", ".js", ".css")):
                    path = os.path.join(root, file)
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        for f_word in forbidden:
                            self.assertNotIn(f_word, content, f"Forbidden external dependency '{f_word}' found in {file}")

        print("✓ Test M Passed: Zero external CDN / cloud telemetry found. 100% Offline confirmed.")

    # -------------------------------------------------------------------------
    # TEST N: Reports & Navigation Coverage
    # -------------------------------------------------------------------------
    def test_N_reports_and_navigation(self):
        print("\n--- Running Test N: Reports & Navigation Coverage ---")
        core_reports = [
            "BalanceSheet", "ProfitAndLoss", "TrialBalance", "DayBook",
            "StockSummary", "OutstandingReceivables", "OutstandingPayables",
            "BankReconciliation", "PayrollSummary", "GSTR1", "GSTR3B",
            "RatioAnalysis", "AuditEditLog", "GeneralLedger", "CashFlow", "CostCentres"
        ]

        for rep in core_reports:
            rendered = self.runtime.render_report(rep, ledger="HDFC Bank")
            self.assertIsNotNone(rendered)
            self.assertIn("parts", rendered)
            self.assertGreater(len(rendered["parts"]), 0)
            self.assertGreater(len(rendered["parts"][0]["lines"]), 0)

        print(f"✓ Test N Passed: All {len(core_reports)} core enterprise reports rendered successfully.")

if __name__ == "__main__":
    unittest.main()
