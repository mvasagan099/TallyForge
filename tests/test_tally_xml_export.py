"""Tally XML exporter and its round trip (synthetic data only)."""
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.tally_xml_export import compare_snapshots, export_company_xml, round_trip, snapshot  # noqa: E402
from engine.tally_xml_import import import_company  # noqa: E402
from test_tally_xml_import import MASTERS, day_book, entry, envelope, ledger, voucher, write  # noqa: E402

ODD = 'Odd&#4;Name'          # a Tally name with a control character
ODD_REAL = 'Odd\x04Name'


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dbs = []

    def tearDown(self):
        for db in self.dbs:
            db.close()
        self.tmp.cleanup()

    def imported(self, vouchers, masters=None):
        src = self.root / 'src'
        src.mkdir(exist_ok=True)
        (self.root / 'data').mkdir(exist_ok=True)
        m = write(src, 'Master.xml', envelope(masters or MASTERS))
        d = write(src, 'DayBook.xml', envelope(vouchers))
        report = import_company(self.root / 'data', [m, d])
        db = EnterpriseAccountingDB(str(Path(report['folder']) / 'company.db'), seed_demo=False)
        db.select_company(int(report['storage_key'].split(':')[1]))
        self.dbs.append(db)
        return db

    def test_round_trip_of_an_imported_company_is_lossless(self):
        extra = (voucher(20, 'Journal', '20260420', [entry(ODD, '-7.00'), entry('Cash', '7.00')], 'J/20'),
                 voucher(21, 'Purchase Order', '20260421', [entry('Rent', '-9.00'), entry('Cash', '9.00')], 'PO/1'),
                 voucher(22, 'Payment', '20260422', [entry('Rent', '-3.00'), entry('Cash', '3.00')], 'P/22').replace(
                     '<ISCANCELLED>No</ISCANCELLED>', '<ISCANCELLED>Yes</ISCANCELLED>'),
                 voucher(23, 'Journal', '20260423', [entry('Rent', '-1.00'), entry('Cash', '1.00')], 'J/23').replace(
                     '<ISOPTIONAL>No</ISOPTIONAL>', '<ISOPTIONAL>Yes</ISOPTIONAL>'))
        db = self.imported(day_book() + list(extra), MASTERS + [ledger(ODD, 'Indirect Expenses', 150)])
        result = round_trip(db, self.root / 'rt')
        self.assertTrue(result['ok'], result['differences'])
        self.assertTrue(result['reimport_verification_ok'])
        self.assertEqual(result['counts']['vouchers'], 9)
        snap = snapshot(db)
        self.assertIn(ODD_REAL, snap['ledgers'])
        self.assertTrue(any(v['bills'] for v in snap['vouchers'].values()))
        self.assertTrue(any(v['inventory'] for v in snap['vouchers'].values()))
        self.assertTrue(any(v['cancelled'] for v in snap['vouchers'].values()))

    def test_line_breaks_and_control_characters_survive_as_character_references(self):
        db = self.imported(day_book(), MASTERS + [ledger(ODD, 'Indirect Expenses', 150)])
        data = export_company_xml(db)
        text = data.decode('utf-8')
        self.assertNotIn('\r', text)
        self.assertNotIn('\x04', text)
        self.assertIn('&#4;', text)
        self.assertIn('&#13;', text)
        # &#4; is not valid strict XML 1.0, but it is exactly how TallyPrime writes such characters;
        # the Tally-style reader restores the names exactly.
        from engine.tally_xml_import import parse_export
        names = {l['name'] for l in parse_export(data)['ledgers']}
        self.assertIn(ODD_REAL, names)
        self.assertIn('Acme Buyer' + chr(13) + chr(10) + 'Unit 2', names)

    def test_all_vouchers_are_exported_not_just_the_first_thousand(self):
        many = [voucher(100 + i, 'Journal', '20260401', [entry('Rent', '-1.00'), entry('Cash', '1.00')], f'M/{i}') for i in range(1205)]
        db = self.imported(many)
        root = ET.fromstring(export_company_xml(db))
        self.assertEqual(len(root.findall('.//VOUCHER')), 1205)

    def test_amounts_use_tally_signs_and_exact_decimals(self):
        db = self.imported(day_book())
        root = ET.fromstring(export_company_xml(db))
        payment = next(v for v in root.iter('VOUCHER') if v.findtext('VOUCHERNUMBER') == 'P/1')
        amounts = {e.findtext('LEDGERNAME'): (e.findtext('AMOUNT'), e.findtext('ISDEEMEDPOSITIVE')) for e in payment.findall('ALLLEDGERENTRIES.LIST')}
        self.assertEqual(amounts, {'Rent': ('-1200.25', 'Yes'), 'Cash': ('1200.25', 'No')})
        self.assertEqual(root.findtext('HEADER/TALLYREQUEST'), 'Import Data')
        self.assertEqual(root.findtext('BODY/IMPORTDATA/REQUESTDESC/STATICVARIABLES/SVCURRENTCOMPANY'), 'Synthetic Traders')

    def test_company_created_inside_tallyforge_round_trips_with_derived_guids(self):
        db = EnterpriseAccountingDB(str(self.root / 'native.db'), seed_demo=False)
        self.dbs.append(db)
        cid = db.create_company(name='Native Co')
        db.select_company(cid)
        db.add_ledger('Cash', 'Cash-in-Hand')
        db.add_ledger('Rent', 'Indirect Expenses')
        db.add_voucher('Journal', '2026-04-05', 'Pay rent', [{'ledger_name': 'Rent', 'entry_type': 'Dr', 'amount': 120.5},
                                                              {'ledger_name': 'Cash', 'entry_type': 'Cr', 'amount': 120.5}])
        result = round_trip(db, self.root / 'rt2')
        self.assertTrue(result['ok'], result['differences'])
        self.assertEqual(result['counts']['vouchers'], 1)

    def test_invoice_details_and_addresses_survive_the_round_trip(self):
        masters = [m.replace('<ISBILLWISEON>', '<MAILINGNAME>Acme Buyers Pvt Ltd</MAILINGNAME><ADDRESS.LIST><ADDRESS>12 Market Road</ADDRESS>'
                             '<ADDRESS>Chennai</ADDRESS></ADDRESS.LIST><COUNTRYNAME>India</COUNTRYNAME><ISBILLWISEON>')
                   if 'Acme Buyer' in m else m for m in MASTERS]
        vouchers = day_book()
        vouchers[0] = vouchers[0].replace('<ALTERID>', '<PARTYGSTIN>33ABCDE1234F1Z5</PARTYGSTIN><PLACEOFSUPPLY>Tamil Nadu</PLACEOFSUPPLY>'
                                          '<CONSIGNEEMAILINGNAME>Acme Godown</CONSIGNEEMAILINGNAME><CMPGSTIN>33AAAAA0000A1Z5</CMPGSTIN>'
                                          '<ADDRESS.LIST><ADDRESS>Buyer Lane 1</ADDRESS></ADDRESS.LIST><ALTERID>', 1)
        db = self.imported(vouchers, masters)
        text = export_company_xml(db).decode('utf-8')
        for expected in ('<PARTYGSTIN>33ABCDE1234F1Z5</PARTYGSTIN>', '<CONSIGNEEMAILINGNAME>Acme Godown</CONSIGNEEMAILINGNAME>',
                         '<MAILINGNAME>Acme Buyers Pvt Ltd</MAILINGNAME>', '<ADDRESS>12 Market Road</ADDRESS>', '<ADDRESS>Buyer Lane 1</ADDRESS>'):
            self.assertIn(expected, text)
        result = round_trip(db, self.root / 'rt3')
        self.assertTrue(result['ok'], result['differences'])
        snap = snapshot(db)
        self.assertTrue(any('33ABCDE1234F1Z5' in v['invoice'] for v in snap['vouchers'].values()))

    def test_comparison_reports_what_differs(self):
        db = self.imported(day_book())
        a = snapshot(db)
        b = snapshot(db)
        self.assertTrue(compare_snapshots(a, b)['ok'])
        guid = next(iter(b['vouchers']))
        b['vouchers'][guid] = dict(b['vouchers'][guid], narration='changed')
        del b['ledgers']['Rent']
        report = compare_snapshots(a, b)
        self.assertFalse(report['ok'])
        self.assertEqual(report['differences']['vouchers']['changed'], [guid])
        self.assertEqual(report['differences']['ledgers']['only_in_original'], ['Rent'])


if __name__ == '__main__':
    unittest.main()
