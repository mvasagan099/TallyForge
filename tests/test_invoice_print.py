"""Voucher / invoice print data (synthetic books only)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.invoice_print import amount_in_words, build_print  # noqa: E402
from engine.tally_xml_import import import_company  # noqa: E402
from test_tally_xml_import import MASTERS, day_book, envelope, write  # noqa: E402


class AmountInWordsTests(unittest.TestCase):
    def test_indian_grouping_and_paise(self):
        self.assertEqual(amount_in_words(1025000), 'Indian Rupee Ten Lakh Twenty Five Thousand Only')
        self.assertEqual(amount_in_words(105000.5), 'Indian Rupee One Lakh Five Thousand and Fifty Paise Only')
        self.assertEqual(amount_in_words(12345678.9),
                         'Indian Rupee One Crore Twenty Three Lakh Forty Five Thousand Six Hundred Seventy Eight and Ninety Paise Only')
        self.assertEqual(amount_in_words(0.07), 'Indian Rupee Seven Paise Only')
        self.assertEqual(amount_in_words(0), 'Indian Rupee Zero Only')
        self.assertEqual(amount_in_words(-100), 'Indian Rupee One Hundred Only')
        self.assertEqual(amount_in_words(100.01), 'Indian Rupee One Hundred and One Paise Only')


class BuildPrintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / 'src').mkdir()
        (root / 'data').mkdir()
        masters = [m.replace('<ISBILLWISEON>', '<MAILINGNAME>Acme Buyers Pvt Ltd</MAILINGNAME><ADDRESS.LIST><ADDRESS>12 Market Road</ADDRESS>'
                             '</ADDRESS.LIST><ISBILLWISEON>') if 'Acme Buyer' in m else m for m in MASTERS]
        vouchers = day_book()
        head = ('<PARTYGSTIN>33ABCDE1234F1Z5</PARTYGSTIN><PLACEOFSUPPLY>Tamil Nadu</PLACEOFSUPPLY><CMPGSTIN>33AAAAA0000A1Z5</CMPGSTIN>'
                '<CMPGSTSTATE>Tamil Nadu</CMPGSTSTATE>')
        vouchers[0] = vouchers[0].replace('<ALTERID>', head + '<ALTERID>', 1)
        report = import_company(root / 'data', [write(root / 'src', 'M.xml', envelope(masters)), write(root / 'src', 'D.xml', envelope(vouchers))])
        self.db = EnterpriseAccountingDB(str(Path(report['folder']) / 'company.db'), seed_demo=False)
        self.db.select_company(int(report['storage_key'].split(':')[1]))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def vid(self, vtype):
        return self.db.conn.execute('SELECT id FROM voucher WHERE voucher_type=?', (vtype,)).fetchone()[0]

    def test_sales_invoice_has_buyer_items_and_total_in_words(self):
        doc = build_print(self.db, self.vid('Sales'))
        self.assertEqual((doc['kind'], doc['title']), ('invoice', 'TAX INVOICE'))
        self.assertEqual(doc['party']['name'], 'Acme Buyers Pvt Ltd')
        self.assertEqual(doc['party']['address'], '12 Market Road')
        self.assertEqual((doc['party']['gstin'], doc['place_of_supply']), ('33ABCDE1234F1Z5', 'Tamil Nadu'))
        self.assertEqual(doc['company']['gstin'], '33AAAAA0000A1Z5')
        self.assertEqual(len(doc['items']), 1)
        self.assertEqual((doc['items'][0]['name'], doc['items'][0]['qty'], doc['items'][0]['amount']), ('Rice', 4.0, 100.0))
        self.assertEqual(doc['total'], 100.0)
        self.assertEqual(doc['amount_in_words'], 'Indian Rupee One Hundred Only')
        self.assertEqual(doc['charges'], [])            # the sales ledger carrying the item is not a separate charge
        self.assertEqual([b['name'] for b in doc['bills']], ['INV-1'])

    def test_voucher_without_items_prints_as_a_voucher(self):
        doc = build_print(self.db, self.vid('Payment'))
        self.assertEqual((doc['kind'], doc['title']), ('voucher', 'PAYMENT VOUCHER'))
        self.assertEqual({(e['ledger'], e['type'], e['amount']) for e in doc['entries']}, {('Rent', 'Dr', 1200.25), ('Cash', 'Cr', 1200.25)})
        self.assertEqual(doc['party']['ledger'], '')

    def test_a_new_voucher_carries_the_buyers_gstin_from_their_latest_earlier_invoice(self):
        # The imported sale carries the buyer's GSTIN; a voucher entered later for the same party has no stored details of its own.
        later = self.db.add_voucher('Sales', '2026-05-20', 'later', [{'ledger_name': 'Acme Buyer\r\nUnit 2', 'entry_type': 'Dr', 'amount': 50},
                                                                     {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': 50}],
                                    inventory_items=[{'stock_item_name': 'Rice', 'qty': 2, 'rate': 25, 'amount': 50}])
        doc = build_print(self.db, later)
        self.assertEqual((doc['party']['gstin'], doc['place_of_supply']), ('33ABCDE1234F1Z5', 'Tamil Nadu'))
        self.assertEqual(doc['party']['address'], '12 Market Road')
        # A party never invoiced before has nothing to carry: nothing is invented.
        self.db.add_ledger('New Buyer', 'Sundry Debtors')
        fresh = self.db.add_voucher('Sales', '2026-05-21', 'x', [{'ledger_name': 'New Buyer', 'entry_type': 'Dr', 'amount': 10},
                                                                {'ledger_name': 'Sales', 'entry_type': 'Cr', 'amount': 10}],
                                    inventory_items=[{'stock_item_name': 'Rice', 'qty': 1, 'rate': 10, 'amount': 10}])
        self.assertEqual(build_print(self.db, fresh)['party']['gstin'], '')

    def test_unknown_voucher_is_an_error(self):
        with self.assertRaises(ValueError):
            build_print(self.db, 99999)

    def test_company_without_import_tables_still_prints(self):
        self.db.conn.execute('DROP TABLE voucher_invoice')
        doc = build_print(self.db, self.vid('Sales'))
        self.assertEqual(doc['party']['gstin'], '')         # nothing is invented when Tally supplied no details
        self.assertEqual(doc['total'], 100.0)


if __name__ == '__main__':
    unittest.main()
