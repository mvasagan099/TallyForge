"""Altering and deleting masters: renames follow every reference, reserved groups are protected (synthetic books only)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import masters_edit, price_lists  # noqa: E402
from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.invoice_entry import save_invoice  # noqa: E402


class MasterEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = EnterpriseAccountingDB(str(Path(self.tmp.name) / 'books.db'), seed_demo=False)
        self.db.select_company(self.db.create_company(name='Edit Co', state='Tamil Nadu', state_code='33'))
        for name, group in (('Sales', 'Sales Accounts'), ('Purchases', 'Purchase Accounts'), ('Buyer', 'Sundry Debtors'), ('Rent', 'Indirect Expenses'),
                            ('Spare', 'Indirect Expenses'), ('Bank', 'Bank Accounts')):
            self.db.add_ledger(name, group)
        self.db.add_stock_item('Rice', uom_symbol='Kg', gst_rate=0)
        self.db.set_negative_stock_policy(True)
        self.sale = save_invoice(self.db, {'voucher_type': 'Sales', 'date': '2026-05-10', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales',
                                           'items': [{'stock_item_name': 'Rice', 'qty': 10, 'rate': 60}]})['voucher_id']

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def balance(self, ledger):
        return float(self.db.get_ledger_closing_balance(ledger)[0])

    # ledgers -------------------------------------------------------------------------------------------------
    def test_renaming_a_ledger_moves_its_vouchers_bills_and_price_list(self):
        price_lists.create(self.db, 'Wholesale')
        price_lists.assign(self.db, 'Buyer', 'Wholesale')
        before = self.db.get_voucher(self.sale)['total_amount']
        masters_edit.alter(self.db, 'ledgers', 'Buyer', {'new_name': 'Buyer Traders', 'credit_period_days': 30})
        self.assertEqual((self.balance('Buyer Traders'), self.balance('Buyer')), (600.0, 0.0))
        v = self.db.get_voucher(self.sale)
        self.assertEqual(v['total_amount'], before)
        self.assertIn('Buyer Traders', [e['ledger_name'] for e in v['entries']])
        self.assertEqual(price_lists.party_price_list(self.db, 'Buyer Traders'), 'Wholesale')
        self.assertEqual(masters_edit.detail(self.db, 'ledgers', 'Buyer Traders')['credit_period_days'], 30)

    def test_changing_a_ledgers_group_reclassifies_it_and_bad_input_is_refused_without_a_trace(self):
        masters_edit.alter(self.db, 'ledgers', 'Spare', {'group_name': 'Direct Expenses', 'opening_balance': 250, 'opening_balance_type': 'Dr'})
        self.assertEqual((masters_edit.detail(self.db, 'ledgers', 'Spare')['group_name'], self.balance('Spare')), ('Direct Expenses', 250.0))
        for bad, message in (({'new_name': 'Rent'}, 'already exists'), ({'group_name': 'No Such'}, 'does not exist'),
                             ({'gstin': 'NOTAGSTIN'}, 'GST format'), ({'opening_balance': -5}, 'cannot be negative'),
                             ({'opening_balance_type': 'X'}, 'Dr or Cr'), ({'new_name': ' '}, 'cannot be empty')):
            with self.assertRaisesRegex(ValueError, message):
                masters_edit.alter(self.db, 'ledgers', 'Spare', {**bad, 'credit_limit': 999})
        self.assertEqual(masters_edit.detail(self.db, 'ledgers', 'Spare')['credit_limit'], 0.0)      # nothing from the refused calls stuck
        masters_edit.alter(self.db, 'ledgers', 'Spare', {'gstin': '33aaaaa0000a1z5'})
        d = masters_edit.detail(self.db, 'ledgers', 'Spare')
        self.assertEqual((d['gstin'], d['state_code']), ('33AAAAA0000A1Z5', '33'))

    def test_a_ledger_used_in_a_voucher_cannot_be_deleted_an_unused_one_can(self):
        with self.assertRaisesRegex(ValueError, 'used in vouchers'):
            masters_edit.delete(self.db, 'ledgers', 'Buyer')
        masters_edit.delete(self.db, 'ledgers', 'Spare')
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            masters_edit.detail(self.db, 'ledgers', 'Spare')

    # stock items ---------------------------------------------------------------------------------------------
    def test_renaming_a_stock_item_keeps_its_stock_and_vouchers(self):
        self.assertEqual(self.db.get_stock_item('Rice')['closing_qty'], -10.0)
        masters_edit.alter(self.db, 'stock_items', 'Rice', {'new_name': 'Basmati Rice', 'hsn_code': '1006', 'gst_rate': 5})
        self.assertEqual(self.db.get_stock_item('Basmati Rice')['closing_qty'], -10.0)
        self.assertEqual(self.db.get_voucher(self.sale)['inventory'][0]['stock_item_name'], 'Basmati Rice')
        d = masters_edit.detail(self.db, 'stock_items', 'Basmati Rice')
        self.assertEqual((d['hsn_code'], d['gst_rate'], d['used_in_vouchers']), ('1006', 5.0, True))

    def test_stock_item_rules_and_opening_value(self):
        masters_edit.alter(self.db, 'stock_items', 'Rice', {'opening_qty': 100, 'opening_rate': 40})
        d = masters_edit.detail(self.db, 'stock_items', 'Rice')
        self.assertEqual((d['opening_qty'], d['opening_value']), (100.0, 4000.0))
        for bad, message in (({'hsn_code': '10A6'}, 'digits only'), ({'uom_symbol': 'Zz'}, 'does not exist'), ({'gst_rate': -1}, 'cannot be negative')):
            with self.assertRaisesRegex(ValueError, message):
                masters_edit.alter(self.db, 'stock_items', 'Rice', bad)
        with self.assertRaisesRegex(ValueError, 'used in vouchers'):
            masters_edit.delete(self.db, 'stock_items', 'Rice')
        self.db.add_stock_item('Unused', uom_symbol='Kg')
        masters_edit.delete(self.db, 'stock_items', 'Unused')

    # groups --------------------------------------------------------------------------------------------------
    def test_groups_follow_tallyprime_reserved_rules(self):
        with self.assertRaisesRegex(ValueError, 'reserved'):
            masters_edit.alter(self.db, 'groups', 'Sundry Debtors', {'new_name': 'Customers'})
        with self.assertRaisesRegex(ValueError, 'reserved'):
            masters_edit.delete(self.db, 'groups', 'Bank Accounts')
        self.db.add_group('Staff Costs', 'Indirect Expenses')
        self.db.add_group('Wages', 'Staff Costs')
        with self.assertRaisesRegex(ValueError, 'under itself'):
            masters_edit.alter(self.db, 'groups', 'Staff Costs', {'parent': 'Wages'})
        masters_edit.alter(self.db, 'ledgers', 'Rent', {'group_name': 'Staff Costs'})
        masters_edit.alter(self.db, 'groups', 'Staff Costs', {'new_name': 'People Costs'})
        self.assertEqual(masters_edit.detail(self.db, 'ledgers', 'Rent')['group_name'], 'People Costs')
        self.assertEqual(masters_edit.detail(self.db, 'groups', 'Wages')['parent'], 'People Costs')
        with self.assertRaisesRegex(ValueError, 'still has'):
            masters_edit.delete(self.db, 'groups', 'People Costs')
        masters_edit.delete(self.db, 'groups', 'Wages')

    def test_the_edit_trail_records_the_change_and_the_api_enforces_roles(self):
        from engine.extension_api import get_route, post_route
        masters_edit.alter(self.db, 'ledgers', 'Spare', {'credit_limit': 10})
        trail = self.db.conn.execute("SELECT details FROM edit_log WHERE entity_type = 'Ledger' AND action = 'ALTER'").fetchall()
        self.assertTrue(any('Spare' in r[0] for r in trail))
        self.assertEqual(post_route('/api/masters/alter', {'type': 'ledgers', 'name': 'Spare', 'changes': {'credit_limit': 5}}, self.db, None, {'role': 'Viewer'})[0], 403)
        self.assertEqual(post_route('/api/masters/alter', {'type': 'ledgers', 'name': 'Spare', 'changes': {'credit_limit': 5}}, self.db, None, {'role': 'Admin'})[0], 200)
        status, body = get_route('/api/masters/detail', {'type': ['ledgers'], 'name': ['Spare']}, self.db, None)
        self.assertEqual((status, body['credit_limit']), (200, 5.0))

    # units, godowns and cost centres ---------------------------------------------------------------------------
    def test_units_follow_their_stock_items_and_a_unit_in_use_cannot_be_deleted(self):
        masters_edit.alter(self.db, 'units', 'Kg', {'new_name': 'KGS', 'formal_name': 'Kilograms', 'decimal_places': 2})
        self.assertEqual(masters_edit.detail(self.db, 'stock_items', 'Rice')['uom_symbol'], 'KGS')
        self.assertEqual((masters_edit.detail(self.db, 'units', 'KGS')['formal_name'], masters_edit.detail(self.db, 'units', 'KGS')['used_in_vouchers']), ('Kilograms', True))
        with self.assertRaisesRegex(ValueError, 'in use'):
            masters_edit.delete(self.db, 'units', 'KGS')
        with self.assertRaisesRegex(ValueError, 'between 0 and 5'):
            masters_edit.alter(self.db, 'units', 'KGS', {'decimal_places': 9})

    def test_a_renamed_godown_moves_the_stock_lines_and_the_default_one_is_protected(self):
        self.db.add_godown('Store A', 'Chennai')
        save_invoice(self.db, {'voucher_type': 'Sales', 'date': '2026-05-11', 'party_ledger': 'Buyer', 'sales_ledger': 'Sales',
                               'items': [{'stock_item_name': 'Rice', 'qty': 1, 'rate': 60, 'godown_name': 'Store A'}]})
        masters_edit.alter(self.db, 'godowns', 'Store A', {'new_name': 'Store B', 'location': 'Madurai'})
        names = [r[0] for r in self.db.conn.execute('SELECT DISTINCT godown_name FROM voucher_inventory')]
        self.assertIn('Store B', names)
        self.assertNotIn('Store A', names)
        self.assertEqual(masters_edit.detail(self.db, 'godowns', 'Store B')['location'], 'Madurai')
        with self.assertRaisesRegex(ValueError, 'default location'):
            masters_edit.alter(self.db, 'godowns', 'Main Location', {'new_name': 'Head Office'})
        with self.assertRaisesRegex(ValueError, 'in use'):
            masters_edit.delete(self.db, 'godowns', 'Store B')
        with self.assertRaisesRegex(ValueError, 'cannot be the parent'):
            masters_edit.alter(self.db, 'godowns', 'Store B', {'parent': 'Store B'})

    def test_cost_centres_can_be_renamed_and_deleted_when_unused(self):
        self.db.conn.execute("INSERT INTO cost_centre(name, category_name, parent) VALUES ('North', 'Regions', 'Primary')")
        self.db.conn.execute("INSERT INTO cost_centre(name, category_name, parent) VALUES ('North-1', 'Regions', 'North')")
        masters_edit.alter(self.db, 'cost_centres', 'North', {'new_name': 'North Zone'})
        self.assertEqual(masters_edit.detail(self.db, 'cost_centres', 'North-1')['parent'], 'North Zone')
        with self.assertRaisesRegex(ValueError, 'still has'):
            masters_edit.delete(self.db, 'cost_centres', 'North Zone')
        masters_edit.delete(self.db, 'cost_centres', 'North-1')
        masters_edit.delete(self.db, 'cost_centres', 'North Zone')


if __name__ == '__main__':
    unittest.main()
