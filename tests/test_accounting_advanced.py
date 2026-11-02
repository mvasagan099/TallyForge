"""
Test Suite: Advanced Accounting Verification
Covers:
- Opening balances and capital equity
- Cash vs Credit sales
- Domestic vs Interstate purchases (CGST/SGST vs IGST)
- Trade discounts and round-off adjustments
- Partial bill settlements (New Ref vs Agst Ref)
- Debit and Credit notes (Returns)
- Inter-company reciprocal consolidation eliminations
- Idempotent Financial Year Closing
- Double-entry mathematical constraint enforcement
"""

import unittest
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.accounting_db import EnterpriseAccountingDB

class TestAdvancedAccounting(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_accounting.db")
        self.db = EnterpriseAccountingDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def _get_tb_totals(self):
        tb = self.db.get_trial_balance()
        tot_dr = sum(r["dr_amount"] for r in tb)
        tot_cr = sum(r["cr_amount"] for r in tb)
        return tot_dr, tot_cr, abs(tot_dr - tot_cr) < 0.01

    def test_01_opening_balances_and_capital(self):
        """Verifies opening balance setup and capital equity integrity."""
        self.db.update_company("Advanced Test Co", gstin="29AABCU9999L1Z5", state="Karnataka", state_code="29")
        
        # Add Capital and Bank with matching opening balances
        self.db.add_ledger("Partner Capital", "Capital Account", 500000.0, "Cr")
        self.db.add_ledger("Axis Bank", "Bank Accounts", 500000.0, "Dr")

        tot_dr, tot_cr, balanced = self._get_tb_totals()
        self.assertTrue(balanced)
        self.assertEqual(tot_dr, 1065000.0) # 565000 seeded + 500000
        self.assertEqual(tot_cr, 1065000.0)

    def test_02_cash_sales_and_credit_sales(self):
        """Verifies both Cash Sales and B2B Credit Sales with GST posting."""
        self.db.update_company("Sales Co", gstin="29AABCU9999L1Z5", state="Karnataka", state_code="29")
        self.db.add_ledger("Counter Sales A/c", "Sales Accounts", 0.0, "Cr")
        self.db.add_ledger("CGST Output", "Duties & Taxes", 0.0, "Cr")
        self.db.add_ledger("SGST Output", "Duties & Taxes", 0.0, "Cr")
        self.db.add_ledger("Customer Retail", "Sundry Debtors", 0.0, "Dr")

        # 1. Cash Sale: 10,000 + 900 CGST + 900 SGST = 11,800
        v_cash = self.db.add_voucher(
            "Sales", "2026-05-01", "Cash sale at counter",
            [
                {"ledger_name": "Cash", "entry_type": "Dr", "amount": 11800.0},
                {"ledger_name": "Counter Sales A/c", "entry_type": "Cr", "amount": 10000.0},
                {"ledger_name": "CGST Output", "entry_type": "Cr", "amount": 900.0},
                {"ledger_name": "SGST Output", "entry_type": "Cr", "amount": 900.0}
            ]
        )
        self.assertIsNotNone(v_cash)

        # 2. Credit Sale: 50,000 + 4,500 CGST + 4,500 SGST = 59,000
        v_credit = self.db.add_voucher(
            "Sales", "2026-05-02", "B2B Credit Sale",
            [
                {"ledger_name": "Customer Retail", "entry_type": "Dr", "amount": 59000.0},
                {"ledger_name": "Counter Sales A/c", "entry_type": "Cr", "amount": 50000.0},
                {"ledger_name": "CGST Output", "entry_type": "Cr", "amount": 4500.0},
                {"ledger_name": "SGST Output", "entry_type": "Cr", "amount": 4500.0}
            ],
            bill_allocations=[
                {"ledger_name": "Customer Retail", "ref_type": "New Ref", "ref_name": "INV-CR-01", "amount": 59000.0, "direction": "Dr"}
            ]
        )
        self.assertIsNotNone(v_credit)

        tot_dr, tot_cr, balanced = self._get_tb_totals()
        self.assertTrue(balanced)
        self.assertEqual(tot_dr, 565000.0 + 11800.0 + 59000.0)

    def test_03_domestic_and_interstate_purchases(self):
        """Verifies Intra-State (CGST+SGST) vs Inter-State (IGST) purchase workflows."""
        self.db.update_company("Purch Co", gstin="29AABCU9999L1Z5", state="Karnataka", state_code="29")
        self.db.add_ledger("Local Supplier", "Sundry Creditors", 0.0, "Cr")
        self.db.add_ledger("Interstate Supplier", "Sundry Creditors", 0.0, "Cr")

        # 1. Intra-State Purchase: 100,000 + 9,000 CGST + 9,000 SGST = 118,000
        self.db.add_voucher(
            "Purchase", "2026-05-10", "Intra-state raw material purchase",
            [
                {"ledger_name": "Purchase A/c", "entry_type": "Dr", "amount": 100000.0},
                {"ledger_name": "CGST", "entry_type": "Dr", "amount": 9000.0},
                {"ledger_name": "SGST", "entry_type": "Dr", "amount": 9000.0},
                {"ledger_name": "Local Supplier", "entry_type": "Cr", "amount": 118000.0}
            ]
        )

        # 2. Inter-State Purchase: 200,000 + 36,000 IGST (18%) = 236,000
        self.db.add_voucher(
            "Purchase", "2026-05-12", "Interstate equipment purchase",
            [
                {"ledger_name": "Purchase A/c", "entry_type": "Dr", "amount": 200000.0},
                {"ledger_name": "IGST", "entry_type": "Dr", "amount": 36000.0},
                {"ledger_name": "Interstate Supplier", "entry_type": "Cr", "amount": 236000.0}
            ]
        )

        tot_dr, tot_cr, balanced = self._get_tb_totals()
        self.assertTrue(balanced)

        # Check ITC ledger balance
        igst_bal, btype = self.db.get_ledger_closing_balance("IGST")
        self.assertEqual(float(igst_bal), 36000.0)
        self.assertEqual(btype, "Dr")

    def test_04_trade_discount_and_round_off(self):
        """Verifies multi-line compound vouchers with discounts and round-off adjustments."""
        self.db.update_company("Discount Co", gstin="29AABCU9999L1Z5", state="Karnataka", state_code="29")
        self.db.add_ledger("Discount Allowed", "Indirect Expenses", 0.0, "Dr")
        self.db.add_ledger("Round Off", "Indirect Expenses", 0.0, "Dr")

        # Gross: 10,000. Discount: 500 Dr. Net before roundoff: 9,500.25.
        # Cash received: 9,500.00. Round off adjustment: 0.25 Dr.
        self.db.add_voucher(
            "Sales", "2026-05-15", "Sales with trade discount and round-off",
            [
                {"ledger_name": "Cash", "entry_type": "Dr", "amount": 9500.0},
                {"ledger_name": "Discount Allowed", "entry_type": "Dr", "amount": 500.0},
                {"ledger_name": "Round Off", "entry_type": "Dr", "amount": 0.25},
                {"ledger_name": "Sales A/c", "entry_type": "Cr", "amount": 10000.25}
            ]
        )

        tot_dr, tot_cr, balanced = self._get_tb_totals()
        self.assertTrue(balanced)

    def test_05_partial_bill_settlement(self):
        """Verifies New Ref creation and subsequent Agst Ref partial settlements."""
        self.db.update_company("Settlement Co", gstin="29AABCU9999L1Z5", state="Karnataka", state_code="29")
        self.db.add_ledger("Customer ABC", "Sundry Debtors", 0.0, "Dr")

        # 1. Invoice for 1,00,000
        self.db.add_voucher(
            "Sales", "2026-05-01", "Credit invoice",
            [
                {"ledger_name": "Customer ABC", "entry_type": "Dr", "amount": 100000.0},
                {"ledger_name": "Sales A/c", "entry_type": "Cr", "amount": 100000.0}
            ],
            bill_allocations=[
                {"ledger_name": "Customer ABC", "ref_type": "New Ref", "ref_name": "INV-101", "amount": 100000.0, "direction": "Dr"}
            ]
        )

        # 2. Partial receipt of 40,000 against INV-101
        self.db.add_voucher(
            "Receipt", "2026-05-10", "Partial settlement",
            [
                {"ledger_name": "HDFC Bank", "entry_type": "Dr", "amount": 40000.0},
                {"ledger_name": "Customer ABC", "entry_type": "Cr", "amount": 40000.0}
            ],
            bill_allocations=[
                {"ledger_name": "Customer ABC", "ref_type": "Agst Ref", "ref_name": "INV-101", "amount": 40000.0, "direction": "Cr"}
            ]
        )

        # Check outstanding receivables
        bills = self.db.get_outstanding_bills("Sundry Debtors")
        matched = [b for b in bills if b["bill_ref"] == "INV-101"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["pending_amount"], 60000.0)

    def test_06_debit_and_credit_notes(self):
        """Verifies Sales Returns via Credit Note and Purchase Returns via Debit Note."""
        self.db.update_company("Notes Co", gstin="29AABCU9999L1Z5", state="Karnataka", state_code="29")
        self.db.add_ledger("Sales Returns", "Sales Accounts", 0.0, "Dr")
        self.db.add_ledger("Purchase Returns", "Purchase Accounts", 0.0, "Cr")
        self.db.add_ledger("Customer X", "Sundry Debtors", 50000.0, "Dr")
        self.db.add_ledger("Vendor Y", "Sundry Creditors", 80000.0, "Cr")

        # 1. Credit Note: Customer X returns goods worth 15,000
        self.db.add_voucher(
            "Credit Note", "2026-06-01", "Sales return from customer",
            [
                {"ledger_name": "Sales Returns", "entry_type": "Dr", "amount": 15000.0},
                {"ledger_name": "Customer X", "entry_type": "Cr", "amount": 15000.0}
            ]
        )
        bal_x, btype_x = self.db.get_ledger_closing_balance("Customer X")
        self.assertEqual(float(bal_x), 35000.0)
        self.assertEqual(btype_x, "Dr")

        # 2. Debit Note: Return goods to Vendor Y worth 25,000
        self.db.add_voucher(
            "Debit Note", "2026-06-02", "Purchase return to vendor",
            [
                {"ledger_name": "Vendor Y", "entry_type": "Dr", "amount": 25000.0},
                {"ledger_name": "Purchase Returns", "entry_type": "Cr", "amount": 25000.0}
            ]
        )
        bal_y, btype_y = self.db.get_ledger_closing_balance("Vendor Y")
        self.assertEqual(float(bal_y), 55000.0)
        self.assertEqual(btype_y, "Cr")

    def test_07_inter_company_consolidation_elimination(self):
        """Verifies automated elimination of reciprocal inter-company member balances."""
        # Main Parent Company
        self.db.update_company("Apex Holding Ltd", gstin="29AABCA1111L1Z1", state="Karnataka", state_code="29")
        self.db.add_ledger("Apex Tech Subsidiary", "Sundry Debtors", 100000.0, "Dr")

        # Subsidiary Company
        sub_id = self.db.create_company("Apex Tech Subsidiary", gstin="29AABCB2222L1Z2", state="Karnataka", state_code="29")
        self.db.select_company(sub_id)
        self.db.add_ledger("Apex Holding Ltd", "Sundry Creditors", 100000.0, "Cr")
        self.db.add_ledger("Petty Cash", "Cash-in-Hand", 100000.0, "Dr")

        # Switch back to parent and verify eliminations
        self.db.select_company(1)
        res = self.db.get_consolidation_eliminations([1, sub_id])
        self.assertEqual(len(res["eliminations"]), 1)
        self.assertEqual(res["total_eliminated_amount"], 100000.0)

        # Verify consolidated summary deducts the 1,00,000 reciprocal balance
        cons = self.db.get_consolidated_financial_summary([1, sub_id])
        self.assertEqual(cons["eliminated_intercompany_amount"], 100000.0)

    def test_08_financial_year_closing_idempotence(self):
        """Verifies that closing financial year creates closing voucher and prevents repeated execution."""
        self.db.update_company("YearEnd Co", gstin="29AABCU9999L1Z5", state="Karnataka", state_code="29")

        # Sales 50,000, Rent 20,000 -> Net Profit = 30,000
        self.db.add_voucher("Sales", "2026-06-01", "Revenue", [
            {"ledger_name": "Cash", "entry_type": "Dr", "amount": 50000.0},
            {"ledger_name": "Sales A/c", "entry_type": "Cr", "amount": 50000.0}
        ])
        self.db.add_voucher("Payment", "2026-06-02", "Office Rent", [
            {"ledger_name": "Office Rent", "entry_type": "Dr", "amount": 20000.0},
            {"ledger_name": "Cash", "entry_type": "Cr", "amount": 20000.0}
        ])

        # Execute year-end closing
        res1 = self.db.close_financial_year("2027-03-31", "2027-04-01")
        self.assertEqual(res1["status"], "SUCCESS")
        self.assertEqual(res1["net_profit_transferred"], 30000.0)

        # Execute second time -> Must be idempotent (ALREADY_CLOSED)
        res2 = self.db.close_financial_year("2027-03-31", "2027-04-01")
        self.assertEqual(res2["status"], "ALREADY_CLOSED")

    def test_09_double_entry_imbalance_rejection(self):
        """Enforces that an unbalanced debit/credit voucher raises ValueError and is rejected."""
        self.db.update_company("Integrity Co", gstin="29AABCU9999L1Z5", state="Karnataka", state_code="29")

        with self.assertRaises(ValueError):
            self.db.add_voucher("Sales", "2026-05-01", "Imbalanced entry", [
                {"ledger_name": "Cash", "entry_type": "Dr", "amount": 1000.0},
                {"ledger_name": "Sales A/c", "entry_type": "Cr", "amount": 900.0}  # Imbalanced by 100
            ])

if __name__ == "__main__":
    unittest.main()
