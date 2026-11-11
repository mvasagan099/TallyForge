"""Report behaviours checked against a real TallyPrime company, reproduced on small synthetic books."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402


class ReportParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EnterpriseAccountingDB(str(Path(self.tmp.name) / 'books.db'), seed_demo=False)
        self.db.select_company(self.db.create_company(name='Parity Co'))
        self.db.add_ledger('Cash', 'Cash-in-Hand')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def journal(self, dr, cr, amount, date='2026-04-05'):
        return self.db.add_voucher('Journal', date, 'x', [{'ledger_name': dr, 'entry_type': 'Dr', 'amount': amount},
                                                          {'ledger_name': cr, 'entry_type': 'Cr', 'amount': amount}])

    def test_indexes_exist_so_reports_do_not_scan_every_entry(self):
        names = {r[0] for r in self.db.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for expected in ('idx_voucher_entry_ledger', 'idx_voucher_entry_voucher', 'idx_voucher_date',
                         'idx_bill_allocation_ledger', 'idx_voucher_inventory_item'):
            self.assertIn(expected, names)
        self.db._ensure_indexes()          # idempotent on an existing company file

    def test_expenses_in_sub_groups_count_towards_profit(self):
        self.db.add_group('Staff Costs', 'Indirect Expenses', is_revenue=True)
        self.db.add_ledger('Wages', 'Staff Costs')
        self.db.add_ledger('Rent', 'Indirect Expenses')
        self.journal('Wages', 'Cash', 100)
        self.journal('Rent', 'Cash', 40)
        fin = self.db.get_financial_summary()
        self.assertEqual(fin['net_profit'], -140.0)        # before the fix only the 40 in the top group counted

    def test_negative_stock_is_valued_and_stock_journals_move_stock(self):
        self.db.add_stock_item('Toor', uom_symbol='Kg')
        self.db.add_ledger('Purchases', 'Purchase Accounts')
        self.db.add_ledger('Sales', 'Sales Accounts')
        self.db.add_voucher('Purchase', '2026-04-01', 'buy', [{'ledger_name': 'Purchases', 'entry_type': 'Dr', 'amount': 100},
                                                              {'ledger_name': 'Cash', 'entry_type': 'Cr', 'amount': 100}],
                            inventory_items=[{'stock_item_name': 'Toor', 'qty': 10, 'rate': 10, 'amount': 100, 'direction': 'In'}])
        self.db.set_negative_stock_policy(True)
        self.db.add_voucher('Sales', '2026-04-02', 'sell', [{'ledger_name': 'Cash', 'entry_type': 'Dr', 'amount': 300},
                                                            {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': 300}],
                            inventory_items=[{'stock_item_name': 'Toor', 'qty': 15, 'rate': 20, 'amount': 300, 'direction': 'Out'}])
        item = self.db.get_stock_item('Toor')
        self.assertEqual(item['closing_qty'], -5.0)
        self.assertEqual(item['closing_value'], -50.0)      # negative stock at the average inward rate, not zero
        # A Stock Journal does not post to ledgers (stored as optional) but it moves stock.
        cur = self.db.conn.execute("INSERT INTO voucher(voucher_type,voucher_number,date,narration,total_amount,is_optional,company_id) "
                                   "VALUES('Stock Journal','SJ/1','2026-04-03','',0,1,?)", (self.db.active_company_id,))
        self.db.conn.execute("INSERT INTO voucher_inventory(voucher_id,stock_item_name,godown_name,qty,rate,amount,direction) "
                             "VALUES(?,?,?,?,?,?,?)", (cur.lastrowid, 'Toor', 'Main Location', 7, 10, 70, 'In'))
        self.db.conn.commit()
        self.assertEqual(self.db.get_stock_item('Toor')['closing_qty'], 2.0)
        # ... but an order voucher (also optional) does not.
        cur = self.db.conn.execute("INSERT INTO voucher(voucher_type,voucher_number,date,narration,total_amount,is_optional,company_id) "
                                   "VALUES('Purchase Order','PO/1','2026-04-04','',0,1,?)", (self.db.active_company_id,))
        self.db.conn.execute("INSERT INTO voucher_inventory(voucher_id,stock_item_name,godown_name,qty,rate,amount,direction) "
                             "VALUES(?,?,?,?,?,?,?)", (cur.lastrowid, 'Toor', 'Main Location', 1000, 10, 10000, 'In'))
        self.db.conn.commit()
        self.assertEqual(self.db.get_stock_item('Toor')['closing_qty'], 2.0)

    def test_average_cost_is_a_running_average_in_date_order(self):
        self.db.add_stock_item('Toor', uom_symbol='Kg')
        self.db.add_ledger('Purchases', 'Purchase Accounts')
        self.db.add_ledger('Sales', 'Sales Accounts')

        def buy(date, qty, rate):
            amount = qty * rate
            self.db.add_voucher('Purchase', date, 'buy', [{'ledger_name': 'Purchases', 'entry_type': 'Dr', 'amount': amount},
                                                          {'ledger_name': 'Cash', 'entry_type': 'Cr', 'amount': amount}],
                                inventory_items=[{'stock_item_name': 'Toor', 'qty': qty, 'rate': rate, 'amount': amount, 'direction': 'In'}])
        buy('2026-04-01', 10, 10)
        self.db.add_voucher('Sales', '2026-04-02', 'sell', [{'ledger_name': 'Cash', 'entry_type': 'Dr', 'amount': 100},
                                                            {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': 100}],
                            inventory_items=[{'stock_item_name': 'Toor', 'qty': 5, 'rate': 20, 'amount': 100, 'direction': 'Out'}])
        buy('2026-04-03', 5, 30)
        item = self.db.get_stock_item('Toor')
        self.assertEqual(item['closing_qty'], 10.0)
        self.assertEqual(item['closing_value'], 200.0)      # 5 left at 10 + 5 bought at 30; the overall average gave 166.67

    def test_negative_stock_is_valued_at_this_years_average_purchase_rate(self):
        self.db.add_stock_item('Toor', uom_symbol='Kg')
        self.db.add_ledger('Purchases', 'Purchase Accounts')
        self.db.add_ledger('Sales', 'Sales Accounts')
        self.db.set_negative_stock_policy(True)

        def move(kind, date, qty, rate):
            amount = qty * rate
            if kind == 'Purchase':
                lines = [{'ledger_name': 'Purchases', 'entry_type': 'Dr', 'amount': amount}, {'ledger_name': 'Cash', 'entry_type': 'Cr', 'amount': amount}]
            else:
                lines = [{'ledger_name': 'Cash', 'entry_type': 'Dr', 'amount': amount}, {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': amount}]
            self.db.add_voucher(kind, date, 'x', lines, inventory_items=[{'stock_item_name': 'Toor', 'qty': qty, 'rate': rate, 'amount': amount,
                                                                          'direction': 'In' if kind == 'Purchase' else 'Out'}])
        move('Purchase', '2026-02-01', 10, 10)      # last financial year, sold out before 1 April
        move('Sales', '2026-03-01', 10, 20)
        move('Purchase', '2026-05-01', 10, 30)
        move('Sales', '2026-05-02', 15, 40)
        item = self.db.get_stock_item('Toor')
        self.assertEqual(item['closing_qty'], -5.0)
        self.assertEqual(item['closing_value'], -150.0)   # 5 short at this year's purchase rate (30); the all-time average (20) gave -100

    def test_balance_sheet_lists_groups_not_every_ledger_and_balances(self):
        self.db.add_ledger('Owner', 'Capital Account')
        self.db.add_ledger('Rent', 'Indirect Expenses')
        self.journal('Cash', 'Owner', 1000)
        self.journal('Rent', 'Cash', 40)
        sheet = self.db.get_balance_sheet()
        self.assertEqual({r['name']: r['amount'] for r in sheet['liabilities']}, {'Capital Account': 1000.0, 'Profit & Loss A/c': -40.0})
        self.assertEqual({r['name']: r['amount'] for r in sheet['assets']}, {'Current Assets': 960.0})   # income/expense ledgers are not listed
        self.assertEqual(sheet['total_liabilities'], sheet['total_assets'])
        # A ledger without a group is the Profit & Loss A/c: it joins that line instead of being a group of its own.
        self.db.add_group('Primary (ungrouped ledgers)', 'Primary', is_revenue=False)
        self.db.add_ledger('Profit & Loss A/c', 'Primary (ungrouped ledgers)')
        self.journal('Cash', 'Profit & Loss A/c', 10)
        sheet = self.db.get_balance_sheet()
        self.assertEqual({r['name']: r['amount'] for r in sheet['liabilities']}['Profit & Loss A/c'], -30.0)
        self.assertEqual(sheet['total_liabilities'], sheet['total_assets'])

    def test_outstanding_bills_are_classified_by_balance_side_like_tallyprime(self):
        self.db.add_ledger('Buyer', 'Sundry Debtors')
        self.db.add_ledger('Supplier', 'Sundry Creditors')
        vid = self.journal('Buyer', 'Supplier', 1)

        def bill(ledger, ref_type, ref, direction, amount):
            self.db.conn.execute('INSERT INTO bill_allocation(voucher_id,ledger_name,ref_type,ref_name,amount,direction) VALUES(?,?,?,?,?,?)',
                                 (vid, ledger, ref_type, ref, amount, direction))
        bill('Buyer', 'New Ref', 'A1', 'Dr', 100)                 # debtor invoice -> receivable
        bill('Supplier', 'New Ref', 'B1', 'Cr', 500)              # supplier bill -> payable ...
        bill('Supplier', 'Agst Ref', 'B1', 'Dr', 200)             # ... part paid: 300 pending
        bill('Supplier', 'New Ref', 'C1', 'Dr', 70)               # a creditor with a DEBIT bill is a receivable
        bill('Buyer', 'On Account', '', 'Cr', 999)                # nameless on-account items are not bills
        self.db.conn.commit()
        receivable = {(b['party_name'], b['bill_ref']): b['pending_amount'] for b in self.db.get_outstanding_bills('Sundry Debtors')}
        payable = {(b['party_name'], b['bill_ref']): b['pending_amount'] for b in self.db.get_outstanding_bills('Sundry Creditors')}
        self.assertEqual(receivable, {('Buyer', 'A1'): 100.0, ('Supplier', 'C1'): 70.0})
        self.assertEqual(payable, {('Supplier', 'B1'): 300.0})


if __name__ == '__main__':
    unittest.main()
