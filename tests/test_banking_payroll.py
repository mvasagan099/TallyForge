"""
Test Suite: Banking & Payroll Verification
Covers:
- Bank Reconciliation Statement (BRS) manual clearance
- Automated CSV bank statement reconciliation
- Cheque book management and automated sequential cheque issuance
- Payment advice slip generation with amount in words
- Monthly payroll processing and journal generation
- Statutory Provident Fund (PF) and Professional Tax (PT) calculations
"""

import unittest
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.accounting_db import EnterpriseAccountingDB

class TestBankingPayroll(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_banking_payroll.db")
        self.db = EnterpriseAccountingDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_01_manual_bank_reconciliation_brs(self):
        """Verifies Bank Reconciliation Statement and manual date clearance."""
        # 1. Post a payment from HDFC Bank
        vid = self.db.add_voucher(
            "Payment", "2026-05-02", "Vendor payment via cheque",
            [
                {"ledger_name": "Apex Systems Ltd (Creditor)", "entry_type": "Dr", "amount": 25000.0},
                {"ledger_name": "HDFC Bank", "entry_type": "Cr", "amount": 25000.0}
            ]
        )
        self.assertGreater(vid, 0)

        # 2. Check BRS initial status (uncleared)
        brs1 = self.db.get_bank_reconciliation("HDFC Bank")
        self.assertGreaterEqual(len(brs1["transactions"]), 1)
        unrec = next((t for t in brs1["transactions"] if t["status"] == "Unreconciled"), None)
        self.assertIsNotNone(unrec)

        # 3. Clear the transaction as of 2026-05-05
        ok = self.db.reconcile_transaction(unrec["id"], "2026-05-05")
        self.assertTrue(ok)

        # 4. Check BRS status after clearance
        brs2 = self.db.get_bank_reconciliation("HDFC Bank")
        cleared = next((t for t in brs2["transactions"] if t["id"] == unrec["id"]), None)
        self.assertIsNotNone(cleared)
        self.assertEqual(cleared["status"], "Reconciled")
        self.assertEqual(cleared["bank_date"], "2026-05-05")

    def test_02_automated_csv_statement_reconciliation(self):
        """Verifies batch CSV bank statement reconciliation."""
        # Post payment voucher
        vid = self.db.add_voucher(
            "Payment", "2026-06-10", "RTGS Vendor Payment",
            [
                {"ledger_name": "Office Rent", "entry_type": "Dr", "amount": 35000.0},
                {"ledger_name": "State Bank of India", "entry_type": "Cr", "amount": 35000.0}
            ]
        )
        self.assertGreater(vid, 0)

        csv_content = """Date,Narration,ChqNo,Debit,Credit,Balance
2026-06-12,Rent Settlement,CHQ-0001,35000,0,145000"""

        res = self.db.auto_reconcile_statement_csv("State Bank of India", csv_content)
        self.assertGreaterEqual(res["reconciled_count"], 1)

    def test_03_cheque_book_management_and_issuance(self):
        """Verifies cheque book registration and sequential cheque numbering."""
        ok = self.db.add_cheque_book("HDFC Bank", "Main Series A", 100001, 100050)
        self.assertTrue(ok)

        vid = self.db.add_voucher(
            "Payment", "2026-07-01", "Cheque Payment",
            [
                {"ledger_name": "Office Rent", "entry_type": "Dr", "amount": 10000.0},
                {"ledger_name": "HDFC Bank", "entry_type": "Cr", "amount": 10000.0}
            ]
        )

        chq = self.db.issue_cheque("HDFC Bank", vid)
        self.assertEqual(chq["status"], "SUCCESS")
        self.assertEqual(chq["instrument_no"], "100001")

    def test_04_payment_advice_generation(self):
        """Verifies payment advice slip generation for vendor remittances."""
        vid = self.db.add_voucher(
            "Payment", "2026-08-15", "Vendor Settlement",
            [
                {"ledger_name": "Apex Systems Ltd (Creditor)", "entry_type": "Dr", "amount": 45000.0},
                {"ledger_name": "HDFC Bank", "entry_type": "Cr", "amount": 45000.0}
            ]
        )

        advice = self.db.get_payment_advice(vid)
        self.assertEqual(advice["amount"], 45000.0)
        self.assertIn("Forty Five Thousand", advice["amount_in_words"])
        self.assertEqual(advice["beneficiary_name"], "Apex Systems Ltd (Creditor)")

    def test_05_monthly_payroll_processing(self):
        """Processes monthly salaries for all active employees."""
        p_id = self.db.process_payroll_voucher(month="October 2026", payment_date="2026-10-31")
        self.assertGreater(p_id, 0)

        # Check Trial Balance remains strictly balanced
        tb = self.db.get_trial_balance()
        tot_dr = sum(r["dr_amount"] for r in tb)
        tot_cr = sum(r["cr_amount"] for r in tb)
        self.assertAlmostEqual(tot_dr, tot_cr, places=2)

    def test_06_statutory_pf_pt_deductions_and_payslip(self):
        """Verifies employee salary component breakdown and statutory deductions."""
        slip = self.db.calculate_employee_salary("EMP001")
        self.assertEqual(slip["emp_code"], "EMP001")
        self.assertEqual(slip["name"], "Arjun Sharma")

        # Verify earnings and statutory deductions exist
        self.assertGreater(slip["gross_salary"], 0.0)
        self.assertGreater(slip["total_deductions"], 0.0)
        self.assertEqual(slip["net_salary"], slip["gross_salary"] - slip["total_deductions"])

        self.assertIn("Provident Fund (PF)", slip["deductions"])
        self.assertIn("Professional Tax (PT)", slip["deductions"])

if __name__ == "__main__":
    unittest.main()
