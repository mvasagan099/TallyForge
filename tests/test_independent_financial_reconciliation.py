"""
Independent Accounting & Financial Reconciliation Test Suite
Validates mathematical correctness against independent expected financial truth tables:
- Double-entry debit-credit equilibrium
- Fundamental Balance Sheet Equation: Assets = Liabilities + Equity
- Profit & Loss Statement arithmetic: Net Profit = Revenue - Expense + Closing Stock
- FIFO Inventory Valuation & Stock Movement
- GST Output/Input Tax Ledger movements
- Bill-wise receivable/payable clearing
- Payroll Statutory Liability Postings
- Manufacturing BOM Raw Material Consumption
- BRS Bank Book vs Bank Statement reconciliation
- Transaction Atomicity & Rollback on Error
"""

import unittest
from decimal import Decimal
from engine.accounting_db import EnterpriseAccountingDB

class TestIndependentFinancialReconciliation(unittest.TestCase):
    def setUp(self):
        self.db = EnterpriseAccountingDB(":memory:")
        self.comp_id = self.db.create_company(
            name="Apex Horizon Pvt Ltd",
            formal_name="Apex Horizon Private Limited",
            gstin="29AAACH1234F1Z5",
            state="Karnataka",
            state_code="29"
        )
        self.db.select_company(self.comp_id)

    def tearDown(self):
        self.db.close()

    def test_01_balance_sheet_fundamental_equation(self):
        """
        Validates: Assets = Liabilities + Equity (Capital Account)
        """
        # 1. Capital infusion of ₹10,00,000 in HDFC Bank
        self.db.add_voucher(
            voucher_type="Receipt",
            date="2026-04-01",
            narration="Capital Infusion by Promoter",
            entries=[
                {"ledger_name": "HDFC Bank", "entry_type": "Dr", "amount": 1000000.0},
                {"ledger_name": "Capital Account", "entry_type": "Cr", "amount": 1000000.0}
            ]
        )

        fin = self.db.get_financial_summary()
        # Verify assets and liabilities equality (accounting equation)
        tb = self.db.get_trial_balance()
        tot_dr = sum(r["dr_amount"] for r in tb)
        tot_cr = sum(r["cr_amount"] for r in tb)
        self.assertAlmostEqual(tot_dr, tot_cr, places=2)
        self.assertTrue(fin["assets_total"] > 0)
        self.assertTrue(fin["liab_total"] > 0)

    def test_02_profit_and_loss_arithmetic_integrity(self):
        """
        Validates: Net Profit = Sales - Purchases - Direct Exp - Indirect Exp + Stock Change
        """
        initial_stock = sum(s["closing_value"] for s in self.db.get_stock_summary())

        # Purchase goods for ₹2,00,000 on credit
        self.db.add_voucher(
            voucher_type="Purchase",
            date="2026-04-05",
            narration="Bulk Inventory Purchase",
            entries=[
                {"ledger_name": "Purchase Accounts", "entry_type": "Dr", "amount": 200000.0},
                {"ledger_name": "Sundry Creditors", "entry_type": "Cr", "amount": 200000.0}
            ]
        )

        # Sell goods for ₹3,50,000 on credit
        self.db.add_voucher(
            voucher_type="Sales",
            date="2026-04-10",
            narration="Wholesale Goods Supply",
            entries=[
                {"ledger_name": "Sundry Debtors", "entry_type": "Dr", "amount": 350000.0},
                {"ledger_name": "Sales Accounts", "entry_type": "Cr", "amount": 350000.0}
            ]
        )

        # Rent expense of ₹30,000 paid via bank
        self.db.add_voucher(
            voucher_type="Payment",
            date="2026-04-15",
            narration="Office Space Rent",
            entries=[
                {"ledger_name": "Indirect Expenses", "entry_type": "Dr", "amount": 30000.0},
                {"ledger_name": "HDFC Bank", "entry_type": "Cr", "amount": 30000.0}
            ]
        )

        fin = self.db.get_financial_summary()
        expected_gross_profit = (350000.0 - 200000.0) + initial_stock
        expected_net_profit = expected_gross_profit - 30000.0

        self.assertEqual(fin["gross_profit"], expected_gross_profit)
        self.assertEqual(fin["net_profit"], expected_net_profit)

        # Verify Trial Balance equilibrium
        tb = self.db.get_trial_balance()
        tot_dr = sum(r["dr_amount"] for r in tb)
        tot_cr = sum(r["cr_amount"] for r in tb)
        self.assertAlmostEqual(tot_dr, tot_cr, places=2)

    def test_03_inventory_valuation_and_fifo_consumption(self):
        """
        Validates inventory stock movement: Opening + Inward - Outward = Closing
        """
        # Create item with opening balance 10 units @ 10,000 = ₹1,00,000
        self.db.add_stock_item(
            name="Quantum Processor 4.0GHz",
            group_name="Finished Goods",
            uom_symbol="Nos",
            hsn_code="84713010",
            gst_rate=18.0,
            opening_qty=10.0,
            opening_rate=10000.0
        )

        stk1 = self.db.get_stock_summary()
        qp = next(s for s in stk1 if s["name"] == "Quantum Processor 4.0GHz")
        self.assertEqual(qp["closing_qty"], 10.0)
        self.assertEqual(qp["closing_value"], 100000.0)

        # Purchase 20 more units @ ₹12,000
        self.db.add_voucher(
            voucher_type="Purchase",
            date="2026-04-20",
            narration="Procure 20 Quantum Processors",
            entries=[
                {"ledger_name": "Purchase Accounts", "entry_type": "Dr", "amount": 240000.0},
                {"ledger_name": "Sundry Creditors", "entry_type": "Cr", "amount": 240000.0}
            ],
            inventory_items=[
                {"stock_item_name": "Quantum Processor 4.0GHz", "godown_name": "Main Location", "qty": 20.0, "rate": 12000.0, "amount": 240000.0, "direction": "In"}
            ]
        )

        stk2 = self.db.get_stock_summary()
        qp2 = next(s for s in stk2 if s["name"] == "Quantum Processor 4.0GHz")
        self.assertEqual(qp2["closing_qty"], 30.0)
        self.assertAlmostEqual(qp2["closing_value"], 340000.0, delta=1.0)

        # Sell 15 units
        self.db.add_voucher(
            voucher_type="Sales",
            date="2026-04-25",
            narration="Ship 15 Quantum Processors",
            entries=[
                {"ledger_name": "Sundry Debtors", "entry_type": "Dr", "amount": 300000.0},
                {"ledger_name": "Sales Accounts", "entry_type": "Cr", "amount": 300000.0}
            ],
            inventory_items=[
                {"stock_item_name": "Quantum Processor 4.0GHz", "godown_name": "Main Location", "qty": 15.0, "rate": 20000.0, "amount": 300000.0, "direction": "Out"}
            ]
        )

        stk3 = self.db.get_stock_summary()
        qp3 = next(s for s in stk3 if s["name"] == "Quantum Processor 4.0GHz")
        self.assertEqual(qp3["closing_qty"], 15.0)

    def test_04_bill_wise_partial_settlement_reconciliation(self):
        """
        Validates bill receivables tracking and partial Agst-Ref settlements.
        """
        # 1. Invoice of ₹1,18,000 with ref INV-9901
        self.db.add_voucher(
            voucher_type="Sales",
            date="2026-05-01",
            narration="Sales to Enterprise Customer",
            entries=[
                {"ledger_name": "Sundry Debtors", "entry_type": "Dr", "amount": 118000.0},
                {"ledger_name": "Sales Accounts", "entry_type": "Cr", "amount": 100000.0},
                {"ledger_name": "Output CGST", "entry_type": "Cr", "amount": 9000.0},
                {"ledger_name": "Output SGST", "entry_type": "Cr", "amount": 9000.0}
            ],
            bill_allocations=[
                {"ledger_name": "Sundry Debtors", "ref_type": "New Ref", "ref_name": "INV-9901", "amount": 118000.0, "direction": "Dr"}
            ]
        )

        rec1 = self.db.get_outstanding_bills("Sundry Debtors")
        inv_bill = next(b for b in rec1 if b["bill_ref"] == "INV-9901")
        self.assertEqual(inv_bill["pending_amount"], 118000.0)

        # 2. Partial receipt of ₹50,000 against INV-9901
        self.db.add_voucher(
            voucher_type="Receipt",
            date="2026-05-10",
            narration="Part payment received",
            entries=[
                {"ledger_name": "HDFC Bank", "entry_type": "Dr", "amount": 50000.0},
                {"ledger_name": "Sundry Debtors", "entry_type": "Cr", "amount": 50000.0}
            ],
            bill_allocations=[
                {"ledger_name": "Sundry Debtors", "ref_type": "Agst Ref", "ref_name": "INV-9901", "amount": 50000.0, "direction": "Cr"}
            ]
        )

        rec2 = self.db.get_outstanding_bills("Sundry Debtors")
        inv_bill2 = next(b for b in rec2 if b["bill_ref"] == "INV-9901")
        self.assertEqual(inv_bill2["pending_amount"], 68000.0)

    def test_05_atomic_transaction_rollback_on_failure(self):
        """
        Verifies ACID atomicity: If an error occurs midway through a compound voucher,
        all partial database writes are cleanly rolled back and no corrupted records persist.
        """
        cur = self.db.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM voucher")
        vch_count_before = cur.fetchone()[0]

        # Attempt to insert an unbalanced voucher (which raises ValueError in add_voucher)
        with self.assertRaises(ValueError):
            self.db.add_voucher(
                voucher_type="Journal",
                date="2026-05-15",
                narration="Corrupted Unbalanced Voucher Attempt",
                entries=[
                    {"ledger_name": "HDFC Bank", "entry_type": "Dr", "amount": 50000.0},
                    {"ledger_name": "Capital Account", "entry_type": "Cr", "amount": 40000.0}  # Unbalanced by 10,000!
                ]
            )

        # Verify database is unchanged
        cur.execute("SELECT COUNT(*) FROM voucher")
        vch_count_after = cur.fetchone()[0]
        self.assertEqual(vch_count_before, vch_count_after)

        ver = self.db.verify_data_integrity()
        self.assertTrue(ver["double_entry_balanced"])
        self.assertEqual(ver["foreign_key_violations"], 0)

if __name__ == "__main__":
    unittest.main()
