"""Voucher entry rules that follow TallyPrime: numbering, dates, existing masters, voucher-type rules, bill references."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402


def dr(name, amount):
    return {'ledger_name': name, 'entry_type': 'Dr', 'amount': amount}


def cr(name, amount):
    return {'ledger_name': name, 'entry_type': 'Cr', 'amount': amount}


class EntryRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EnterpriseAccountingDB(str(Path(self.tmp.name) / 'books.db'), seed_demo=False)
        self.db.select_company(self.db.create_company(name='Entry Co', financial_year_from='2026-04-01', financial_year_to='2027-03-31'))
        self.db.add_ledger('Cash', 'Cash-in-Hand')
        self.db.add_ledger('Bank', 'Bank Accounts')
        self.db.add_ledger('Buyer', 'Sundry Debtors')
        self.db.add_ledger('Sales', 'Sales Accounts')
        self.db.add_ledger('Rent', 'Indirect Expenses')
        self.db.conn.execute("UPDATE ledger SET maintain_bill_by_bill = 1 WHERE name = 'Buyer'")
        self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def number(self, vid):
        return self.db.conn.execute('SELECT voucher_number FROM voucher WHERE id=?', (vid,)).fetchone()[0]

    def strict(self):
        self.db.set_setting('strict_voucher_entry', True)

    # numbering ------------------------------------------------------------------------------------
    def test_next_number_follows_the_series_keeping_prefix_suffix_and_padding(self):
        for n in ('INV-0098/26-27', 'INV-0099/26-27'):
            self.db.add_voucher('Sales', '2026-05-01', 'x', [dr('Buyer', 10), cr('Sales', 10)], voucher_number=n)
        self.assertEqual(self.number(self.db.add_voucher('Sales', '2026-05-02', 'x', [dr('Buyer', 10), cr('Sales', 10)])), 'INV-0100/26-27')
        # The highest number wins, not the newest by date.
        for n in ('7', '9', '8'):
            self.db.add_voucher('Journal', '2026-05-03', 'x', [dr('Cash', 1), cr('Rent', 1)], voucher_number=n)
        self.assertEqual(self.db.next_voucher_number('Journal', '2026-06-01'), '10')

    def test_a_new_financial_year_restarts_the_series_and_moves_the_year_suffix(self):
        self.db.add_voucher('Sales', '2026-05-01', 'x', [dr('Buyer', 10), cr('Sales', 10)], voucher_number='INV-0450/26-27')
        self.assertEqual(self.db.next_voucher_number('Sales', '2027-04-02'), 'INV-0001/27-28')
        self.db.add_voucher('Payment', '2026-05-01', 'x', [dr('Rent', 5), cr('Cash', 5)], voucher_number='5490')
        self.assertEqual(self.db.next_voucher_number('Payment', '2027-04-02'), '1')
        self.assertIsNone(self.db.next_voucher_number('Contra', '2026-05-01'))      # nothing to follow: the app's default applies

    def test_the_same_number_may_repeat_across_years_but_not_within_one(self):
        self.db.add_voucher('Payment', '2026-05-01', 'x', [dr('Rent', 5), cr('Cash', 5)], voucher_number='12')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.db.add_voucher('Payment', '2026-06-01', 'x', [dr('Rent', 5), cr('Cash', 5)], voucher_number='12')
        self.db.add_voucher('Payment', '2027-05-01', 'x', [dr('Rent', 5), cr('Cash', 5)], voucher_number='12')   # next year
        self.db.add_voucher('Receipt', '2026-06-01', 'x', [dr('Cash', 5), cr('Buyer', 5)], voucher_number='12')  # other type

    # dates ----------------------------------------------------------------------------------------
    def test_dates_must_be_real_iso_dates(self):
        for bad in ('30-10-2026', '2026-13-01', '2026-02-30', '20261030', 'tomorrow'):
            with self.assertRaisesRegex(ValueError, 'Invalid voucher date'):
                self.db.add_voucher('Journal', bad, 'x', [dr('Cash', 1), cr('Rent', 1)])

    # strict mode (companies imported from TallyPrime) ---------------------------------------------
    def test_unknown_ledgers_are_created_only_outside_strict_mode(self):
        self.db.add_voucher('Journal', '2026-05-01', 'x', [dr('Brand New', 1), cr('Rent', 1)])          # legacy convenience
        self.assertTrue(self.db.conn.execute("SELECT 1 FROM ledger WHERE name='Brand New'").fetchone())
        self.strict()
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.db.add_voucher('Journal', '2026-05-01', 'x', [dr('Another New', 1), cr('Rent', 1)])
        self.assertFalse(self.db.conn.execute("SELECT 1 FROM ledger WHERE name='Another New'").fetchone())
        with self.assertRaisesRegex(ValueError, "Stock item"):
            self.db.add_voucher('Sales', '2026-05-01', 'x', [dr('Buyer', 5), cr('Sales', 5)],
                                inventory_items=[{'stock_item_name': 'Ghost', 'qty': 1, 'rate': 5, 'amount': 5, 'direction': 'Out'}])

    def test_voucher_type_rules_for_cash_and_bank(self):
        self.strict()
        self.db.add_voucher('Payment', '2026-05-01', 'x', [dr('Rent', 5), cr('Cash', 5)])
        self.db.add_voucher('Receipt', '2026-05-01', 'x', [dr('Bank', 5), cr('Buyer', 5)])
        self.db.add_voucher('Contra', '2026-05-01', 'x', [dr('Bank', 5), cr('Cash', 5)])
        with self.assertRaisesRegex(ValueError, 'Payment voucher must credit'):
            self.db.add_voucher('Payment', '2026-05-01', 'x', [dr('Cash', 5), cr('Buyer', 5)])
        with self.assertRaisesRegex(ValueError, 'Receipt voucher must debit'):
            self.db.add_voucher('Receipt', '2026-05-01', 'x', [dr('Buyer', 5), cr('Sales', 5)])
        with self.assertRaisesRegex(ValueError, 'Contra voucher can only'):
            self.db.add_voucher('Contra', '2026-05-01', 'x', [dr('Cash', 5), cr('Buyer', 5)])

    def test_voucher_date_cannot_precede_the_books_beginning(self):
        self.strict()
        with self.assertRaisesRegex(ValueError, 'before the books beginning'):
            self.db.add_voucher('Journal', '2025-01-01', 'x', [dr('Cash', 1), cr('Rent', 1)])

    def test_bill_wise_parties_get_automatic_references(self):
        self.strict()
        sale = self.db.add_voucher('Sales', '2026-05-01', 'x', [dr('Buyer', 100), cr('Sales', 100)], voucher_number='INV-1')
        receipt = self.db.add_voucher('Receipt', '2026-05-02', 'x', [dr('Bank', 40), cr('Buyer', 40)])
        bills = lambda vid: [tuple(r) for r in self.db.conn.execute(
            'SELECT ledger_name, ref_type, ref_name, amount, direction FROM bill_allocation WHERE voucher_id=?', (vid,))]
        self.assertEqual(bills(sale), [('Buyer', 'New Ref', 'INV-1', 100.0, 'Dr')])
        self.assertEqual(bills(receipt), [('Buyer', 'On Account', '', 40.0, 'Cr')])
        pending = {b['bill_ref']: b['pending_amount'] for b in self.db.get_outstanding_bills('Sundry Debtors')}
        self.assertEqual(pending, {'INV-1': 100.0})          # the on-account receipt is not a bill, as in TallyPrime's lists

    # inventory invoices ---------------------------------------------------------------------------
    def item(self, qty, rate, amount, name='Toor'):
        return [{'stock_item_name': name, 'qty': qty, 'rate': rate, 'amount': amount}]

    def test_item_lines_are_checked_against_quantity_rate_and_the_ledger_postings(self):
        self.db.add_stock_item('Toor', uom_symbol='Kg')
        self.strict()
        sale = lambda amount, items: self.db.add_voucher('Sales', '2026-05-01', 'x', [dr('Buyer', amount), cr('Sales', amount)], inventory_items=items)
        sale(4000, self.item(10, 400, 4000))
        sale(1845100, self.item(1085, 1700.55, 1845100))                    # a two-decimal rate rounds: within tolerance
        sale(100, self.item(0, 0, 100))                                     # a service line has no quantity
        with self.assertRaisesRegex(ValueError, 'does not match quantity x rate'):
            sale(9999, self.item(10, 400, 9999))
        with self.assertRaisesRegex(ValueError, 'cannot be negative'):
            sale(100, self.item(-5, 20, 100))
        with self.assertRaisesRegex(ValueError, 'must equal the sales/purchase ledger postings'):
            self.db.add_voucher('Sales', '2026-05-01', 'x', [dr('Buyer', 5000), cr('Sales', 5000)], inventory_items=self.item(10, 400, 4000))

    def test_stock_moves_the_right_way_for_every_voucher_type(self):
        self.db.add_stock_item('Toor', uom_symbol='Kg')
        self.db.add_ledger('Purchases', 'Purchase Accounts')
        self.db.add_ledger('Supplier', 'Sundry Creditors')
        direction = {}
        for vtype, entries in (('Purchase', [cr('Supplier', 40), dr('Purchases', 40)]), ('Sales', [dr('Buyer', 40), cr('Sales', 40)]),
                               ('Credit Note', [cr('Buyer', 40), dr('Sales', 40)]), ('Debit Note', [dr('Supplier', 40), cr('Purchases', 40)])):
            vid = self.db.add_voucher(vtype, '2026-05-01', 'x', entries, inventory_items=[{'stock_item_name': 'Toor', 'qty': 4, 'rate': 10, 'amount': 40}])
            direction[vtype] = self.db.conn.execute('SELECT direction FROM voucher_inventory WHERE voucher_id=?', (vid,)).fetchone()[0]
        self.assertEqual(direction, {'Purchase': 'In', 'Sales': 'Out', 'Credit Note': 'In', 'Debit Note': 'Out'})   # a credit note is a sales return

    # alteration, cancellation, deletion ----------------------------------------------------------
    def bills(self, vid):
        return [tuple(r) for r in self.db.conn.execute(
            'SELECT ref_type, ref_name, amount, direction FROM bill_allocation WHERE voucher_id=?', (vid,))]

    def test_altering_a_voucher_keeps_its_bill_references_unless_the_amount_changes(self):
        self.strict()
        self.db.add_ledger('Cash2', 'Cash-in-Hand')
        sale = self.db.add_voucher('Sales', '2026-05-01', 'x', [dr('Buyer', 100), cr('Sales', 100)], voucher_number='INV-1')
        self.db.conn.execute("UPDATE bill_allocation SET ref_type='Agst Ref', ref_name='OLD-7' WHERE voucher_id=?", (sale,))
        self.db.conn.commit()
        self.db.alter_voucher(sale, '2026-05-01', 'new narration', [dr('Buyer', 100), cr('Sales', 100)])
        self.assertEqual(self.bills(sale), [('Agst Ref', 'OLD-7', 100.0, 'Dr')])                 # was wiped before
        self.db.alter_voucher(sale, '2026-05-01', 'bigger', [dr('Buyer', 150), cr('Sales', 150)])
        self.assertEqual(self.bills(sale), [('New Ref', 'INV-1', 150.0, 'Dr')])                  # amount changed: automatic reference

    def test_altering_follows_the_same_entry_rules_and_stock_direction(self):
        self.strict()
        self.db.add_stock_item('Toor', uom_symbol='Kg')
        self.db.add_ledger('Purchases', 'Purchase Accounts')
        self.db.add_ledger('Supplier', 'Sundry Creditors')
        buy = self.db.add_voucher('Purchase', '2026-05-01', 'x', [cr('Supplier', 40), dr('Purchases', 40)],
                                  inventory_items=[{'stock_item_name': 'Toor', 'qty': 4, 'rate': 10, 'amount': 40}])
        self.db.alter_voucher(buy, '2026-05-01', 'x', [cr('Supplier', 50), dr('Purchases', 50)],
                              inventory_items=[{'stock_item_name': 'Toor', 'qty': 5, 'rate': 10, 'amount': 50}])
        self.assertEqual(self.db.conn.execute('SELECT direction, qty FROM voucher_inventory WHERE voucher_id=?', (buy,)).fetchone()[:], ('In', 5.0))
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            self.db.alter_voucher(buy, '2026-05-01', 'x', [cr('Supplier', 50), dr('Ghost', 50)])
        with self.assertRaisesRegex(ValueError, 'Invalid voucher date'):
            self.db.alter_voucher(buy, '1-5-2026', 'x', [cr('Supplier', 50), dr('Purchases', 50)])

    def test_the_edit_trail_keeps_before_and_after_and_survives_deletion(self):
        vid = self.db.add_voucher('Payment', '2026-05-01', 'first', [dr('Rent', 5), cr('Cash', 5)], voucher_number='P1')
        self.db.alter_voucher(vid, '2026-05-01', 'second', [dr('Rent', 7), cr('Cash', 7)])
        history = [h for h in self.db.get_voucher_version_history(vid) if h.get('details_json')]
        self.assertEqual([h['action'] for h in history], ['CREATE', 'ALTER'])        # the original snapshot rides on the creation entry
        self.assertEqual(history[0]['details_json']['narration'], 'first')
        self.assertEqual(history[1]['details_json']['total_amount'], 7.0)
        changes = next(d for d in self.db.get_voucher_diff(vid)['diffs'] if d['action'] == 'ALTER')['changes']
        self.assertEqual(changes['narration'], {'before': 'first', 'after': 'second'})
        self.db.cancel_voucher(vid)
        self.assertEqual(self.db.get_voucher(vid)['is_cancelled'], 1)
        self.db.delete_voucher(vid)
        gone = [h for h in self.db.get_voucher_version_history(vid) if h.get('details_json')]
        self.assertEqual(gone[-1]['action'], 'DELETE')
        self.assertEqual(gone[-1]['details_json']['entries'], [['Cash', 'Cr', 7.0], ['Rent', 'Dr', 7.0]])   # the content is not lost

    def test_one_vouchers_history_does_not_mix_in_another_with_the_same_number(self):
        a = self.db.add_voucher('Payment', '2026-05-01', 'a', [dr('Rent', 5), cr('Cash', 5)], voucher_number='9')
        b = self.db.add_voucher('Receipt', '2026-05-01', 'b', [dr('Cash', 5), cr('Buyer', 5)], voucher_number='9')
        self.db.alter_voucher(a, '2026-05-01', 'a2', [dr('Rent', 6), cr('Cash', 6)])
        self.db.alter_voucher(b, '2026-05-01', 'b2', [dr('Cash', 8), cr('Buyer', 8)])
        for vid, total in ((a, 6.0), (b, 8.0)):
            rows = [h for h in self.db.get_voucher_version_history(vid) if h.get('details_json')]
            self.assertEqual({h['details_json']['voucher_id'] for h in rows}, {vid})
            self.assertEqual(rows[-1]['details_json']['total_amount'], total)

    # godowns --------------------------------------------------------------------------------------
    def test_godowns_must_exist_and_the_godown_summary_adds_up_to_the_item_totals(self):
        self.db.add_stock_item('Toor', uom_symbol='Kg')
        self.db.set_negative_stock_policy(True)
        # Legacy company: a godown named on a line is not rejected, but it is still listed so the totals reconcile.
        self.db.add_voucher('Sales', '2026-05-01', 'x', [dr('Buyer', 40), cr('Sales', 40)],
                            inventory_items=[{'stock_item_name': 'Toor', 'qty': 4, 'rate': 10, 'amount': 40, 'godown_name': 'Loose Godown'}])
        summary = {g['godown_name']: g for g in self.db.get_godown_summary()}
        self.assertEqual(summary['Loose Godown']['items'], [{'item_name': 'Toor', 'qty': -4.0}])      # negative stock is listed
        self.assertFalse(summary['Loose Godown']['in_master'])
        self.assertEqual(sum(g['total_qty'] for g in summary.values()), self.db.get_stock_item('Toor')['closing_qty'])
        self.strict()
        line = {'stock_item_name': 'Toor', 'qty': 1, 'rate': 10, 'amount': 10, 'godown_name': 'Shed'}
        with self.assertRaisesRegex(ValueError, "Godown 'Shed' does not exist"):
            self.db.add_voucher('Sales', '2026-05-02', 'x', [dr('Buyer', 10), cr('Sales', 10)], inventory_items=[line])
        self.db.add_godown('Shed', 'Back yard')
        self.db.add_voucher('Sales', '2026-05-02', 'x', [dr('Buyer', 10), cr('Sales', 10)], inventory_items=[line])
        summary = {g['godown_name']: g for g in self.db.get_godown_summary()}
        self.assertTrue(summary['Shed']['in_master'])
        self.assertEqual(sum(g['total_qty'] for g in summary.values()), self.db.get_stock_item('Toor')['closing_qty'])

    # bank statement import -----------------------------------------------------------------------
    def test_statement_credit_lines_reconcile_even_though_their_debit_column_says_zero(self):
        receipt = self.db.add_voucher('Receipt', '2026-05-10', 'x', [dr('Bank', 500), cr('Buyer', 500)])
        payment = self.db.add_voucher('Payment', '2026-05-12', 'x', [dr('Rent', 300), cr('Bank', 300)])
        csv = ("Date,Description,Debit,Credit\n"
               "2026-05-10,Customer receipt,0.0,500.00\n"              # a credit line: its Debit column is 0.0
               "2026-05-12,Rent paid,300.00,0.0\n")
        result = self.db.auto_reconcile_statement_csv('Bank', csv)
        self.assertEqual(result['reconciled_count'], 2)
        status = {r[0]: r[1] for r in self.db.conn.execute('SELECT voucher_id, status FROM bank_reconciliation')}
        self.assertEqual((status[receipt], status[payment]), ('Reconciled', 'Reconciled'))

    def test_gst_registration_numbering_still_works_when_a_series_exists(self):
        self.db.add_gst_registration if hasattr(self.db, 'add_gst_registration') else None
        self.db.add_voucher('Sales', '2026-05-01', 'x', [dr('Buyer', 10), cr('Sales', 10)], voucher_number='INV-7')
        reg = self.db.conn.execute('INSERT INTO gst_registration(company_id,name,gstin,state_code) VALUES(?,?,?,?)',
                                   (self.db.active_company_id, 'Main', '29AABCA9999Z1Z8', '29')).lastrowid
        self.db.conn.commit()
        vid = self.db.add_voucher('Sales', '2026-05-02', 'x', [dr('Buyer', 10), cr('Sales', 10)], gst_registration_id=reg)
        self.assertEqual(self.number(vid), f'R{reg}-S-0001')          # registration-scoped numbering is its own series


if __name__ == '__main__':
    unittest.main()
