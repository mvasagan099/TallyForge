"""GST on entry: intra / inter-state, per-rate tax ledgers, side by voucher type, rounding (synthetic books only)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.gst_entry import suggest_gst  # noqa: E402


class GstEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EnterpriseAccountingDB(str(Path(self.tmp.name) / 'books.db'), seed_demo=False)
        self.db.select_company(self.db.create_company(name='Gst Co', state='Tamil Nadu', state_code='33'))
        for name in ('Cgst 9%', 'Sgst 9%', 'Igst 18%', 'Cgst 2.5%', 'Sgst 2.5%', 'Igst 5%'):
            self.db.add_ledger(name, 'Duties & Taxes')
        for name, state in (('Local Buyer', 'Tamil Nadu'), ('Far Buyer', 'Kerala'), ('Unknown Buyer', '')):
            self.db.add_ledger(name, 'Sundry Debtors')
            self.db.conn.execute('UPDATE ledger SET state=? WHERE name=?', (state, name))
        self.db.add_stock_item('Soap', uom_symbol='Nos', gst_rate=18)
        self.db.add_stock_item('Rice', uom_symbol='Kg', gst_rate=5)
        self.db.add_stock_item('Exempt', uom_symbol='Kg', gst_rate=0)
        self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def lines(self, result):
        return [(l['kind'], l['rate'], l['amount'], l['ledger_name'], l['entry_type']) for l in result['tax_lines']]

    def test_intra_state_splits_into_cgst_and_sgst(self):
        r = suggest_gst(self.db, 'Sales', 'Local Buyer', taxable={18: 1000})
        self.assertEqual(r['supply'], 'Intra-state (CGST + SGST)')
        self.assertEqual(self.lines(r), [('CGST', 9.0, 90.0, 'Cgst 9%', 'Cr'), ('SGST', 9.0, 90.0, 'Sgst 9%', 'Cr')])
        self.assertEqual((r['total_tax'], r['invoice_total'], r['party_entry_type']), (180.0, 1180.0, 'Dr'))

    def test_inter_state_is_igst_at_the_full_rate(self):
        r = suggest_gst(self.db, 'Sales', 'Far Buyer', taxable={18: 1000})
        self.assertEqual(r['supply'], 'Inter-state (IGST)')
        self.assertEqual(self.lines(r), [('IGST', 18.0, 180.0, 'Igst 18%', 'Cr')])
        self.assertEqual(suggest_gst(self.db, 'Sales', 'Local Buyer', taxable={18: 1000}, place_of_supply='Kerala')['supply'], 'Inter-state (IGST)')

    def test_side_follows_the_voucher_type(self):
        sides = {vt: suggest_gst(self.db, vt, 'Local Buyer', taxable={18: 100})['tax_lines'][0]['entry_type']
                 for vt in ('Sales', 'Purchase', 'Credit Note', 'Debit Note')}
        self.assertEqual(sides, {'Sales': 'Cr', 'Purchase': 'Dr', 'Credit Note': 'Dr', 'Debit Note': 'Cr'})
        self.assertEqual(suggest_gst(self.db, 'Purchase', 'Local Buyer', taxable={18: 100})['party_entry_type'], 'Cr')

    def test_item_rates_group_the_taxable_value_and_exempt_items_carry_no_tax(self):
        r = suggest_gst(self.db, 'Sales', 'Local Buyer', items=[{'stock_item_name': 'Soap', 'amount': 1000}, {'stock_item_name': 'Rice', 'amount': 2000},
                                                                {'stock_item_name': 'Exempt', 'amount': 500}])
        self.assertEqual(self.lines(r), [('CGST', 2.5, 50.0, 'Cgst 2.5%', 'Cr'), ('SGST', 2.5, 50.0, 'Sgst 2.5%', 'Cr'),
                                         ('CGST', 9.0, 90.0, 'Cgst 9%', 'Cr'), ('SGST', 9.0, 90.0, 'Sgst 9%', 'Cr')])
        self.assertEqual((r['taxable_value'], r['total_tax'], r['invoice_total']), (3500.0, 280.0, 3780.0))
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            suggest_gst(self.db, 'Sales', 'Local Buyer', items=[{'stock_item_name': 'Ghost', 'amount': 1}])

    def test_missing_ledgers_and_unknown_state_are_reported_not_invented(self):
        r = suggest_gst(self.db, 'Sales', 'Unknown Buyer', taxable={12: 1000})
        self.assertEqual([l['ledger_name'] for l in r['tax_lines']], [None, None])
        self.assertEqual([l['missing_ledger'] for l in r['tax_lines']], ['Cgst 6%', 'Sgst 6%'])
        self.assertTrue(any('Cgst 6%' in w for w in r['warnings']))
        self.assertTrue(any('state is not known' in w for w in r['warnings']))
        self.assertFalse(self.db.conn.execute("SELECT 1 FROM ledger WHERE name='Cgst 6%'").fetchone())

    def test_amounts_round_half_up_to_the_paisa_and_round_off_is_offered(self):
        r = suggest_gst(self.db, 'Sales', 'Local Buyer', taxable={5: 10.05})            # 2.5% of 10.05 = 0.25125 each
        self.assertEqual([l['amount'] for l in r['tax_lines']], [0.25, 0.25])
        self.assertEqual(r['invoice_total'], 10.55)
        self.assertEqual(r['round_off'], 0.45)                                           # to 11.00
        self.assertEqual(suggest_gst(self.db, 'Sales', 'Far Buyer', taxable={5: 10.10})['tax_lines'][0]['amount'], 0.51)   # 0.505 rounds up

    def test_only_invoice_like_vouchers_get_gst(self):
        with self.assertRaises(ValueError):
            suggest_gst(self.db, 'Journal', 'Local Buyer', taxable={18: 100})


if __name__ == '__main__':
    unittest.main()
