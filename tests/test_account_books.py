"""Ledger monthly summary, a month's vouchers and the Cash / Bank Books (synthetic books only)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import account_books as books  # noqa: E402
from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.runtime import TDLRuntime  # noqa: E402
from engine.tdl_parser import TDLSymbolTable  # noqa: E402


class AccountBookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EnterpriseAccountingDB(str(Path(self.tmp.name) / 'books.db'), seed_demo=False)
        self.db.select_company(self.db.create_company(name='Books Co', state='Tamil Nadu', state_code='33'))
        for name, group in (('Sales', 'Sales Accounts'), ('Buyer', 'Sundry Debtors'), ('Rent', 'Indirect Expenses'), ('Cash', 'Cash-in-Hand'),
                            ('Bank', 'Bank Accounts'), ('Cgst 9%', 'Duties & Taxes')):
            self.db.add_ledger(name, group)
        self.db.conn.execute("UPDATE ledger SET opening_balance = 1000, opening_balance_type = 'Dr' WHERE name = 'Bank'")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def v(self, vtype, date, lines, **kw):
        return self.db.add_voucher(vtype, date, 'x', [{'ledger_name': n, 'entry_type': s, 'amount': a} for n, s, a in lines], **kw)

    def test_monthly_summary_carries_the_opening_and_everything_before_the_period(self):
        self.v('Receipt', '2026-04-10', [('Bank', 'Dr', 500), ('Buyer', 'Cr', 500)])
        self.v('Payment', '2026-05-05', [('Rent', 'Dr', 200), ('Bank', 'Cr', 200)])
        self.v('Payment', '2026-05-20', [('Rent', 'Dr', 100), ('Bank', 'Cr', 100)])
        s = books.monthly_summary(self.db, 'Bank', '2026-04-01', '2026-06-30')
        self.assertEqual((s['opening'], s['debit'], s['credit'], s['closing']), (1000.0, 500.0, 300.0, 1200.0))
        by = {m['label']: m for m in s['months']}
        self.assertEqual((by['Apr-2026']['closing'], by['May-2026']['debit'], by['May-2026']['credit'], by['May-2026']['closing'], by['Jun-2026']['closing']),
                         (1500.0, 0.0, 300.0, 1200.0, 1200.0))
        later = books.monthly_summary(self.db, 'Bank', '2026-05-01', '2026-06-30')               # April moves into the opening
        self.assertEqual((later['opening'], later['closing']), (1500.0, 1200.0))
        self.assertEqual(float(self.db.get_ledger_closing_balance('Bank')[0]), 1200.0)           # agrees with the ledger's own balance

    def test_cancelled_and_optional_vouchers_are_left_out(self):
        self.v('Receipt', '2026-04-10', [('Bank', 'Dr', 500), ('Buyer', 'Cr', 500)])
        gone = self.v('Receipt', '2026-04-11', [('Bank', 'Dr', 70), ('Buyer', 'Cr', 70)])
        self.v('Receipt', '2026-04-12', [('Bank', 'Dr', 9), ('Buyer', 'Cr', 9)], is_optional=True)
        self.db.cancel_voucher(gone)
        self.assertEqual(books.monthly_summary(self.db, 'Bank', '2026-04-01', '2026-04-30')['closing'], 1500.0)

    def test_a_months_vouchers_name_the_other_side(self):
        self.v('Receipt', '2026-04-10', [('Bank', 'Dr', 500), ('Buyer', 'Cr', 500)])
        self.v('Sales', '2026-04-12', [('Buyer', 'Dr', 590), ('Sales', 'Cr', 500), ('Cgst 9%', 'Cr', 90)])
        self.v('Journal', '2026-04-14', [('Rent', 'Dr', 30), ('Buyer', 'Cr', 10), ('Cash', 'Cr', 20)])
        bank = books.month_vouchers(self.db, 'Bank', '2026-04')
        self.assertEqual([(r['particulars'], r['debit']) for r in bank['vouchers']], [('Buyer', 500.0)])
        party = books.month_vouchers(self.db, 'Buyer', '2026-04')
        kinds = {r['voucher_type']: r['particulars'] for r in party['vouchers']}
        self.assertEqual((kinds['Receipt'], kinds['Sales'], kinds['Journal']), ('Bank', 'Sales', '(as per details)'))   # the journal has Rent and Cash opposite
        self.assertEqual((party['opening'], party['closing']), (0.0, 590.0 - 500.0 - 10.0))
        mixed = books.month_vouchers(self.db, 'Rent', '2026-04')
        self.assertEqual(mixed['vouchers'][0]['particulars'], '(as per details)')                # two ledgers on the other side

    def test_cash_bank_books_list_only_cash_and_bank_ledgers(self):
        self.v('Receipt', '2026-04-10', [('Bank', 'Dr', 500), ('Buyer', 'Cr', 500)])
        self.v('Payment', '2026-04-11', [('Rent', 'Dr', 40), ('Cash', 'Cr', 40)])
        data = books.cash_bank_books(self.db, '2026-04-01', '2026-04-30')
        rows = {r['ledger']: r for r in data['ledgers']}
        self.assertEqual(set(rows), {'Cash', 'Bank'})
        self.assertEqual((rows['Bank']['opening'], rows['Bank']['closing'], rows['Cash']['credit'], rows['Cash']['closing']), (1000.0, 1500.0, 40.0, -40.0))

    def test_the_report_walks_from_the_list_to_the_summary_to_the_vouchers(self):
        self.v('Receipt', '2026-04-10', [('Bank', 'Dr', 500), ('Buyer', 'Cr', 500)])
        rt = TDLRuntime(TDLSymbolTable(), self.db)

        def text(report):
            return ' | '.join(f['display_value'] for line in report['parts'][0]['lines'] for f in line['fields'])
        listing = rt.render_report('GeneralLedger')
        self.assertIn('Bank', text(listing))
        self.assertTrue(any(line.get('drill') == 'ledger=Bank' for line in listing['parts'][0]['lines']))
        summary = rt.render_report('GeneralLedger', ledger='Bank', from_date='2026-04-01', to_date='2026-04-30')
        self.assertIn('Opening Balance', text(summary))
        self.assertIn('Apr-2026', text(summary))
        self.assertTrue(any(line.get('drill') == 'ledger=Bank&month=2026-04' for line in summary['parts'][0]['lines']))
        month = rt.render_report('GeneralLedger', ledger='Bank', month='2026-04')
        self.assertTrue(any(line.get('voucher_id') for line in month['parts'][0]['lines']))
        self.assertIn('Closing Balance', text(month))
        unknown = rt.render_report('GeneralLedger', ledger='Nobody')                              # an unknown ledger shows the list, not an error
        self.assertIn('Bank', text(unknown))
        self.assertIn('Cash / Bank Books', rt.render_report('CashBankBooks')['title'])
        self.assertIn('Bank', text(rt.render_report('Cash/Bank Books')))


if __name__ == '__main__':
    unittest.main()
