"""Sales / purchase invoice entry: postings, stock, GST, charges, bill references, saving (synthetic books only)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.extension_api import post_route  # noqa: E402
from engine.invoice_entry import build_invoice, save_invoice  # noqa: E402
from engine.invoice_print import build_print  # noqa: E402


def posted(built):
    return sorted((e['ledger_name'], e['entry_type'], round(e['amount'], 2)) for e in built['entries'])


class InvoiceEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EnterpriseAccountingDB(str(Path(self.tmp.name) / 'books.db'), seed_demo=False)
        self.db.select_company(self.db.create_company(name='Invoice Co', state='Tamil Nadu', state_code='33', gstin='33AAAAA0000A1Z5'))
        for name, group in (('Sales', 'Sales Accounts'), ('Purchases', 'Purchase Accounts'), ('Freight', 'Indirect Incomes'),
                            ('Discount Allowed', 'Indirect Expenses'), ('Round Off', 'Indirect Expenses'), ('Cash', 'Cash-in-Hand'),
                            ('Cgst 9%', 'Duties & Taxes'), ('Sgst 9%', 'Duties & Taxes'), ('Igst 18%', 'Duties & Taxes'),
                            ('Buyer', 'Sundry Debtors'), ('Far Buyer', 'Sundry Debtors'), ('Supplier', 'Sundry Creditors')):
            self.db.add_ledger(name, group)
        self.db.conn.execute("UPDATE ledger SET maintain_bill_by_bill=1, credit_period_days=30, state='Tamil Nadu', gstin='33ABCDE1234F1Z5', address='1 Main Rd' WHERE name='Buyer'")
        self.db.conn.execute("UPDATE ledger SET maintain_bill_by_bill=1, state='Kerala' WHERE name IN ('Far Buyer', 'Supplier')")
        self.db.add_stock_item('Soap', uom_symbol='Nos', gst_rate=18)
        self.db.add_stock_item('Rice', uom_symbol='Kg', gst_rate=0)
        self.db.set_negative_stock_policy(True)
        self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def sale(self, **kw):
        data = {'voucher_type': 'Sales', 'date': '2026-05-01', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales',
                'items': [{'stock_item_name': 'Rice', 'qty': 10, 'rate': 40}]}
        data.update(kw)
        return data

    # postings -------------------------------------------------------------------------------------
    def test_a_plain_sale_posts_party_debit_and_sales_credit_and_moves_stock_out(self):
        b = build_invoice(self.db, self.sale())
        self.assertEqual(posted(b), [('Buyer', 'Dr', 400.0), ('Sales', 'Cr', 400.0)])
        self.assertEqual((b['items'][0]['amount'], b['items'][0]['direction'], b['total']), (400.0, 'Out', 400.0))
        self.assertEqual(b['voucher_number'], 'S-0001')

    def test_every_voucher_type_posts_on_the_right_sides_and_moves_stock_the_right_way(self):
        expect = {'Sales': ('Dr', 'Cr', 'Out'), 'Purchase': ('Cr', 'Dr', 'In'), 'Credit Note': ('Cr', 'Dr', 'In'), 'Debit Note': ('Dr', 'Cr', 'Out')}
        for vtype, (party_side, book_side, direction) in expect.items():
            book = 'Purchases' if vtype in ('Purchase', 'Debit Note') else 'Sales'
            party = 'Supplier' if vtype in ('Purchase', 'Debit Note') else 'Buyer'
            b = build_invoice(self.db, self.sale(voucher_type=vtype, party_ledger=party, sales_ledger=book))
            sides = {e['ledger_name']: e['entry_type'] for e in b['entries']}
            self.assertEqual((sides[party], sides[book], b['items'][0]['direction']), (party_side, book_side, direction), vtype)

    def test_item_discount_and_amount_override(self):
        b = build_invoice(self.db, self.sale(items=[{'stock_item_name': 'Rice', 'qty': 10, 'rate': 40, 'discount_percent': 10},
                                                    {'stock_item_name': 'Soap', 'qty': 3, 'rate': 33.33, 'amount': 100}]))
        self.assertEqual([i['amount'] for i in b['items']], [360.0, 100.0])                 # 10% off; a stated amount wins
        self.assertEqual(b['item_total'], 460.0)
        with self.assertRaisesRegex(ValueError, 'does not match quantity x rate'):
            build_invoice(self.db, self.sale(items=[{'stock_item_name': 'Rice', 'qty': 10, 'rate': 40, 'amount': 999}]))
        with self.assertRaisesRegex(ValueError, 'between 0 and 100'):
            build_invoice(self.db, self.sale(items=[{'stock_item_name': 'Rice', 'qty': 1, 'rate': 1, 'discount_percent': 150}]))

    def test_charges_follow_the_invoice_and_a_discount_ledger_flips_the_side(self):
        b = build_invoice(self.db, self.sale(charges=[{'ledger_name': 'Freight', 'amount': 50},
                                                      {'ledger_name': 'Discount Allowed', 'amount': 20, 'entry_type': 'Dr'}]))
        self.assertEqual(posted(b), [('Buyer', 'Dr', 430.0), ('Discount Allowed', 'Dr', 20.0), ('Freight', 'Cr', 50.0), ('Sales', 'Cr', 400.0)])
        pct = build_invoice(self.db, self.sale(charges=[{'ledger_name': 'Freight', 'percent': 5}]))
        self.assertEqual(pct['charges'][0]['amount'], 20.0)                                  # 5% of 400
        self.assertEqual(sum(e['amount'] for e in b['entries'] if e['entry_type'] == 'Dr'), sum(e['amount'] for e in b['entries'] if e['entry_type'] == 'Cr'))

    def test_a_discounted_line_is_saved_at_its_effective_rate(self):
        self.db.set_setting('strict_voucher_entry', True)                      # the stock-line rate check applies
        saved = save_invoice(self.db, self.sale(items=[{'stock_item_name': 'Rice', 'qty': 5, 'rate': 1000, 'discount_percent': 10}]))
        line = self.db.get_voucher(saved['voucher_id'])['inventory'][0]
        self.assertEqual((line['qty'], line['rate'], line['amount']), (5.0, 900.0, 4500.0))
        self.assertEqual(self.db.get_voucher(saved['voucher_id'])['total_amount'], 4500.0)

    # GST ------------------------------------------------------------------------------------------
    def test_gst_is_cgst_sgst_in_state_and_igst_out_of_state(self):
        item = [{'stock_item_name': 'Soap', 'qty': 10, 'rate': 100}]
        intra = build_invoice(self.db, self.sale(items=item, gst=True))
        self.assertEqual(posted(intra), [('Buyer', 'Dr', 1180.0), ('Cgst 9%', 'Cr', 90.0), ('Sales', 'Cr', 1000.0), ('Sgst 9%', 'Cr', 90.0)])
        inter = build_invoice(self.db, self.sale(items=item, gst=True, party_ledger='Far Buyer'))
        self.assertEqual(posted(inter), [('Far Buyer', 'Dr', 1180.0), ('Igst 18%', 'Cr', 180.0), ('Sales', 'Cr', 1000.0)])
        buy = build_invoice(self.db, self.sale(voucher_type='Purchase', party_ledger='Supplier', sales_ledger='Purchases', items=item, gst=True))
        self.assertEqual(sorted((l['ledger_name'], l['entry_type']) for l in buy['tax_lines']), [('Igst 18%', 'Dr')])    # input tax is debited

    def test_exempt_items_carry_no_tax_and_a_missing_tax_ledger_is_an_error(self):
        b = build_invoice(self.db, self.sale(gst=True))                       # Rice has rate 0
        self.assertEqual((b['tax_lines'], b['total']), ([], 400.0))
        self.db.add_stock_item('Pens', uom_symbol='Nos', gst_rate=12)
        with self.assertRaisesRegex(ValueError, 'Create the tax ledger'):
            build_invoice(self.db, self.sale(gst=True, items=[{'stock_item_name': 'Pens', 'qty': 1, 'rate': 100}]))

    def test_round_off_to_the_rupee(self):
        b = build_invoice(self.db, self.sale(items=[{'stock_item_name': 'Rice', 'qty': 1, 'rate': 100.4}], round_off=True))
        self.assertEqual((b['total'], b['round_off']), (100.0, -0.4))
        self.assertIn(('Round Off', 'Dr', 0.4), posted(b))                    # rounding down reduces what the party owes
        up = build_invoice(self.db, self.sale(items=[{'stock_item_name': 'Rice', 'qty': 1, 'rate': 100.6}], round_off=True))
        self.assertEqual((up['total'], up['round_off']), (101.0, 0.4))

    # bill references and details ---------------------------------------------------------------------
    def test_bill_reference_and_due_date(self):
        sale = build_invoice(self.db, self.sale(voucher_number='INV-9'))
        self.assertEqual(sale['bill_allocations'], [{'ledger_name': 'Buyer', 'ref_type': 'New Ref', 'ref_name': 'INV-9', 'amount': 400.0,
                                                     'direction': 'Dr', 'due_date': '2026-05-31'}])          # 30 days' credit period
        self.db.conn.execute("UPDATE ledger SET credit_period_days=0 WHERE name='Supplier'")
        buy = build_invoice(self.db, self.sale(voucher_type='Purchase', party_ledger='Supplier', sales_ledger='Purchases', reference_no='SUP/77'))
        self.assertEqual((buy['bill_allocations'][0]['ref_name'], 'due_date' in buy['bill_allocations'][0]), ('SUP/77', False))   # the supplier's number
        manual = build_invoice(self.db, self.sale(due_date='2026-07-01'))
        self.assertEqual(manual['bill_allocations'][0]['due_date'], '2026-07-01')
        cash = build_invoice(self.db, self.sale(party_ledger='Cash'))
        self.assertEqual(cash['bill_allocations'], [])                                                   # a cash party keeps no bills

    # saving ---------------------------------------------------------------------------------------
    def test_save_stores_everything_and_the_print_shows_it(self):
        saved = save_invoice(self.db, self.sale(gst=False, reference_no='PO-5', order_ref='ORD-12', shipped_by='Blue Dart', destination='Chennai',
                                                vehicle_no='TN01AB1234', consignee_name='Buyer Godown', place_of_supply='Tamil Nadu',
                                                narration='first invoice', charges=[{'ledger_name': 'Freight', 'amount': 25}]))
        vid = saved['voucher_id']
        v = self.db.get_voucher(vid)
        self.assertEqual((v['voucher_number'], v['reference_no'], v['vehicle_no'], v['narration'], v['total_amount']), ('S-0001', 'PO-5', 'TN01AB1234', 'first invoice', 425.0))
        self.assertEqual([(i['stock_item_name'], i['direction'], i['qty'], i['amount']) for i in v['inventory']], [('Rice', 'Out', 10.0, 400.0)])
        self.assertEqual([(b['ref_type'], b['ref_name'], b['amount']) for b in v['bills']], [('New Ref', 'S-0001', 425.0)])
        self.assertEqual(self.db.get_stock_item('Rice')['closing_qty'], -10.0)
        doc = build_print(self.db, vid)
        self.assertEqual((doc['order_ref'], doc['shipped_by'], doc['destination'], doc['consignee']['name']), ('ORD-12', 'Blue Dart', 'Chennai', 'Buyer Godown'))
        self.assertEqual((doc['party']['gstin'], doc['company']['gstin'], doc['total']), ('33ABCDE1234F1Z5', '33AAAAA0000A1Z5', 425.0))
        self.assertEqual([c['name'] for c in doc['charges']], ['Freight'])
        # the next invoice continues the series
        self.assertEqual(save_invoice(self.db, self.sale())['voucher_number'], 'S-0002')

    def test_the_numbered_series_continues_and_entry_rules_still_apply(self):
        self.db.set_setting('strict_voucher_entry', True)
        self.db.add_voucher('Sales', '2026-04-01', 'x', [{'ledger_name': 'Buyer', 'entry_type': 'Dr', 'amount': 10}, {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': 10}],
                            voucher_number='INV-0041/26-27')
        self.assertEqual(save_invoice(self.db, self.sale())['voucher_number'], 'INV-0042/26-27')
        with self.assertRaisesRegex(ValueError, "Godown 'Shed' does not exist"):
            save_invoice(self.db, self.sale(items=[{'stock_item_name': 'Rice', 'qty': 1, 'rate': 5, 'godown_name': 'Shed'}]))

    def test_bad_input_is_refused_before_anything_is_saved(self):
        count = lambda: self.db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0]
        bad = [(self.sale(voucher_type='Journal'), 'Invoices are entered'), (self.sale(party_ledger='Nobody'), 'does not exist'),
               (self.sale(sales_ledger='Nope'), 'does not exist'), (self.sale(items=[]), 'at least one item'),
               (self.sale(items=[{'stock_item_name': 'Ghost', 'qty': 1, 'rate': 1}]), 'does not exist'),
               (self.sale(items=[{'stock_item_name': 'Rice', 'qty': -1, 'rate': 1}]), 'cannot be negative'),
               (self.sale(date='1/5/2026'), 'Invalid voucher date'), (self.sale(items=[{'stock_item_name': 'Rice', 'qty': 0, 'rate': 0}]), 'must be positive'),
               (self.sale(charges=[{'ledger_name': 'Nope', 'amount': 5}]), 'does not exist'),
               (self.sale(charges=[{'ledger_name': 'Sales', 'amount': 5}]), 'is a sales/purchase ledger')]
        for data, text in bad:
            with self.assertRaisesRegex(ValueError, text):
                save_invoice(self.db, data)
        self.assertEqual(count(), 0)

    def test_compute_previews_without_saving_and_viewers_cannot_save(self):
        status, built = post_route('/api/invoices/compute', self.sale(), self.db, None, {'role': 'Admin'})
        self.assertEqual((status, built['total'], self.db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0]), (200, 400.0, 0))
        self.assertEqual(post_route('/api/invoices/save', self.sale(), self.db, None, {'role': 'Viewer'})[0], 403)
        status, saved = post_route('/api/invoices/save', self.sale(), self.db, None, {'role': 'Accountant'})
        self.assertEqual((status, saved['voucher_number']), (201, 'S-0001'))


if __name__ == '__main__':
    unittest.main()
