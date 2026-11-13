"""Rejection notes, TCS / TDS on invoices, price lists and the note print layout (synthetic books only)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import orders, price_lists  # noqa: E402
from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.invoice_entry import build_invoice, save_invoice  # noqa: E402
from engine.invoice_print import build_print  # noqa: E402


class ExtrasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EnterpriseAccountingDB(str(Path(self.tmp.name) / 'books.db'), seed_demo=False)
        self.db.select_company(self.db.create_company(name='Extras Co', state='Tamil Nadu', state_code='33'))
        for name, group in (('Sales', 'Sales Accounts'), ('Purchases', 'Purchase Accounts'), ('Buyer', 'Sundry Debtors'),
                            ('Supplier', 'Sundry Creditors'), ('TCS Payable', 'Duties & Taxes'), ('TDS Payable', 'Duties & Taxes'),
                            ('Misc', 'Indirect Expenses')):
            self.db.add_ledger(name, group)
        self.db.add_stock_item('Rice', uom_symbol='Kg', gst_rate=0)
        self.db.set_negative_stock_policy(True)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def stock(self):
        return self.db.get_stock_item('Rice')['closing_qty']

    def balance(self, ledger):
        return float(self.db.get_ledger_closing_balance(ledger)[0])

    def note(self, vtype, qty=10, rate=60):
        sales = vtype == 'Delivery Note'
        return save_invoice(self.db, {'voucher_type': vtype, 'date': '2026-05-05', 'party_ledger': 'Buyer' if sales else 'Supplier',
                                      'sales_ledger' if sales else 'purchase_ledger': 'Sales' if sales else 'Purchases',
                                      'items': [{'stock_item_name': 'Rice', 'qty': qty, 'rate': rate}]})['voucher_id']

    def reject(self, vtype, against, qty):
        sales = vtype == 'Rejections In'
        return save_invoice(self.db, {'voucher_type': vtype, 'date': '2026-05-08', 'party_ledger': 'Buyer' if sales else 'Supplier',
                                      'sales_ledger' if sales else 'purchase_ledger': 'Sales' if sales else 'Purchases',
                                      'items': [{'stock_item_name': 'Rice', 'qty': qty, 'rate': 60, 'order_voucher_id': against}]})['voucher_id']

    def sale(self, qty, against, date='2026-05-10'):
        return save_invoice(self.db, {'voucher_type': 'Sales', 'date': date, 'party_ledger': 'Buyer', 'sales_ledger': 'Sales',
                                      'items': [{'stock_item_name': 'Rice', 'qty': qty, 'rate': 60, 'order_voucher_id': against}]})['voucher_id']

    def pending(self, vtype):
        return orders.order_lines(self.db, vtype)[0]['pending_qty']

    # rejection notes -----------------------------------------------------------------------------------------
    def test_a_rejection_in_brings_goods_back_posts_nothing_and_lowers_what_is_left_to_invoice(self):
        note = self.note('Delivery Note')
        self.assertEqual(self.stock(), -10.0)
        rej = self.reject('Rejections In', note, 3)
        v = self.db.get_voucher(rej)
        self.assertEqual((v['voucher_type'], v['is_optional'], v['inventory'][0]['direction']), ('Rejections In', 1, 'In'))
        self.assertEqual((self.stock(), self.balance('Buyer'), self.pending('Delivery Note')), (-7.0, 0, 7.0))
        self.sale(7, note)                                        # the rest is invoiced: goods go out once, not twice
        self.assertEqual((self.stock(), self.balance('Buyer'), self.pending('Delivery Note')), (-7.0, 420, 0.0))
        self.db.cancel_voucher(rej)                               # the goods are not back after all
        self.assertEqual((self.stock(), self.pending('Delivery Note')), (-10.0, 3.0))

    def test_more_cannot_be_invoiced_than_is_left_after_a_rejection(self):
        note = self.note('Delivery Note')
        self.reject('Rejections In', note, 4)
        with self.assertRaisesRegex(ValueError, 'Only 6 of .Rice. is pending'):
            self.sale(7, note)
        with self.assertRaisesRegex(ValueError, 'Only 6 of .Rice. is pending'):
            self.reject('Rejections In', note, 7)

    def test_a_rejection_out_sends_goods_back_to_the_supplier_against_a_receipt_note(self):
        note = self.note('Receipt Note')
        self.assertEqual(self.stock(), 10.0)
        rej = self.reject('Rejections Out', note, 2)
        v = self.db.get_voucher(rej)
        self.assertEqual((v['voucher_type'], v['inventory'][0]['direction'], self.stock(), self.pending('Receipt Note')), ('Rejections Out', 'Out', 8.0, 8.0))
        self.assertEqual(self.balance('Supplier'), 0)
        with self.assertRaisesRegex(ValueError, 'cannot be allocated'):
            self.reject('Rejections In', note, 1)                 # a rejection in belongs to a delivery note, not a receipt note

    # TCS and TDS ---------------------------------------------------------------------------------------------
    def test_tcs_adds_to_the_sale_and_is_credited_to_the_tax_ledger(self):
        built = build_invoice(self.db, {'voucher_type': 'Sales', 'date': '2026-05-10', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales',
                                        'items': [{'stock_item_name': 'Rice', 'qty': 10, 'rate': 105}], 'tcs': {'ledger_name': 'TCS Payable', 'percent': 0.1}})
        self.assertEqual(built['statutory'][0]['amount'], 1.0)    # 0.1% of 1,050 = 1.05 -> whole rupee
        self.assertEqual(built['total'], 1051.0)
        save_invoice(self.db, {'voucher_type': 'Sales', 'date': '2026-05-10', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales',
                               'items': [{'stock_item_name': 'Rice', 'qty': 10, 'rate': 105}], 'tcs': {'ledger_name': 'TCS Payable', 'amount': 1.05}})
        self.assertEqual((self.balance('Buyer'), self.balance('TCS Payable'), self.balance('Sales')), (1051.05, 1.05, 1050.0))

    def test_tds_reduces_what_is_owed_to_the_supplier(self):
        saved = save_invoice(self.db, {'voucher_type': 'Purchase', 'date': '2026-05-10', 'party_ledger': 'Supplier', 'purchase_ledger': 'Purchases',
                                       'items': [{'stock_item_name': 'Rice', 'qty': 100, 'rate': 100}], 'tds': {'ledger_name': 'TDS Payable', 'percent': 10}})
        self.assertEqual(saved['total'], 9000.0)
        self.assertEqual((self.balance('Supplier'), self.balance('TDS Payable'), self.balance('Purchases')), (9000.0, 1000.0, 10000.0))

    def test_statutory_rules(self):
        base = {'voucher_type': 'Sales', 'date': '2026-05-10', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales', 'items': [{'stock_item_name': 'Rice', 'qty': 1, 'rate': 100}]}
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            build_invoice(self.db, {**base, 'tcs': {'ledger_name': 'Nope', 'percent': 1}})
        with self.assertRaisesRegex(ValueError, 'percent or an amount'):
            build_invoice(self.db, {**base, 'tcs': {'ledger_name': 'TCS Payable'}})
        built = build_invoice(self.db, {**base, 'tcs': {'ledger_name': 'Misc', 'percent': 10}})
        self.assertTrue(any('not under Duties & Taxes' in w for w in built['warnings']))
        with self.assertRaisesRegex(ValueError, 'item lines only'):
            build_invoice(self.db, {**base, 'voucher_type': 'Delivery Note', 'tds': {'ledger_name': 'TDS Payable', 'percent': 1}})

    # price lists ---------------------------------------------------------------------------------------------
    def make_list(self):
        price_lists.create(self.db, 'Wholesale')
        price_lists.set_rates(self.db, 'Wholesale', [
            {'stock_item_name': 'Rice', 'effective_from': '2026-04-01', 'rate': 50},
            {'stock_item_name': 'Rice', 'effective_from': '2026-06-01', 'rate': 55},
            {'stock_item_name': 'Rice', 'effective_from': '2026-04-01', 'qty_from': 100, 'rate': 45, 'discount_percent': 10}])

    def test_the_rate_in_force_on_the_date_for_the_quantity_is_used(self):
        self.make_list()
        r = lambda date, qty: (price_lists.rate_for(self.db, 'Wholesale', 'Rice', date, qty) or {}).get('rate')
        self.assertEqual((r('2026-05-10', 10), r('2026-06-10', 10), r('2026-05-10', 100), r('2026-03-31', 10)), (50, 55, 45, None))
        self.assertEqual(price_lists.rate_for(self.db, 'Wholesale', 'Rice', '2026-05-10', 150)['discount_percent'], 10)

    def test_an_invoice_line_without_a_rate_is_priced_from_the_party_list_and_an_explicit_rate_wins(self):
        self.make_list()
        price_lists.assign(self.db, 'Buyer', 'Wholesale')
        data = {'voucher_type': 'Sales', 'date': '2026-05-10', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales'}
        priced = build_invoice(self.db, {**data, 'items': [{'stock_item_name': 'Rice', 'qty': 10}]})
        self.assertEqual((priced['items'][0]['rate'], priced['item_total'], priced['price_list']), (50.0, 500.0, 'Wholesale'))
        slab = build_invoice(self.db, {**data, 'items': [{'stock_item_name': 'Rice', 'qty': 100}]})
        self.assertEqual((slab['items'][0]['rate'], slab['items'][0]['discount_percent'], slab['item_total']), (45.0, 10.0, 4050.0))
        typed = build_invoice(self.db, {**data, 'items': [{'stock_item_name': 'Rice', 'qty': 10, 'rate': 70}]})
        self.assertEqual(typed['item_total'], 700.0)
        saved = save_invoice(self.db, {**data, 'items': [{'stock_item_name': 'Rice', 'qty': 10}]})
        self.assertEqual(self.balance('Buyer'), 500.0)
        self.assertEqual(saved['price_list'], 'Wholesale')

    def test_price_list_edge_cases(self):
        self.make_list()
        data = {'voucher_type': 'Sales', 'date': '2026-03-01', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales', 'items': [{'stock_item_name': 'Rice', 'qty': 1}]}
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            build_invoice(self.db, {**data, 'price_list': 'Retail'})
        with self.assertRaisesRegex(ValueError, 'must be positive'):
            build_invoice(self.db, {**data, 'price_list': 'Wholesale'})      # before the list's first date: no price, and no rate typed
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            price_lists.set_rates(self.db, 'Wholesale', [{'stock_item_name': 'Ghost', 'effective_from': '2026-04-01', 'rate': 1}])
        with self.assertRaisesRegex(ValueError, 'slab'):
            price_lists.set_rates(self.db, 'Wholesale', [{'stock_item_name': 'Rice', 'effective_from': '2026-04-01', 'qty_from': 10, 'qty_to': 5, 'rate': 1}])

    # the print layout ----------------------------------------------------------------------------------------
    def test_notes_print_on_their_own_layout_without_tax_invoice_wording(self):
        note = self.note('Delivery Note')
        doc = build_print(self.db, note)
        self.assertEqual((doc['kind'], doc['title'], doc['supplier_side'], doc['items_total'], doc['charges']), ('note', 'DELIVERY NOTE', False, 600.0, []))
        rej = build_print(self.db, self.reject('Rejections Out', self.note('Receipt Note'), 1))
        self.assertEqual((rej['title'], rej['supplier_side']), ('REJECTION NOTE (OUT)', True))
        po = save_invoice(self.db, {'voucher_type': 'Purchase Order', 'date': '2026-05-01', 'party_ledger': 'Supplier', 'purchase_ledger': 'Purchases',
                                    'items': [{'stock_item_name': 'Rice', 'qty': 5, 'rate': 50, 'due_date': '2026-06-30'}]})['voucher_id']
        self.assertEqual(build_print(self.db, po)['items'][0]['due'], '2026-06-30')

    def test_price_list_rates_must_be_positive(self):
        price_lists.create(self.db, 'Zero')
        with self.assertRaisesRegex(ValueError, 'above zero'):
            price_lists.set_rates(self.db, 'Zero', [{'stock_item_name': 'Rice', 'effective_from': '2026-04-01', 'rate': 0}])

    # e-way details and readiness -------------------------------------------------------------------------------
    def test_dispatch_details_are_stored_and_readiness_lists_what_is_missing(self):
        from engine.einvoice_check import readiness
        self.db.conn.execute("UPDATE stock_item SET hsn_code = '' WHERE name = 'Rice'")        # new items start with a placeholder HSN
        base = {'voucher_type': 'Sales', 'date': '2026-05-10', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales', 'items': [{'stock_item_name': 'Rice', 'qty': 2, 'rate': 60}]}
        with self.assertRaisesRegex(ValueError, 'vehicle number'):
            build_invoice(self.db, {**base, 'vehicle_no': 'NOT A PLATE!'})
        vid = save_invoice(self.db, {**base, 'vehicle_no': 'tn 01-ab 1234', 'transporter_id': ' 33AAAAA0000A1Z5 ', 'distance_km': 120})['voucher_id']
        row = self.db.conn.execute('SELECT vehicle_no, transporter_id, distance_km FROM voucher WHERE id = ?', (vid,)).fetchone()
        self.assertEqual(tuple(row), ('TN01AB1234', '33AAAAA0000A1Z5', 120.0))
        r = readiness(self.db, vid)
        self.assertTrue(r['applicable'])
        self.assertIn('seller GSTIN (company)', r['einvoice']['missing'])
        self.assertTrue(any(m.startswith('HSN / SAC code') for m in r['einvoice']['missing']))
        self.assertNotIn('vehicle number', r['eway']['missing'])                   # entered, so no longer missing
        bare = save_invoice(self.db, base)['voucher_id']
        self.assertEqual(sorted({'vehicle number', 'transport distance (km)'} & set(readiness(self.db, bare)['eway']['missing'])), ['transport distance (km)', 'vehicle number'])
        note = self.note('Delivery Note')
        self.assertFalse(readiness(self.db, note)['applicable'])


if __name__ == '__main__':
    unittest.main()
