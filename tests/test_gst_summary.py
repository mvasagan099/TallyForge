"""GST summary from posted tax ledgers (synthetic books only)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.gst_summary import gst_summary  # noqa: E402


class GstSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EnterpriseAccountingDB(str(Path(self.tmp.name) / 'books.db'), seed_demo=False)
        self.db.select_company(self.db.create_company(name='Gst Co', state='Tamil Nadu', state_code='33'))
        for name, group in (('Sales', 'Sales Accounts'), ('Services', 'Direct Incomes'), ('Purchases', 'Purchase Accounts'), ('Buyer', 'Sundry Debtors'),
                            ('Walk-in', 'Sundry Debtors'), ('Supplier', 'Sundry Creditors'), ('Cgst 9%', 'Duties & Taxes'), ('Sgst 9%', 'Duties & Taxes'),
                            ('Cgst 2.5%', 'Duties & Taxes'), ('Sgst 2.5%', 'Duties & Taxes'), ('Igst 18%', 'Duties & Taxes'), ('Round Off', 'Indirect Expenses'),
                            ('Bank', 'Bank Accounts')):
            self.db.add_ledger(name, group)
        self.db.conn.execute("UPDATE ledger SET gstin = '33AAAAA0000A1Z5' WHERE name = 'Buyer'")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def post(self, vtype, party, party_side, lines, date='2026-05-10'):
        entries = [{'ledger_name': party, 'entry_type': party_side, 'amount': sum(a if s != party_side else -a for _n, s, a in lines)}]
        entries += [{'ledger_name': n, 'entry_type': s, 'amount': a} for n, s, a in lines]
        return self.db.add_voucher(vtype, date, 'x', entries)

    def run_summary(self):
        return gst_summary(self.db, '2026-04-01', '2027-03-31')

    def test_sales_credit_notes_and_purchases_fill_the_outward_and_inward_tables_by_rate(self):
        self.post('Sales', 'Buyer', 'Dr', [('Sales', 'Cr', 1000), ('Cgst 9%', 'Cr', 90), ('Sgst 9%', 'Cr', 90)])
        self.post('Sales', 'Walk-in', 'Dr', [('Services', 'Cr', 200), ('Cgst 2.5%', 'Cr', 5), ('Sgst 2.5%', 'Cr', 5)], date='2026-05-11')
        self.post('Credit Note', 'Buyer', 'Cr', [('Sales', 'Dr', 100), ('Cgst 9%', 'Dr', 9), ('Sgst 9%', 'Dr', 9)], date='2026-05-12')
        self.post('Purchase', 'Supplier', 'Cr', [('Purchases', 'Dr', 500), ('Cgst 9%', 'Dr', 45), ('Sgst 9%', 'Dr', 45)], date='2026-05-13')
        r = self.run_summary()
        by_rate = {row['rate']: row for row in r['outward']}
        self.assertEqual((by_rate[18.0]['taxable'], by_rate[18.0]['cgst'], by_rate[18.0]['sgst']), (900.0, 81.0, 81.0))   # the credit note reduces it
        self.assertEqual((by_rate[5.0]['taxable'], by_rate[5.0]['cgst']), (200.0, 5.0))                                   # a service ledger counts as supply
        self.assertEqual((r['inward_total']['taxable'], r['inward_total']['cgst']), (500.0, 45.0))
        self.assertEqual((r['net_tax']['cgst'], r['net_tax']['sgst']), (41.0, 41.0))
        self.assertEqual(r['outward_registered']['taxable'], 900.0)                                                       # the buyer has a GSTIN
        self.assertEqual(r['outward_unregistered']['taxable'], 200.0)
        self.assertTrue(r['closes'])

    def test_interstate_sale_uses_the_integrated_tax_head_and_round_off_is_not_taxable(self):
        self.post('Sales', 'Buyer', 'Dr', [('Sales', 'Cr', 1000), ('Igst 18%', 'Cr', 180), ('Round Off', 'Cr', 0.4)])
        r = self.run_summary()
        self.assertEqual((r['outward'][0]['rate'], r['outward'][0]['taxable'], r['outward'][0]['igst']), (18.0, 1000.0, 180.0))

    def test_a_mixed_rate_voucher_is_split_by_rate_with_taxable_worked_back_and_a_warning(self):
        self.post('Sales', 'Walk-in', 'Dr', [('Sales', 'Cr', 1100), ('Cgst 9%', 'Cr', 90), ('Sgst 9%', 'Cr', 90), ('Cgst 2.5%', 'Cr', 2.5), ('Sgst 2.5%', 'Cr', 2.5)])
        r = self.run_summary()
        by_rate = {row['rate']: row for row in r['outward']}
        self.assertEqual((by_rate[18.0]['taxable'], by_rate[5.0]['taxable']), (1000.0, 100.0))
        self.assertTrue(any('more than one rate' in w for w in r['warnings']))

    def test_other_voucher_types_and_one_sided_postings_are_reported_not_hidden(self):
        self.post('Sales', 'Buyer', 'Dr', [('Sales', 'Cr', 1000), ('Cgst 9%', 'Cr', 90)])               # SGST forgotten
        self.db.add_voucher('Journal', '2026-05-20', 'tax adjustment', [{'ledger_name': 'Cgst 9%', 'entry_type': 'Dr', 'amount': 10}, {'ledger_name': 'Walk-in', 'entry_type': 'Cr', 'amount': 10}])
        r = self.run_summary()
        self.assertEqual((r['other_postings']['cgst'], len(r['other_vouchers'])), (-10.0, 1))
        self.assertTrue(r['closes'])                                                                    # the journal is listed apart, so it still closes
        self.assertTrue(any('differ' in w for w in r['warnings']))

    def test_the_report_renders_and_a_period_without_tax_ledgers_says_so(self):
        from engine.runtime import TDLRuntime
        from engine.tdl_parser import TDLSymbolTable
        self.post('Sales', 'Buyer', 'Dr', [('Sales', 'Cr', 1000), ('Cgst 9%', 'Cr', 90), ('Sgst 9%', 'Cr', 90)])
        rendered = TDLRuntime(TDLSymbolTable(), self.db).render_report('GSTSummary', from_date='2026-04-01', to_date='2027-03-31')
        text = ' '.join(f['display_value'] for line in rendered['parts'][0]['lines'] for f in line['fields'])
        self.assertIn('Outward supplies', text)
        self.assertIn('Closes against the tax ledgers: yes', text)
        self.assertEqual(gst_summary(self.db, '2020-01-01', '2020-01-31')['outward'], [])


if __name__ == '__main__':
    unittest.main()
