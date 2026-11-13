"""Bank reconciliation statement and statement matching (synthetic books only)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import bank_recon  # noqa: E402
from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402


class BankReconTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EnterpriseAccountingDB(str(Path(self.tmp.name) / 'books.db'), seed_demo=False)
        self.db.select_company(self.db.create_company(name='Bank Co', state='Tamil Nadu', state_code='33'))
        for name, group in (('Bank', 'Bank Accounts'), ('Cash', 'Cash-in-Hand'), ('Buyer', 'Sundry Debtors'), ('Rent', 'Indirect Expenses')):
            self.db.add_ledger(name, group)
        self.db.conn.execute("UPDATE ledger SET opening_balance = 1000, opening_balance_type = 'Dr' WHERE name = 'Bank'")
        self.receipt = self.voucher('Receipt', '2026-05-05', 'Buyer', 'Cr', 500)
        self.pay1 = self.voucher('Payment', '2026-05-06', 'Rent', 'Dr', 200)
        self.pay2 = self.voucher('Payment', '2026-05-07', 'Rent', 'Dr', 200)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def voucher(self, vtype, date, other, other_side, amount):
        bank_side = 'Dr' if other_side == 'Cr' else 'Cr'
        return self.db.add_voucher(vtype, date, 'x', [{'ledger_name': 'Bank', 'entry_type': bank_side, 'amount': amount},
                                                      {'ledger_name': other, 'entry_type': other_side, 'amount': amount}])

    def st(self, as_of='2026-05-31'):
        return bank_recon.statement(self.db, 'Bank', as_of)

    def test_statement_shows_books_unreflected_items_and_the_bank_balance(self):
        s = self.st()
        self.assertEqual((s['book_balance'], s['unreflected_debits'], s['unreflected_credits'], s['bank_balance']), (1100.0, 500.0, 400.0, 1000.0))
        bank_recon.mark(self.db, self.receipt, 'Bank', '2026-05-08')
        s = self.st()
        self.assertEqual((s['unreflected_debits'], s['bank_balance'], s['cleared_count'], s['pending_count']), (0.0, 1500.0, 1, 2))
        self.assertEqual(self.st('2026-05-07')['unreflected_debits'], 500.0)             # on the 7th the bank had not yet cleared it
        bank_recon.mark(self.db, self.receipt, 'Bank', '')                                 # an empty date undoes it
        self.assertEqual(self.st()['bank_balance'], 1000.0)

    def test_imported_entries_without_a_row_are_picked_up_and_cancelled_ones_ignored(self):
        self.db.conn.execute('DELETE FROM bank_reconciliation')                            # as if the vouchers came in by import
        self.assertEqual(self.st()['pending_count'], 3)
        self.db.cancel_voucher(self.pay2)
        s = self.st()
        self.assertEqual((s['pending_count'], s['book_balance']), (2, 1300.0))

    def test_statement_lines_match_by_amount_then_reference_then_nearest_date(self):
        result = bank_recon.match_statement(self.db, 'Bank', [
            {'date': '2026-05-08', 'amount': '500.00', 'side': 'Dr'},                      # the receipt
            {'date': '2026-05-08', 'amount': '200', 'side': 'Cr'},                         # two payments of 200: the nearer date (the 7th) is taken
            {'date': '2026-05-30', 'amount': '200', 'side': 'Cr'},                         # the last one left
            {'date': '2026-05-09', 'amount': '999', 'side': 'Cr'}])                        # nothing like it
        self.assertEqual((result['matched'], len(result['unmatched_lines'])), (3, 1))
        self.assertEqual(self.st()['pending_count'], 0)
        self.assertEqual(self.db.get_voucher(self.pay2)['id'], self.pay2)
        row = self.db.conn.execute('SELECT bank_date FROM bank_reconciliation WHERE voucher_id = ?', (self.pay2,)).fetchone()
        self.assertEqual(row[0], '2026-05-08')

    def test_two_equally_near_entries_are_left_for_a_person(self):
        result = bank_recon.match_statement(self.db, 'Bank', [{'date': '2026-05-20', 'amount': '200', 'side': 'Cr'}])
        self.assertEqual((result['matched'], result['unmatched_lines'][0]['reason']), (0, 'more than one possible entry'))

    def test_the_csv_upload_uses_the_same_matching_and_rejects_non_bank_ledgers(self):
        csv_text = 'date,description,debit,credit\n2026-05-08,deposit,0.0,500.00\n2026-05-08,cheque,200.00,0.0\n'
        out = self.db.auto_reconcile_statement_csv('Bank', csv_text)
        self.assertEqual((out['matched'], out['total_lines']), (2, 2))
        with self.assertRaisesRegex(ValueError, 'not a bank ledger'):
            bank_recon.statement(self.db, 'Cash')
        with self.assertRaisesRegex(ValueError, 'Invalid'):
            bank_recon.mark(self.db, self.receipt, 'Bank', '31-05-2026')

    def test_report_and_api(self):
        from engine.extension_api import get_route, post_route
        from engine.runtime import TDLRuntime
        from engine.tdl_parser import TDLSymbolTable
        status, body = get_route('/api/banking/statement', {'as_of': ['2026-05-31']}, self.db, None)
        self.assertEqual((status, body['ledgers'], body['bank_balance']), (200, ['Bank'], 1000.0))
        self.assertEqual(post_route('/api/banking/mark', {'voucher_id': self.receipt, 'ledger': 'Bank', 'bank_date': '2026-05-08'}, self.db, None, {'role': 'Admin'})[0], 200)
        self.assertEqual(post_route('/api/banking/mark', {'voucher_id': self.receipt, 'ledger': 'Bank'}, self.db, None, {'role': 'Viewer'})[0], 403)
        rendered = TDLRuntime(TDLSymbolTable(), self.db).render_report('BankReconciliation', to_date='2026-05-31')
        text = ' '.join(f['display_value'] for line in rendered['parts'][0]['lines'] for f in line['fields'])
        self.assertIn('Balance as per bank', text)
        self.assertIn('1,500.00 Dr', text)


if __name__ == '__main__':
    unittest.main()
