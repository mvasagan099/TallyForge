"""Order processing: order vouchers, due dates, allocation against invoices, the pending-orders report (synthetic books only)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import orders  # noqa: E402
from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.invoice_entry import build_invoice, save_invoice  # noqa: E402


class OrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EnterpriseAccountingDB(str(Path(self.tmp.name) / 'books.db'), seed_demo=False)
        self.db.select_company(self.db.create_company(name='Order Co', state='Tamil Nadu', state_code='33'))
        for name, group in (('Sales', 'Sales Accounts'), ('Purchases', 'Purchase Accounts'), ('Buyer', 'Sundry Debtors'), ('Supplier', 'Sundry Creditors')):
            self.db.add_ledger(name, group)
        self.db.add_stock_item('Rice', uom_symbol='Kg', gst_rate=0)
        self.db.add_stock_item('Toor', uom_symbol='Kg', gst_rate=0)
        self.db.set_negative_stock_policy(True)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def po(self, order_no='PO-1', qty=100, rate=50, due='2026-06-30', date='2026-05-01', item='Rice', party='Supplier'):
        return save_invoice(self.db, {'voucher_type': 'Purchase Order', 'date': date, 'party_ledger': party, 'purchase_ledger': 'Purchases',
                                      'reference_no': order_no, 'items': [{'stock_item_name': item, 'qty': qty, 'rate': rate, 'due_date': due}]})

    def purchase(self, order_voucher_id=None, qty=40, rate=50, date='2026-06-10', item='Rice'):
        line = {'stock_item_name': item, 'qty': qty, 'rate': rate}
        if order_voucher_id:
            line['order_voucher_id'] = order_voucher_id
        return save_invoice(self.db, {'voucher_type': 'Purchase', 'date': date, 'party_ledger': 'Supplier', 'purchase_ledger': 'Purchases', 'items': [line]})

    def pending(self, otype='Purchase Order', as_of='2026-07-10'):
        return orders.pending_orders(self.db, otype, as_of)

    # the order voucher --------------------------------------------------------------------------------
    def test_an_order_posts_nothing_moves_no_stock_and_keeps_its_terms(self):
        saved = self.po()
        v = self.db.get_voucher(saved['voucher_id'])
        self.assertEqual((v['voucher_type'], v['is_optional'], v['reference_no'], v['total_amount']), ('Purchase Order', 1, 'PO-1', 5000.0))
        self.assertEqual(self.db.get_ledger_closing_balance('Supplier')[0], 0)           # no posting reaches the books
        self.assertEqual(self.db.get_stock_item('Rice')['closing_qty'], 0.0)             # no stock moves
        self.assertEqual(self.db.conn.execute('SELECT due_date FROM order_line WHERE voucher_id=?', (saved['voucher_id'],)).fetchone()[0], '2026-06-30')
        self.assertEqual(self.db.get_financial_summary()['purchase_total'], 0.0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM bill_allocation').fetchone()[0], 0)

    def test_an_order_has_item_lines_only(self):
        for extra in ({'gst': True}, {'round_off': True}, {'charges': [{'ledger_name': 'Sales', 'amount': 5}]}):
            with self.assertRaisesRegex(ValueError, 'item lines only'):
                build_invoice(self.db, {'voucher_type': 'Purchase Order', 'party_ledger': 'Supplier', 'purchase_ledger': 'Purchases',
                                        'items': [{'stock_item_name': 'Rice', 'qty': 1, 'rate': 1}], **extra})
        with self.assertRaisesRegex(ValueError, 'Invalid due date'):
            self.po(due='30/6/2026')

    def test_the_order_number_defaults_to_the_voucher_number(self):
        saved = save_invoice(self.db, {'voucher_type': 'Sales Order', 'date': '2026-05-01', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales',
                                       'items': [{'stock_item_name': 'Rice', 'qty': 5, 'rate': 60}]})
        self.assertEqual(self.db.get_voucher(saved['voucher_id'])['reference_no'], saved['voucher_number'])

    # pending report ---------------------------------------------------------------------------------
    def test_pending_report_rows_value_and_days_overdue(self):
        self.po()
        row = self.pending()[0]
        self.assertEqual((row['order_no'], row['party'], row['stock_item_name'], row['pending_qty'], row['rate'], row['pending_value'], row['due_date']),
                         ('PO-1', 'Supplier', 'Rice', 100.0, 50.0, 5000.0, '2026-06-30'))
        self.assertEqual(row['overdue_days'], 10)                                        # 10 July minus 30 June
        self.assertEqual(self.pending(as_of='2026-06-01')[0]['overdue_days'], 0)         # not yet due
        self.assertEqual(self.pending('Sales Order'), [])

    def test_vouchers_sharing_an_order_number_and_terms_are_one_row_but_other_orders_stay_apart(self):
        self.po(order_no='PO-5', qty=100)
        self.po(order_no='PO-5', qty=60)
        self.po(order_no='PO-5', qty=10, due='2026-07-31')          # another due date: its own row
        self.po(order_no='PO-6', qty=5)
        rows = {(r['order_no'], r['due_date']): r['pending_qty'] for r in self.pending()}
        self.assertEqual(rows, {('PO-5', '2026-06-30'): 160.0, ('PO-5', '2026-07-31'): 10.0, ('PO-6', '2026-06-30'): 5.0})

    # allocation ------------------------------------------------------------------------------------
    def test_an_invoice_allocated_to_an_order_reduces_what_is_pending_until_it_closes(self):
        order = self.po()['voucher_id']
        self.purchase(order, qty=40)
        self.assertEqual(self.pending()[0]['pending_qty'], 60.0)
        self.purchase(order, qty=60, date='2026-06-20')
        self.assertEqual(self.pending(), [])                                             # fully received: gone from the report
        line = orders.order_lines(self.db, 'Purchase Order')[0]
        self.assertEqual((line['ordered_qty'], line['delivered_qty'], line['pending_qty']), (100.0, 100.0, 0.0))

    def test_over_delivery_and_mismatches_are_refused_before_anything_is_saved(self):
        order = self.po()['voucher_id']
        count = lambda: self.db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0]
        before = count()
        with self.assertRaisesRegex(ValueError, 'Only 100 of .Rice. is pending'):
            self.purchase(order, qty=101)
        with self.assertRaisesRegex(ValueError, 'is not on that order'):
            self.purchase(order, qty=1, item='Toor')
        with self.assertRaisesRegex(ValueError, 'Order not found'):
            self.purchase(99999, qty=1)
        sales_order = save_invoice(self.db, {'voucher_type': 'Sales Order', 'date': '2026-05-01', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales',
                                             'items': [{'stock_item_name': 'Rice', 'qty': 5, 'rate': 60}]})['voucher_id']
        before = count()
        with self.assertRaisesRegex(ValueError, 'cannot be allocated to a Sales Order'):
            self.purchase(sales_order, qty=1)
        self.assertEqual(count(), before)

    def test_cancelling_or_deleting_the_invoice_gives_the_quantity_back(self):
        order = self.po()['voucher_id']
        first = self.purchase(order, qty=40)['voucher_id']
        second = self.purchase(order, qty=30, date='2026-06-12')['voucher_id']
        self.assertEqual(self.pending()[0]['pending_qty'], 30.0)
        self.db.cancel_voucher(first)
        self.assertEqual(self.pending()[0]['pending_qty'], 70.0)
        self.db.delete_voucher(second)
        self.assertEqual(self.pending()[0]['pending_qty'], 100.0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM order_allocation WHERE against_voucher_id=?', (second,)).fetchone()[0], 0)

    def test_altering_an_order_keeps_its_due_date_and_what_was_received(self):
        order = self.po()['voucher_id']
        self.purchase(order, qty=40)
        self.db.alter_voucher(order, '2026-05-01', 'revised', [{'ledger_name': 'Supplier', 'entry_type': 'Cr', 'amount': 6000},
                                                               {'ledger_name': 'Purchases', 'entry_type': 'Dr', 'amount': 6000}],
                              inventory_items=[{'stock_item_name': 'Rice', 'qty': 120, 'rate': 50, 'amount': 6000}])
        row = self.pending()[0]
        self.assertEqual((row['ordered_qty'], row['pending_qty'], row['due_date']), (120.0, 80.0, '2026-06-30'))

    def test_a_sales_order_is_fulfilled_by_a_sales_invoice(self):
        order = save_invoice(self.db, {'voucher_type': 'Sales Order', 'date': '2026-05-01', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales', 'reference_no': 'SO-1',
                                       'items': [{'stock_item_name': 'Rice', 'qty': 50, 'rate': 60, 'due_date': '2026-05-20'}]})['voucher_id']
        save_invoice(self.db, {'voucher_type': 'Sales', 'date': '2026-05-10', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales',
                               'items': [{'stock_item_name': 'Rice', 'qty': 20, 'rate': 60, 'order_voucher_id': order}]})
        self.assertEqual([(r['order_no'], r['pending_qty']) for r in self.pending('Sales Order', '2026-05-31')], [('SO-1', 30.0)])
        self.assertEqual(self.pending('Sales Order', '2026-05-31')[0]['overdue_days'], 11)

    def test_party_filter_and_the_picker_lists_one_line_per_order_voucher(self):
        a = self.po(order_no='PO-1')['voucher_id']
        self.po(order_no='PO-1', date='2026-05-05', qty=10)
        self.assertEqual([l['voucher_id'] for l in orders.order_lines(self.db, 'Purchase Order', party='Supplier')][0], a)
        self.assertEqual(orders.order_lines(self.db, 'Purchase Order', party='Nobody'), [])

    def test_the_report_and_the_picker_endpoint(self):
        from engine.extension_api import get_route
        from engine.runtime import TDLRuntime
        from engine.tdl_parser import TDLSymbolTable
        order = self.po(qty=100)['voucher_id']
        self.purchase(order, qty=30)
        runtime = TDLRuntime(TDLSymbolTable(), self.db)
        for name in ('PurchaseOrders', 'Purchase Orders'):
            lines = runtime.render_report(name, to_date='2026-07-10')['parts'][0]['lines']
            row = [f['display_value'] for f in lines[1]['fields']]
            self.assertEqual(row[1:], ['PO-1', 'Supplier', 'Rice', '70 Kg', '50.00', '3,500.00', '2026-06-30', '10'])
            self.assertEqual(lines[-1]['fields'][1]['display_value'], '3,500.00')
        self.assertEqual(len(runtime.render_report('SalesOrders')['parts'][0]['lines']), 2)                # header and total only
        status, picked = get_route('/api/orders/lines', {'order_type': ['Purchase Order'], 'party': ['Supplier']}, self.db, None)
        self.assertEqual((status, [(l['voucher_id'], l['pending_qty']) for l in picked['lines']]), (200, [(order, 70.0)]))
        self.assertEqual(get_route('/api/orders/lines', {'order_type': ['Purchase Order'], 'party': ['Nobody']}, self.db, None)[1]['lines'], [])

    def test_credit_and_debit_notes_are_returns_not_deliveries_against_an_order(self):
        order = self.po()['voucher_id']
        with self.assertRaisesRegex(ValueError, 'cannot be allocated'):
            save_invoice(self.db, {'voucher_type': 'Debit Note', 'date': '2026-06-10', 'party_ledger': 'Supplier', 'purchase_ledger': 'Purchases',
                                   'items': [{'stock_item_name': 'Rice', 'qty': 1, 'rate': 50, 'order_voucher_id': order}]})

    # delivery and receipt notes ------------------------------------------------------------------------------
    def stock(self, item='Rice'):
        return self.db.get_stock_item(item)['closing_qty']

    def note(self, vtype='Delivery Note', qty=10, order=None, date='2026-05-05', rate=60):
        party, book_key, book = ('Buyer', 'sales_ledger', 'Sales') if vtype == 'Delivery Note' else ('Supplier', 'purchase_ledger', 'Purchases')
        line = {'stock_item_name': 'Rice', 'qty': qty, 'rate': rate}
        if order:
            line['order_voucher_id'] = order
        return save_invoice(self.db, {'voucher_type': vtype, 'date': date, 'party_ledger': party, book_key: book, 'items': [line]})

    def sale(self, qty, against, date='2026-05-10'):
        return save_invoice(self.db, {'voucher_type': 'Sales', 'date': date, 'party_ledger': 'Buyer', 'sales_ledger': 'Sales',
                                      'items': [{'stock_item_name': 'Rice', 'qty': qty, 'rate': 60, 'order_voucher_id': against}]})

    def test_a_delivery_note_moves_stock_but_posts_nothing_and_a_receipt_note_brings_it_in(self):
        self.db.add_voucher('Purchase', '2026-04-01', 'stock', [{'ledger_name': 'Supplier', 'entry_type': 'Cr', 'amount': 5000}, {'ledger_name': 'Purchases', 'entry_type': 'Dr', 'amount': 5000}],
                            inventory_items=[{'stock_item_name': 'Rice', 'qty': 100, 'rate': 50, 'amount': 5000}])
        note = self.note()['voucher_id']
        v = self.db.get_voucher(note)
        self.assertEqual((v['voucher_type'], v['is_optional'], self.stock()), ('Delivery Note', 1, 90.0))
        self.assertEqual(self.db.get_ledger_closing_balance('Buyer')[0], 0)               # nothing posted to the party
        self.assertEqual(self.db.get_financial_summary()['sales_total'], 0.0)
        self.note('Receipt Note', qty=5)
        self.assertEqual(self.stock(), 95.0)

    def test_an_invoice_raised_against_a_delivery_note_does_not_move_stock_a_second_time(self):
        note = self.note(qty=10)['voucher_id']
        self.assertEqual(self.stock(), -10.0)
        invoice = self.sale(6, note)['voucher_id']
        self.assertEqual(self.stock(), -10.0)                                              # note 4 + invoice 6: still ten out
        self.assertEqual(self.db.get_voucher(note)['inventory'][0]['qty'], 4.0)            # the note holds the undelivered balance
        self.assertEqual(orders.order_lines(self.db, 'Delivery Note')[0]['pending_qty'], 4.0)
        self.sale(4, note, date='2026-05-12')
        self.assertEqual((self.stock(), self.db.get_voucher(note)['inventory'][0]['qty']), (-10.0, 0.0))
        self.assertEqual(orders.order_lines(self.db, 'Delivery Note')[0]['pending_qty'], 0.0)
        self.db.cancel_voucher(invoice)                                                    # its 6 go back to the note
        self.assertEqual((self.stock(), self.db.get_voucher(note)['inventory'][0]['qty']), (-10.0, 6.0))      # still ten out: delivered on the note; the cancelled invoice's 6 are the note's again

    def test_deleting_the_invoice_restores_the_note_and_over_invoicing_is_refused(self):
        note = self.note(qty=10)['voucher_id']
        invoice = self.sale(7, note)['voucher_id']
        before = self.db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0]
        with self.assertRaisesRegex(ValueError, 'Only 3 of .Rice. is pending'):
            self.sale(4, note, date='2026-05-11')
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0], before)
        self.db.delete_voucher(invoice)
        self.assertEqual((self.stock(), self.db.get_voucher(note)['inventory'][0]['qty']), (-10.0, 10.0))
        receipt = self.note('Receipt Note', qty=5)['voucher_id']
        with self.assertRaisesRegex(ValueError, 'cannot be allocated to a Receipt Note'):
            self.sale(1, receipt)

    def test_altering_an_invoice_cannot_drop_below_what_was_taken_from_the_note_and_altering_the_note_rebases_it(self):
        note = self.note(qty=10)['voucher_id']
        invoice = self.sale(6, note)['voucher_id']
        entries = [{'ledger_name': 'Buyer', 'entry_type': 'Dr', 'amount': 240}, {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': 240}]
        with self.assertRaisesRegex(ValueError, 'cannot go below 6'):
            self.db.alter_voucher(invoice, '2026-05-10', 'x', entries, inventory_items=[{'stock_item_name': 'Rice', 'qty': 4, 'rate': 60, 'amount': 240}])
        self.assertEqual(self.db.get_voucher(invoice)['inventory'][0]['qty'], 6.0)         # refused, nothing changed
        self.db.alter_voucher(note, '2026-05-05', 'x', [{'ledger_name': 'Buyer', 'entry_type': 'Dr', 'amount': 180}, {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': 180}],
                              inventory_items=[{'stock_item_name': 'Rice', 'qty': 3, 'rate': 60, 'amount': 180, 'direction': 'Out'}])
        line = orders.order_lines(self.db, 'Delivery Note')[0]
        self.assertEqual((line['ordered_qty'], line['delivered_qty'], line['pending_qty']), (9.0, 6.0, 3.0))   # 3 outstanding + 6 invoiced
        self.assertEqual(self.stock(), -9.0)

    def test_order_then_delivery_note_then_invoice_counts_the_goods_once(self):
        order = save_invoice(self.db, {'voucher_type': 'Sales Order', 'date': '2026-05-01', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales', 'reference_no': 'SO-9',
                                       'items': [{'stock_item_name': 'Rice', 'qty': 10, 'rate': 60}]})['voucher_id']
        note = self.note(qty=10, order=order)['voucher_id']
        self.assertEqual(self.pending('Sales Order'), [])                                  # the delivery note fulfilled the order
        self.sale(10, note)
        self.assertEqual(self.stock(), -10.0)                                              # delivered once, not twice
        self.assertEqual(self.db.get_ledger_closing_balance('Buyer')[0], 600)              # and billed once
        self.assertEqual(orders.order_lines(self.db, 'Delivery Note')[0]['pending_qty'], 0.0)


if __name__ == '__main__':
    unittest.main()
