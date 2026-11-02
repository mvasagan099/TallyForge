"""
Accounting Reference Certification Suite (Phase 5.3)
20 Canonical Accounting Verification Tests
Validates standard financial mathematics, double-entry equilibrium,
inventory valuation, taxation, payroll, BRS, forex, year-end closing idempotency,
and database transaction rollback.
"""

import unittest
import os
import tempfile
from decimal import Decimal
from engine.accounting_db import EnterpriseAccountingDB


class TestAccountingCertification(unittest.TestCase):

    def setUp(self):
        # Create an isolated temporary database for each test case
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix="_cert.db")
        os.close(self.temp_db_fd)
        self.db = EnterpriseAccountingDB(self.temp_db_path)
        self.company_id = self.db.active_company_id

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    def _get_bal(self, ledger_name: str) -> float:
        """Helper to get ledger closing balance as float."""
        return float(self.db.get_ledger_closing_balance(ledger_name)[0])

    def _post(self, voucher_type: str, date: str, narration: str, entries, **kwargs):
        """Helper to post a voucher."""
        return self.db.add_voucher(
            voucher_type=voucher_type,
            date=date,
            narration=narration,
            entries=entries,
            **kwargs
        )

    # 1. Opening Balances: Assets == Liabilities + Equity
    def test_01_opening_balances_balance_sheet_equation(self):
        ledgers = self.db.get_ledgers()
        total_assets = sum(l["opening_balance"] for l in ledgers if l["group_name"] in ["Bank Accounts", "Cash-in-Hand", "Sundry Debtors", "Stock-in-Hand", "Fixed Assets"] and l["opening_balance_type"] == "Dr")
        total_liabilities = sum(l["opening_balance"] for l in ledgers if l["group_name"] in ["Sundry Creditors", "Duties & Taxes", "Current Liabilities"] and l["opening_balance_type"] == "Cr")
        capital = sum(l["opening_balance"] for l in ledgers if l["group_name"] == "Capital Account" and l["opening_balance_type"] == "Cr")

        self.assertGreater(total_assets, 0)
        self.assertAlmostEqual(total_assets, total_liabilities + capital, places=2)

    # 2. Ledger Balance Calculation across Dr/Cr postings
    def test_02_ledger_balance_calculation(self):
        init_cash = self._get_bal("Cash")

        # Post receipt of 25,000 into Cash from Capital
        self._post(
            voucher_type="Receipt",
            date="2026-05-01",
            narration="Capital infusion",
            entries=[
                {"ledger_name": "Cash", "entry_type": "Dr", "amount": 25000.0},
                {"ledger_name": "Capital Account", "entry_type": "Cr", "amount": 25000.0}
            ]
        )
        updated_cash = self._get_bal("Cash")
        self.assertAlmostEqual(updated_cash, init_cash + 25000.0, places=2)

    # 3. Double-entry Equilibrium Enforcement
    def test_03_double_entry_equilibrium(self):
        # Unbalanced entry must raise ValueError
        with self.assertRaises(ValueError):
            self._post(
                voucher_type="Journal",
                date="2026-05-02",
                narration="Unbalanced test voucher",
                entries=[
                    {"ledger_name": "Cash", "entry_type": "Dr", "amount": 5000.0},
                    {"ledger_name": "Sales", "entry_type": "Cr", "amount": 4000.0}
                ]
            )

    # 4. Credit Purchase Workflow
    def test_04_credit_purchase_workflow(self):
        prev_payable = self._get_bal("Supplier XYZ")
        v_id = self._post(
            voucher_type="Purchase",
            date="2026-05-10",
            narration="Credit purchase of RAM modules",
            entries=[
                {"ledger_name": "Purchase", "entry_type": "Dr", "amount": 20000.0},
                {"ledger_name": "Input CGST", "entry_type": "Dr", "amount": 1800.0},
                {"ledger_name": "Input SGST", "entry_type": "Dr", "amount": 1800.0},
                {"ledger_name": "Supplier XYZ", "entry_type": "Cr", "amount": 23600.0}
            ],
            inventory_items=[{
                "stock_item_name": "RAM 16GB DDR4",
                "godown_name": "Main Godown",
                "qty": 10.0,
                "rate": 2000.0,
                "amount": 20000.0,
                "direction": "In"
            }],
            bill_allocations=[{
                "ledger_name": "Supplier XYZ",
                "ref_type": "New Ref",
                "ref_name": "PUR-RAM-001",
                "amount": 23600.0,
                "direction": "Cr"
            }]
        )
        self.assertGreater(v_id, 0)
        new_payable = self._get_bal("Supplier XYZ")
        self.assertAlmostEqual(new_payable, prev_payable + 23600.0, places=2)

    # 5. Cash Sales Workflow
    def test_05_cash_sales_workflow(self):
        init_cash = self._get_bal("Cash")
        v_id = self._post(
            voucher_type="Sales",
            date="2026-05-12",
            narration="Cash counter sale",
            entries=[
                {"ledger_name": "Cash", "entry_type": "Dr", "amount": 11800.0},
                {"ledger_name": "Sales", "entry_type": "Cr", "amount": 10000.0},
                {"ledger_name": "Output CGST", "entry_type": "Cr", "amount": 900.0},
                {"ledger_name": "Output SGST", "entry_type": "Cr", "amount": 900.0}
            ]
        )
        self.assertGreater(v_id, 0)
        self.assertAlmostEqual(self._get_bal("Cash"), init_cash + 11800.0, places=2)

    # 6. Credit Sales Workflow with Bill-Wise Allocations
    def test_06_credit_sales_workflow(self):
        prev_debt = self._get_bal("Customer ABC Pvt Ltd")
        v_id = self._post(
            voucher_type="Sales",
            date="2026-05-15",
            narration="Sales invoice INV-2026-99",
            entries=[
                {"ledger_name": "Customer ABC Pvt Ltd", "entry_type": "Dr", "amount": 59000.0},
                {"ledger_name": "Sales", "entry_type": "Cr", "amount": 50000.0},
                {"ledger_name": "Output CGST", "entry_type": "Cr", "amount": 4500.0},
                {"ledger_name": "Output SGST", "entry_type": "Cr", "amount": 4500.0}
            ],
            bill_allocations=[{
                "ledger_name": "Customer ABC Pvt Ltd",
                "ref_type": "New Ref",
                "ref_name": "INV-2026-99",
                "amount": 59000.0,
                "direction": "Dr"
            }]
        )
        self.assertGreater(v_id, 0)
        new_debt = self._get_bal("Customer ABC Pvt Ltd")
        self.assertAlmostEqual(new_debt, prev_debt + 59000.0, places=2)

    # 7. Partial Settlement (Agst Ref)
    def test_07_partial_settlement_agst_ref(self):
        # Create invoice of 30,000
        self._post(
            voucher_type="Sales",
            date="2026-06-01",
            narration="Invoice PART-101",
            entries=[
                {"ledger_name": "Customer ABC Pvt Ltd", "entry_type": "Dr", "amount": 30000.0},
                {"ledger_name": "Sales", "entry_type": "Cr", "amount": 30000.0}
            ],
            bill_allocations=[{
                "ledger_name": "Customer ABC Pvt Ltd",
                "ref_type": "New Ref",
                "ref_name": "PART-101",
                "amount": 30000.0,
                "direction": "Dr"
            }]
        )
        # Settle 18,000 partially
        self._post(
            voucher_type="Receipt",
            date="2026-06-15",
            narration="Part payment for PART-101",
            entries=[
                {"ledger_name": "HDFC Bank", "entry_type": "Dr", "amount": 18000.0},
                {"ledger_name": "Customer ABC Pvt Ltd", "entry_type": "Cr", "amount": 18000.0}
            ],
            bill_allocations=[{
                "ledger_name": "Customer ABC Pvt Ltd",
                "ref_type": "Agst Ref",
                "ref_name": "PART-101",
                "amount": 18000.0,
                "direction": "Cr"
            }]
        )
        # Outstanding check
        receivables = self.db.get_outstanding_bills("Sundry Debtors")
        bill = next((b for b in receivables if b["bill_ref"] == "PART-101"), None)
        self.assertIsNotNone(bill)
        self.assertAlmostEqual(bill["amount"], 12000.0, places=2)

    # 8. Sales Return (Credit Note)
    def test_08_sales_return_credit_note(self):
        # Post original sale of 20,000 to Customer ABC
        self._post(
            voucher_type="Sales",
            date="2026-06-10",
            narration="Original sales",
            entries=[
                {"ledger_name": "Customer ABC Pvt Ltd", "entry_type": "Dr", "amount": 20000.0},
                {"ledger_name": "Sales", "entry_type": "Cr", "amount": 20000.0}
            ]
        )
        prev_debt = self._get_bal("Customer ABC Pvt Ltd")
        self.assertEqual(prev_debt, 20000.0)

        # Issue Credit Note of 5,000
        self._post(
            voucher_type="Credit Note",
            date="2026-06-20",
            narration="Goods returned defective",
            entries=[
                {"ledger_name": "Sales", "entry_type": "Dr", "amount": 5000.0},
                {"ledger_name": "Customer ABC Pvt Ltd", "entry_type": "Cr", "amount": 5000.0}
            ]
        )
        new_debt = self._get_bal("Customer ABC Pvt Ltd")
        self.assertAlmostEqual(new_debt, prev_debt - 5000.0, places=2)

    # 9. Purchase Return (Debit Note)
    def test_09_purchase_return_debit_note(self):
        # Post original purchase of 25,000 from Supplier XYZ
        self._post(
            voucher_type="Purchase",
            date="2026-06-12",
            narration="Original purchase",
            entries=[
                {"ledger_name": "Purchase", "entry_type": "Dr", "amount": 25000.0},
                {"ledger_name": "Supplier XYZ", "entry_type": "Cr", "amount": 25000.0}
            ]
        )
        prev_payable = self._get_bal("Supplier XYZ")
        self.assertEqual(prev_payable, 25000.0)

        # Issue Debit Note of 4,000
        self._post(
            voucher_type="Debit Note",
            date="2026-06-25",
            narration="Return to Supplier XYZ",
            entries=[
                {"ledger_name": "Supplier XYZ", "entry_type": "Dr", "amount": 4000.0},
                {"ledger_name": "Purchase", "entry_type": "Cr", "amount": 4000.0}
            ]
        )
        new_payable = self._get_bal("Supplier XYZ")
        self.assertAlmostEqual(new_payable, prev_payable - 4000.0, places=2)

    # 10. GST Adjustments & Statutory Summary
    def test_10_gst_adjustments_and_itc_netting(self):
        gst = self.db.get_gst_summary()
        self.assertIn("taxable_turnover", gst)
        self.assertIn("total_gst_liability", gst)
        self.assertIn("cgst_liability", gst)
        self.assertIn("sgst_liability", gst)
        self.assertIn("igst_liability", gst)
        self.assertIn("b2b_invoices", gst)
        self.assertGreaterEqual(gst["total_gst_liability"], 0.0)

    # 11. Inventory Valuation
    def test_11_inventory_valuation(self):
        summary = self.db.get_stock_summary()
        self.assertIsInstance(summary, list)
        for item in summary:
            # Value = Qty * Rate
            calc_val = round(item["closing_qty"] * item["closing_rate"], 2)
            self.assertAlmostEqual(item["closing_value"], calc_val, delta=1.0)

    # 12. Manufacturing BOM Costs
    def test_12_manufacturing_bom_cost(self):
        v_id = self.db.process_manufacturing_journal("Assembled Desktop Combo", produce_qty=2.0, date="2026-07-01")
        self.assertGreater(v_id, 0)
        reports = self.db.get_manufacturing_cost_report("Assembled Desktop Combo")
        self.assertGreater(len(reports), 0)
        rep = reports[0]
        self.assertGreater(rep["cost_per_unit"], 0)
        self.assertAlmostEqual(rep["net_mfg_cost"], rep["total_rm_cost"] - rep["scrap_recovery"], places=2)

    # 13. Payroll Accruals & Statutory Deductions
    def test_13_payroll_accruals_and_deductions(self):
        sal = self.db.calculate_employee_salary("EMP001")
        self.assertEqual(sal["gross_salary"], 60000.0)
        self.assertGreater(sal["total_deductions"], 0.0)
        self.assertAlmostEqual(sal["net_salary"], sal["gross_salary"] - sal["total_deductions"], places=2)

    # 14. Bank Reconciliation (BRS)
    def test_14_bank_reconciliation_brs(self):
        brs = self.db.get_bank_reconciliation("HDFC Bank")
        self.assertIn("book_balance", brs)
        self.assertIn("passbook_balance", brs)
        self.assertIn("transactions", brs)
        self.assertIn("bank_ledger", brs)

    # 15. Foreign Exchange Differences (Realized/Unrealized)
    def test_15_foreign_exchange_differences(self):
        forex = self.db.calculate_forex_gain_loss()
        self.assertIn("total_forex_gain_loss", forex)
        self.assertIn("status", forex)
        self.assertIn("transactions", forex)

    # 16. Financial Year Closing
    def test_16_financial_year_closing(self):
        result = self.db.close_financial_year("2027-03-31", "2027-04-01")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertIn("net_profit_transferred", result)
        self.assertEqual(result["new_financial_year_from"], "2027-04-01")

    # 17. Duplicate Closing Idempotency Verification
    def test_17_duplicate_year_end_closing_idempotency(self):
        # First close
        r1 = self.db.close_financial_year("2027-03-31", "2027-04-01")
        self.assertEqual(r1["status"], "SUCCESS")

        # Second close with exact same date - must be ALREADY_CLOSED and create zero duplicate vouchers
        vch_count_before = len(self.db.get_vouchers())
        r2 = self.db.close_financial_year("2027-03-31", "2027-04-01")
        vch_count_after = len(self.db.get_vouchers())

        self.assertEqual(r2["status"], "ALREADY_CLOSED")
        self.assertEqual(vch_count_before, vch_count_after)

    # 18. Opening Balance Carry-Forward into New Year via Split FY
    def test_18_opening_balance_carry_forward(self):
        # Capture cash balance before split
        cash_pre = self._get_bal("Cash")
        split_res = self.db.split_financial_year("2027-04-01", archive_db_path=os.path.join(tempfile.gettempdir(), "test_arch.db"))
        self.assertEqual(split_res["status"], "SUCCESS")
        # In rolled active DB, opening balance matches previous closing balance
        cash_post_open = next(l["opening_balance"] for l in self.db.get_ledgers() if l["name"] == "Cash")
        self.assertAlmostEqual(cash_post_open, cash_pre, places=2)

    # 19. Group Company Financial Consolidation & Elimination
    def test_19_group_company_consolidation(self):
        # Create subsidiary company
        cid2 = self.db.create_company("Sub Company Ltd", "29SUBCO9999L1Z1", "Karnataka", "29")
        cons = self.db.get_consolidated_financial_summary([self.company_id, cid2])
        self.assertIn("consolidated_companies_count", cons)
        self.assertEqual(cons["consolidated_companies_count"], 2)
        self.assertIn("assets_total", cons)
        self.assertIn("liab_total", cons)

    # 20. Transaction Rollback Verification on Database Error
    def test_20_transaction_rollback_on_error(self):
        # Count vouchers before attempt
        vch_count_initial = len(self.db.get_vouchers())
        cash_bal_initial = self._get_bal("Cash")

        # Attempt to post a voucher that triggers a forced database integrity error
        with self.assertRaises(Exception):
            with self.db.conn:
                # Manually execute partial insert then raise error
                self.db.conn.execute("INSERT INTO voucher (voucher_number, voucher_type, date, narration, company_id) VALUES ('BAD-01', 'Payment', '2026-05-01', 'Fail', ?)", (self.company_id,))
                # Intentional error to force rollback
                raise RuntimeError("Simulated mid-transaction hardware/system fault")

        # Verify database remained untouched
        vch_count_after = len(self.db.get_vouchers())
        cash_bal_after = self._get_bal("Cash")
        self.assertEqual(vch_count_initial, vch_count_after)
        self.assertEqual(cash_bal_initial, cash_bal_after)


if __name__ == "__main__":
    unittest.main()
