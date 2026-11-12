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

    def test_average_cost_is_the_financial_years_weighted_average_of_receipts(self):
        self.db.add_ledger('Purchases', 'Purchase Accounts')
        self.db.add_ledger('Sales', 'Sales Accounts')
        self.db.add_stock_item('Toor', uom_symbol='Kg')

        def buy(date, qty, rate):
            amount = qty * rate
            self.db.add_voucher('Purchase', date, 'buy', [{'ledger_name': 'Purchases', 'entry_type': 'Dr', 'amount': amount},
                                                          {'ledger_name': 'Cash', 'entry_type': 'Cr', 'amount': amount}],
                                inventory_items=[{'stock_item_name': 'Toor', 'qty': qty, 'rate': rate, 'amount': amount, 'direction': 'In'}])

        def sell(date, qty):
            self.db.add_voucher('Sales', date, 'sell', [{'ledger_name': 'Cash', 'entry_type': 'Dr', 'amount': qty * 50},
                                                        {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': qty * 50}],
                                inventory_items=[{'stock_item_name': 'Toor', 'qty': qty, 'rate': 50, 'amount': qty * 50, 'direction': 'Out'}])
        buy('2026-04-01', 10, 10)
        sell('2026-04-02', 5)
        buy('2026-05-03', 5, 30)
        item = self.db.get_stock_item('Toor')
        # Everything received this year, 10 at 10 and 5 at 30, averages 16.67 whatever was sold in between.
        self.assertEqual((item['closing_qty'], item['closing_value']), (10.0, 166.67))
        # A new financial year starts from the closing stock at its closing rate, then weighs in the year's receipts.
        sell('2027-04-02', 4)                                                # year 2 opens with the 10 kg (166.67) ...
        buy('2027-05-01', 6, 30)                                             # ... plus 6 at 30: (166.67 + 180) / 16 = 21.667
        item = self.db.get_stock_item('Toor')
        self.assertEqual((item['closing_qty'], item['closing_value']), (12.0, 260.0))
        monthly = self.db.get_stock_item_monthly('Toor', '2026-04-01', '2027-03-31')
        self.assertEqual(monthly['months'][-1]['closing_value'], 166.67)
        april = monthly['months'][0]
        self.assertEqual((april['in_qty'], april['out_qty'], april['closing_qty']), (10.0, 5.0, 5.0))
        self.assertEqual(round(0 + april['in_value'] - april['out_value'], 2), april['closing_value'])   # columns reconcile
        with self.assertRaises(ValueError):
            self.db.get_stock_item_monthly('Nope')

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

    def test_schedule_iii_classifies_sub_groups_and_balances(self):
        self.db.add_ledger('Owner', 'Capital Account')
        self.db.add_ledger('Rent', 'Indirect Expenses')
        self.db.add_group('Retail Debtors', 'Sundry Debtors', is_revenue=False)
        self.db.add_ledger('Shop', 'Retail Debtors')
        self.db.add_ledger('Tax', 'Duties & Taxes')
        self.journal('Cash', 'Owner', 1000)
        self.journal('Rent', 'Cash', 40)
        self.journal('Shop', 'Tax', 25)
        sheet = self.db.get_schedule_iii_balance_sheet()
        self.assertTrue(sheet['is_balanced'], sheet)
        self.assertEqual(sheet['equity_and_liabilities']['shareholders_funds'], {'share_capital': 1000.0, 'reserves_and_surplus': -40.0, 'total': 960.0})
        self.assertEqual(sheet['equity_and_liabilities']['current_liabilities']['other_current_liabilities'], 25.0)
        self.assertEqual(sheet['assets']['current_assets']['trade_receivables'], 25.0)      # a sub-group of Sundry Debtors
        self.assertEqual(sheet['assets']['current_assets']['cash_and_bank_balances'], 960.0)

    def test_ratio_analysis_and_cash_summary_include_sub_groups(self):
        self.db.add_group('Retail Debtors', 'Sundry Debtors', is_revenue=False)
        self.db.add_group('Petty Cash', 'Cash-in-Hand', is_revenue=False)
        self.db.add_ledger('Shop', 'Retail Debtors')
        self.db.add_ledger('Drawer', 'Petty Cash')
        self.db.add_ledger('Owner', 'Capital Account')
        self.db.add_ledger('Tax', 'Duties & Taxes')
        self.journal('Shop', 'Tax', 50)
        self.journal('Drawer', 'Owner', 200)
        ratios = self.db.get_ratio_analysis()
        self.assertEqual(ratios['current_assets'], 250.0)          # debtors 50 + cash 200, both in sub-groups
        self.assertEqual(ratios['current_liabilities'], 50.0)
        self.assertEqual(ratios['current_ratio'], 5.0)
        self.assertEqual({r['name'] for r in self.db.get_cash_bank_summary()}, {'Cash', 'Drawer'})

    def test_group_summary_lists_groups_with_debit_and_credit_columns(self):
        self.db.add_ledger('Owner', 'Capital Account')
        self.db.add_ledger('Rent', 'Indirect Expenses')
        self.db.add_group('Petty Cash', 'Cash-in-Hand', is_revenue=False)
        self.db.add_ledger('Drawer', 'Petty Cash')
        self.journal('Cash', 'Owner', 1000)
        self.journal('Rent', 'Cash', 40)
        self.journal('Drawer', 'Cash', 100)
        top = {r['name']: (r['debit'], r['credit']) for r in self.db.get_group_summary()['rows']}
        self.assertEqual(top['Capital Account'], (0.0, 1000.0))
        self.assertEqual(top['Current Assets'], (960.0, 0.0))        # cash 860 + sub-group Petty Cash 100
        self.assertEqual(top['Indirect Expenses'], (40.0, 0.0))
        summary = self.db.get_group_summary()
        self.assertEqual(summary['total_debit'], summary['total_credit'])
        self.assertEqual({r['name']: r['debit'] for r in self.db.get_group_summary('Current Assets')['rows']}['Cash-in-Hand'], 960.0)
        inside = {r['name']: (r['kind'], r['debit']) for r in self.db.get_group_summary('Cash-in-Hand')['rows']}
        self.assertEqual(inside, {'Petty Cash': ('group', 100.0), 'Cash': ('ledger', 860.0)})
        with self.assertRaises(ValueError):
            self.db.get_group_summary('No such group')

    def test_day_book_lists_every_voucher_of_a_period_not_just_the_latest_hundred(self):
        for i in range(130):
            self.journal('Cash', 'Cash', 1, date='2026-04-05')
        default = self.db.get_day_book()
        self.assertEqual((default['total'], len(default['vouchers'])), (130, 100))
        period = self.db.get_day_book('2026-04-01', '2026-04-30')
        self.assertEqual((period['total'], len(period['vouchers'])), (130, 130))
        self.assertEqual(self.db.get_day_book('2026-05-01', '2026-05-31')['vouchers'], [])
        self.assertIn('Cash', period['vouchers'][0]['particulars'])

    def test_bills_report_days_overdue_from_due_date_at_the_as_of_date(self):
        self.db.add_ledger('Buyer', 'Sundry Debtors')
        vid = self.journal('Buyer', 'Cash', 1, date='2026-04-01')
        for ref, due in (('A1', '2026-04-20'), ('A2', None), ('A3', '2026-06-30')):
            self.db.conn.execute('INSERT INTO bill_allocation(voucher_id,ledger_name,ref_type,ref_name,amount,direction,due_date) VALUES(?,?,?,?,?,?,?)',
                                 (vid, 'Buyer', 'New Ref', ref, 100, 'Dr', due))
        self.db.conn.commit()
        bills = {b['bill_ref']: b for b in self.db.get_outstanding_bills('Sundry Debtors', as_of='2026-05-01')}
        self.assertEqual(bills['A1']['overdue_days'], 11)        # 1 May minus the 20 April due date
        self.assertEqual(bills['A2']['overdue_days'], 30)        # no due date: counted from the bill date, 1 April
        self.assertEqual(bills['A3']['overdue_days'], 0)         # not yet due
        self.assertEqual(bills['A1']['age_days'], 30)            # age still counts from the bill date

    def test_gross_profit_deducts_opening_stock_and_pl_screen_lists_every_part(self):
        self.db.add_stock_item('Toor', uom_symbol='Kg', opening_qty=10, opening_rate=10)       # opening stock 100
        self.db.add_ledger('Sales', 'Sales Accounts')
        self.db.add_ledger('Freight', 'Direct Expenses')
        self.db.add_ledger('Rent', 'Indirect Expenses')
        self.db.add_ledger('Interest', 'Indirect Incomes')
        self.db.add_voucher('Sales', '2026-04-02', 'sell', [{'ledger_name': 'Cash', 'entry_type': 'Dr', 'amount': 150},
                                                            {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': 150}],
                            inventory_items=[{'stock_item_name': 'Toor', 'qty': 5, 'rate': 30, 'amount': 150, 'direction': 'Out'}])
        self.journal('Freight', 'Cash', 20)
        self.journal('Rent', 'Cash', 10)
        self.journal('Cash', 'Interest', 5)
        fin = self.db.get_financial_summary()
        self.assertEqual((fin['opening_stock'], fin['closing_stock']), (100.0, 50.0))
        # Sales 150 + closing stock 50 - opening stock 100 - direct expenses 20 = 80; less rent 10, plus interest 5
        self.assertEqual(fin['gross_profit'], 80.0)
        self.assertEqual(fin['net_profit'], 75.0)
        self.assertEqual((fin['direct_expenses'], fin['indirect_expenses'], fin['indirect_incomes']), (20.0, 10.0, 5.0))

    def _party_sale(self, date, amount, number):
        self.db.add_voucher('Sales', date, 'sale', [{'ledger_name': 'Buyer', 'entry_type': 'Dr', 'amount': amount},
                                                    {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': amount}], voucher_number=number)

    def test_sales_register_totals_the_party_side_by_month_like_tallyprime(self):
        self.db.add_ledger('Buyer', 'Sundry Debtors')
        self.db.add_ledger('Sales', 'Sales Accounts')
        self.db.add_ledger('Freight', 'Indirect Incomes')
        self._party_sale('2026-04-05', 100, 'S1')
        self._party_sale('2026-04-20', 50, 'S2')
        self._party_sale('2026-06-01', 70, 'S3')
        # A sale with an extra credited charge: the register shows what the party owes (150), not the sales ledger side (100).
        self.db.add_voucher('Sales', '2026-05-02', 'sale', [{'ledger_name': 'Buyer', 'entry_type': 'Dr', 'amount': 150},
                                                            {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': 100},
                                                            {'ledger_name': 'Freight', 'entry_type': 'Cr', 'amount': 50}], voucher_number='S4')
        reg = self.db.get_voucher_register('SalesRegister', '2026-04-01', '2027-03-31')
        months = {m['month']: m for m in reg['months']}
        self.assertEqual(len(reg['months']), 12)
        self.assertEqual((months['2026-04']['count'], months['2026-04']['credit'], months['2026-04']['debit']), (2, 150.0, 0.0))
        self.assertEqual(months['2026-05']['credit'], 150.0)
        self.assertEqual(months['2026-06']['closing'], 370.0)          # running total of the credit column
        self.assertEqual(months['2026-07']['closing'], 370.0)          # months with no vouchers carry it forward
        self.assertEqual((reg['total_count'], reg['total_credit']), (4, 370.0))
        month = self.db.get_voucher_register('SalesRegister', '2026-04-01', '2027-03-31', month='2026-04')
        self.assertEqual([v['voucher_number'] for v in month['vouchers']], ['S1', 'S2'])
        # No period given: the financial year of the last voucher.
        self.assertEqual(self.db.get_voucher_register('SalesRegister')['from'], '2026-04-01')
        with self.assertRaises(ValueError):
            self.db.get_voucher_register('NoSuchRegister')

    def test_other_registers_count_vouchers_and_total_both_sides(self):
        self.journal('Cash', 'Cash', 25, date='2026-04-05')
        self.db.add_voucher('Payment', '2026-04-09', 'p', [{'ledger_name': 'Cash', 'entry_type': 'Cr', 'amount': 40},
                                                           {'ledger_name': 'Cash', 'entry_type': 'Dr', 'amount': 40}])
        reg = self.db.get_voucher_register('Payment Register', '2026-04-01', '2026-04-30')
        self.assertEqual([(m['count'], m['debit'], m['credit']) for m in reg['months']], [(1, 40.0, 40.0)])
        journal = self.db.get_voucher_register('JournalRegister', '2026-04-01', '2026-04-30')
        self.assertEqual((journal['total_count'], journal['total_debit']), (1, 25.0))

    def test_register_and_group_summary_reports_render_with_drill_down(self):
        from engine.runtime import TDLRuntime
        from engine.tdl_parser import TDLSymbolTable
        self.db.add_ledger('Buyer', 'Sundry Debtors')
        self.db.add_ledger('Sales', 'Sales Accounts')
        self._party_sale('2026-04-05', 100, 'S1')
        runtime = TDLRuntime(TDLSymbolTable(), self.db)
        report = runtime.render_report('SalesRegister')
        lines = report['parts'][0]['lines']
        april = next(l for l in lines if l.get('drill') == 'month=2026-04')
        self.assertEqual([f['display_value'] for f in april['fields']][:2], ['April 2026', '1'])
        drilled = runtime.render_report('SalesRegister', month='2026-04')['parts'][0]['lines']
        self.assertEqual(drilled[1]['drill'], '')                                  # back row
        self.assertEqual(drilled[2]['fields'][3]['display_value'], 'S1')
        summary = runtime.render_report('GroupSummary')['parts'][0]['lines']
        self.assertTrue(any(l.get('drill', '').startswith('group=') for l in summary))
        inside = runtime.render_report('GroupSummary', group='Current Assets')['parts'][0]['lines']
        self.assertEqual(inside[1]['drill'], '')

    def test_trial_balance_is_by_group_with_drill_down_and_a_flat_view(self):
        from engine.runtime import TDLRuntime
        from engine.tdl_parser import TDLSymbolTable
        self.db.add_ledger('Owner', 'Capital Account')
        self.db.add_ledger('Rent', 'Indirect Expenses')
        self.db.add_group('Petty Cash', 'Cash-in-Hand', is_revenue=False)
        self.db.add_ledger('Drawer', 'Petty Cash')
        self.journal('Cash', 'Owner', 1000)
        self.journal('Rent', 'Cash', 40)
        self.journal('Drawer', 'Cash', 100)
        runtime = TDLRuntime(TDLSymbolTable(), self.db)
        lines = runtime.render_report('TrialBalance')['parts'][0]['lines']
        rows = {l['fields'][0]['display_value']: [f['display_value'] for f in l['fields']] for l in lines}
        self.assertEqual(rows['Capital Account'][1:], ['-', '1,000.00'])
        self.assertEqual(rows['Current Assets'][1:], ['960.00', '-'])
        self.assertEqual(rows['Indirect Expenses'][1:], ['40.00', '-'])
        self.assertEqual(rows['Grand Total'][1], rows['Grand Total'][2])                 # both sides equal
        self.assertEqual(next(l for l in lines if l['fields'][0]['display_value'] == 'Current Assets').get('drill'), 'group=Current%20Assets')
        inside = runtime.render_report('TrialBalance', group='Cash-in-Hand')['parts'][0]['lines']
        self.assertEqual(inside[1]['drill'], '')
        self.assertIn('Petty Cash', [l['fields'][0]['display_value'] for l in inside])
        flat = runtime.render_report('TrialBalance', view='ledgers')['parts'][0]['lines']
        self.assertIn('Drawer', [l['fields'][0]['display_value'] for l in flat])
        self.assertEqual(runtime.render_report('BalanceSheet')['title'], 'Balance Sheet')   # not captured by the trial balance

    def test_stock_summary_report_hides_nil_items_and_drills_into_monthly_summary(self):
        from engine.runtime import TDLRuntime
        from engine.tdl_parser import TDLSymbolTable
        self.db.add_stock_item('Toor', uom_symbol='Kg')
        self.db.add_stock_item('Idle', uom_symbol='Kg')
        self.db.add_ledger('Purchases', 'Purchase Accounts')
        self.db.add_voucher('Purchase', '2026-04-01', 'buy', [{'ledger_name': 'Purchases', 'entry_type': 'Dr', 'amount': 100},
                                                              {'ledger_name': 'Cash', 'entry_type': 'Cr', 'amount': 100}],
                            inventory_items=[{'stock_item_name': 'Toor', 'qty': 10, 'rate': 10, 'amount': 100, 'direction': 'In'}])
        runtime = TDLRuntime(TDLSymbolTable(), self.db)
        lines = runtime.render_report('StockSummary')['parts'][0]['lines']
        names = [l['fields'][0]['display_value'] for l in lines]
        self.assertIn('Toor', names)
        self.assertNotIn('Idle', names)
        self.assertTrue(any(n.startswith('► Show') for n in names))
        self.assertEqual(lines[-1]['fields'][3]['display_value'], '100.00')
        toor = next(l for l in lines if l['fields'][0]['display_value'] == 'Toor')
        self.assertEqual(toor['drill'], 'item=Toor')
        everything = runtime.render_report('StockSummary', view='all')['parts'][0]['lines']
        self.assertIn('Idle', [l['fields'][0]['display_value'] for l in everything])
        monthly = runtime.render_report('StockSummary', item='Toor')['parts'][0]['lines']
        self.assertEqual(monthly[1]['drill'], '')
        self.assertEqual(monthly[3]['fields'][0]['display_value'], 'April 2026')

    def test_opening_stock_without_matching_capital_shows_difference_in_opening_balances(self):
        self.db.add_stock_item('Toor', uom_symbol='Kg', opening_qty=10, opening_rate=10)          # 100 of stock, no capital behind it
        self.db.add_ledger('Owner', 'Capital Account')
        sheet = self.db.get_balance_sheet()
        self.assertEqual({r['name']: r['amount'] for r in sheet['liabilities']}['Difference in Opening Balances'], 100.0)
        self.assertEqual(sheet['total_liabilities'], sheet['total_assets'])
        self.assertTrue(self.db.get_schedule_iii_balance_sheet()['is_balanced'])
        self.db.conn.execute("UPDATE ledger SET opening_balance = 100, opening_balance_type = 'Cr' WHERE name = 'Owner'")
        self.db.conn.commit()
        self.assertEqual(self.db.opening_balance_difference(), 0)                                  # capital now matches the stock
        self.assertNotIn('Difference in Opening Balances', [r['name'] for r in self.db.get_balance_sheet()['liabilities']])

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
