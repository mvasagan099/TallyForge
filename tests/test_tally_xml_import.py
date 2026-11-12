"""Tally XML importer: synthetic exports for every rule, plus an optional check against
the operator's own 100124 exports when that folder is present (skipped otherwise)."""
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.tally_xml_import import (TallyImportError, import_company, load_bundle,  # noqa: E402
                                     parse_export, reference_figures)

GUID = '0f0f0f0f-1111-2222-3333-444444444444-{:08x}'


def envelope(messages, company='Synthetic Traders'):
    return ('<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA>'
            f'<REQUESTDESC><REPORTNAME>All Masters</REPORTNAME><STATICVARIABLES><SVCURRENTCOMPANY>{company}'
            '</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC><REQUESTDATA>'
            + ''.join(f'<TALLYMESSAGE xmlns:UDF="TallyUDF">{m}</TALLYMESSAGE>' for m in messages)
            + '</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>')


def group(name, parent='', revenue='No', gross='No', n=1):
    return (f'<GROUP NAME="{name}" ACTION="Create"><GUID>{GUID.format(n)}</GUID><PARENT>{parent}</PARENT>'
            f'<ISREVENUE>{revenue}</ISREVENUE><AFFECTSGROSSPROFIT>{gross}</AFFECTSGROSSPROFIT><ALTERID>1</ALTERID></GROUP>')


def ledger(name, parent, n, opening=None):
    ob = f'<OPENINGBALANCE>{opening}</OPENINGBALANCE>' if opening is not None else ''
    return (f'<LEDGER NAME="{name}" ACTION="Create"><GUID>{GUID.format(n)}</GUID><PARENT>{parent}</PARENT>'
            f'<CURRENCYNAME>&#8377;</CURRENCYNAME>{ob}<ISBILLWISEON>Yes</ISBILLWISEON>'
            f'<EXCISELEDGERCLASSIFICATION>&#4; Not Applicable</EXCISELEDGERCLASSIFICATION><ALTERID>{n}</ALTERID></LEDGER>')


def entry(name, amount, deemed=None, bills=''):
    deemed = deemed or ('Yes' if Decimal(amount) < 0 else 'No')
    return (f'<ALLLEDGERENTRIES.LIST><LEDGERNAME>{name}</LEDGERNAME><ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>'
            f'<AMOUNT>{amount}</AMOUNTxx>'.replace('</AMOUNTxx>', '</AMOUNT>') + bills + '</ALLLEDGERENTRIES.LIST>')


def voucher(n, vtype, date, entries, number='', alter=1, extra=''):
    return (f'<VOUCHER VCHTYPE="{vtype}" ACTION="Create"><DATE>{date}</DATE><GUID>{GUID.format(1000 + n)}</GUID>'
            f'<VOUCHERTYPENAME>{vtype}</VOUCHERTYPENAME><VOUCHERNUMBER>{number}</VOUCHERNUMBER>'
            f'<ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL><ALTERID>{alter}</ALTERID>'
            f'<STATKEY>x\x05y</STATKEY>{extra}{"".join(entries)}</VOUCHER>')


def sales_with_inventory(n, date, party, amount):
    inv = (f'<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Rice</STOCKITEMNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>'
           f'<RATE>25.00/Kg</RATE><AMOUNT>{amount}</AMOUNT><ACTUALQTY> 4 Kg</ACTUALQTY><BILLEDQTY> 4 Kg</BILLEDQTY>'
           f'<ACCOUNTINGALLOCATIONS.LIST><LEDGERNAME>Sales</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>'
           f'<AMOUNT>{amount}</AMOUNT></ACCOUNTINGALLOCATIONS.LIST></ALLINVENTORYENTRIES.LIST>')
    bills = f'<BILLALLOCATIONS.LIST><NAME>INV-{n}</NAME><BILLTYPE>New Ref</BILLTYPE><AMOUNT>-{amount}</AMOUNT></BILLALLOCATIONS.LIST>'
    party_entry = (f'<LEDGERENTRIES.LIST><LEDGERNAME>{party}</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>'
                   f'<AMOUNT>-{amount}</AMOUNT>{bills}</LEDGERENTRIES.LIST>')
    return voucher(n, 'Sales', date, [inv, party_entry], number=f'S/{n}')


MASTERS = [
    group('Capital Account', n=1), group('Current Assets', n=2), group('Sales Accounts', revenue='Yes', gross='Yes', n=3),
    group('Indirect Expenses', revenue='Yes', n=4), group('Bank Accounts', 'Current Assets', n=5),
    group('Sundry Debtors', 'Current Assets', n=6), group('Cash-in-Hand', 'Current Assets', n=7),
    group('Office Costs', 'Indirect Expenses', revenue='Yes', n=8),
    ledger('Owner Capital', 'Capital Account', 101, '250000.00'),
    ledger('Main Bank', 'Bank Accounts', 102, '-200000.00'),
    ledger('Cash', 'Cash-in-Hand', 103, '-50000.00'),
    ledger('Acme Buyer&#13;&#10;Unit 2', 'Sundry Debtors', 104),
    ledger('Sales', 'Sales Accounts', 105),
    ledger('Rent', 'Office Costs', 106),
    ledger('Profit &amp; Loss A/c', '', 107),
]


def day_book(**kw):
    return [
        sales_with_inventory(1, '20260405', 'Acme Buyer&#13;&#10;Unit 2', '100.00'),
        voucher(2, 'Receipt', '20260406', [entry('Main Bank', '-60.50'), entry('Acme Buyer&#13;&#10;Unit 2', '60.50')], 'R/1'),
        voucher(3, 'Payment', '20260407', [entry('Rent', '-1200.25'), entry('Cash', '1200.25')], 'P/1', **kw),
        voucher(4, 'Journal', '20260408', [entry('Cash', '-0.10'), entry('Main Bank', '0.10'), entry('Rent', '0')], 'J/1'),
        voucher(5, 'Contra', '20260409', [entry('Cash', '-0.07'), entry('Main Bank', '0.07')], ''),
    ]


def write(folder, name, text, encoding='utf-16'):
    path = Path(folder) / name
    path.write_bytes(text.encode(encoding))
    return path


class TallyXmlImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / 'src'
        self.data = Path(self.tmp.name) / 'data'
        self.src.mkdir()
        self.data.mkdir()
        self.master = write(self.src, 'Master.xml', envelope(MASTERS))
        self.daybook = write(self.src, 'DayBook.xml', envelope(day_book()))

        self.conns = []

    def tearDown(self):
        for conn in self.conns:
            conn.close()
        self.tmp.cleanup()

    def db(self, report):
        conn = sqlite3.connect(Path(report['folder']) / 'company.db')
        conn.row_factory = sqlite3.Row
        self.conns.append(conn)
        return conn

    def test_parse_controls_signs_and_inventory(self):
        parsed = parse_export(self.daybook.read_bytes(), 'DayBook.xml')
        self.assertEqual(parsed['companies'], ['Synthetic Traders'])
        sale = parsed['vouchers'][0]
        self.assertEqual(sale['inventory'][0]['qty'], Decimal('4'))
        self.assertEqual(sale['inventory'][0]['unit'], 'Kg')
        self.assertEqual(sale['inventory'][0]['direction'], 'Out')
        ledgers = {e['ledger']: e['amount'] for e in sale['entries']}
        self.assertEqual(ledgers['Acme Buyer\r\nUnit 2'], Decimal('-100.00'))  # debit
        self.assertEqual(ledgers['Sales'], Decimal('100.00'))  # credit
        self.assertEqual(parsed['vouchers'][3]['zero_entries'], 1)
        masters = parse_export(self.master.read_bytes(), 'Master.xml')
        self.assertIsNone(masters['ledgers'][3]['opening'])
        self.assertEqual(masters['ledgers'][0]['opening'], Decimal('250000.00'))

    def test_import_verifies_all_voucher_types_and_openings(self):
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.src.iterdir()}
        report = import_company(self.data, [self.master, self.daybook])
        self.assertTrue(report['verification']['ok'], report['verification']['checks'])
        self.assertTrue(report['created'])
        self.assertEqual(report['verification']['reference']['vouchers_by_type'],
                         {'Sales': 1, 'Receipt': 1, 'Payment': 1, 'Journal': 1, 'Contra': 1})
        conn = self.db(report)
        self.assertEqual(conn.execute("SELECT opening_balance, opening_balance_type FROM ledger WHERE name='Main Bank'").fetchone()[:], (200000.0, 'Dr'))
        self.assertEqual(conn.execute("SELECT opening_balance_type FROM ledger WHERE name='Owner Capital'").fetchone()[0], 'Cr')
        unknown = conn.execute("SELECT COUNT(*) FROM tally_import_record WHERE kind='ledger' AND opening_known=0").fetchone()[0]
        self.assertEqual(unknown, 4)  # 3 book ledgers + the group-less P&L ledger
        self.assertEqual(conn.execute("SELECT group_name FROM ledger WHERE name LIKE 'Profit%'").fetchone()[0], 'Primary (ungrouped ledgers)')
        self.assertEqual(conn.execute("SELECT parent, is_revenue FROM account_group WHERE name='Office Costs'").fetchone()[:], ('Indirect Expenses', 1))
        self.assertEqual(conn.execute('SELECT ref_name, amount, direction FROM bill_allocation').fetchone()[:], ('INV-1', 100.0, 'Dr'))
        self.assertEqual(conn.execute('SELECT stock_item_name, qty, direction FROM voucher_inventory').fetchone()[:], ('Rice', 4.0, 'Out'))
        tb = reference_figures(load_bundle([self.master, self.daybook]))['trial_balance']
        self.assertEqual(tb['debit'], tb['credit'])
        # Capital 250,000 Cr + Sales 100 Cr = Bank 200,060.33 + Cash 48,799.92 + Acme 39.50 + Rent 1,200.25 Dr
        self.assertEqual(tb['debit'], Decimal('250100.00'))
        conn.close()
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.src.iterdir()}
        self.assertEqual(before, after)

    def test_rerun_is_idempotent(self):
        first = import_company(self.data, [self.master, self.daybook])
        counts = lambda: {t: self.db(first).execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                          for t in ('voucher', 'voucher_entry', 'bill_allocation', 'voucher_inventory', 'ledger', 'account_group', 'stock_item')}
        snapshot = counts()
        second = import_company(self.data, [self.master, self.daybook])
        self.assertFalse(second['created'])
        self.assertEqual(second['folder'], first['folder'])
        self.assertEqual(second['import']['counts']['vouchers_unchanged'], 5)
        self.assertNotIn('vouchers_created', second['import']['counts'])
        self.assertNotIn('ledgers_updated', second['import']['counts'])
        self.assertEqual(counts(), snapshot)
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), ['10000'])

    def test_financial_year_covers_every_voucher_and_is_extended_by_later_imports(self):
        first = import_company(self.data, [self.master, self.daybook])
        row = lambda: self.db(first).execute('SELECT financial_year_from, financial_year_to FROM company').fetchone()
        self.assertEqual(tuple(row()), ('2026-04-01', '2027-03-31'))
        later = write(self.src, 'Later.xml', envelope([voucher(40, 'Journal', '20280510', [entry('Rent', '-1.00'), entry('Cash', '1.00')], 'J/40')]))
        import_company(self.data, [later])
        self.assertEqual(tuple(row()), ('2026-04-01', '2029-03-31'))      # 10 May 2028 is in the year ending 31 March 2029

    def test_invoice_header_details_are_kept_for_printing(self):
        masters = [m.replace('<ISBILLWISEON>', '<MAILINGNAME>Acme Buyers Pvt Ltd</MAILINGNAME><ADDRESS.LIST><ADDRESS>12 Market Road</ADDRESS>'
                             '<ADDRESS>Chennai-600107</ADDRESS></ADDRESS.LIST><COUNTRYNAME>India</COUNTRYNAME><ISBILLWISEON>')
                   if 'Acme Buyer' in m else m for m in MASTERS]
        head = ('<PARTYMAILINGNAME>Acme Buyers Pvt Ltd</PARTYMAILINGNAME><PARTYGSTIN>33ABCDE1234F1Z5</PARTYGSTIN>'
                '<PLACEOFSUPPLY>Tamil Nadu</PLACEOFSUPPLY><CONSIGNEEMAILINGNAME>Acme Godown</CONSIGNEEMAILINGNAME>'
                '<CMPGSTIN>33AAAAA0000A1Z5</CMPGSTIN><CMPGSTSTATE>Tamil Nadu</CMPGSTSTATE><BASICORDERREF>PO-77</BASICORDERREF>')
        vouchers = day_book()
        vouchers[0] = vouchers[0].replace('<ALTERID>', head + '<ALTERID>', 1)
        report = import_company(self.data, [write(self.src, 'M2.xml', envelope(masters)), write(self.src, 'D2.xml', envelope(vouchers))])
        conn = self.db(report)
        import json
        details = json.loads(conn.execute("SELECT details_json FROM voucher_invoice").fetchone()[0])
        self.assertEqual((details['gstin'], details['place_of_supply'], details['consignee_name'], details['order_ref']),
                         ('33ABCDE1234F1Z5', 'Tamil Nadu', 'Acme Godown', 'PO-77'))
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM voucher_invoice').fetchone()[0], 1)      # vouchers without details have none
        ledger_row = conn.execute("SELECT address, udf_data FROM ledger WHERE name LIKE 'Acme Buyer%'").fetchone()
        self.assertEqual(ledger_row[0], '12 Market Road\nChennai-600107')
        self.assertEqual(json.loads(ledger_row[1]), {'country': 'India', 'mailing': 'Acme Buyers Pvt Ltd'})
        self.assertEqual(tuple(conn.execute('SELECT gstin, state, state_code FROM company').fetchone()), ('33AAAAA0000A1Z5', 'Tamil Nadu', '33'))

    def test_a_voucher_without_a_guid_is_keyed_by_its_content_and_reimports_unchanged(self):
        import re
        bare = re.sub(r'<GUID>[^<]*</GUID>', '', voucher(50, 'Journal', '20260420', [entry('Rent', '-3.00'), entry('Cash', '3.00')], 'J/50'))
        no_guid = write(self.src, 'NoGuid.xml', envelope([bare]))
        first = import_company(self.data, [self.master, no_guid])
        self.assertEqual(first['import']['counts']['vouchers_created'], 1)
        count = lambda: self.db(first).execute("SELECT COUNT(*) FROM voucher WHERE voucher_number='J/50'").fetchone()[0]
        self.assertEqual(count(), 1)
        again = import_company(self.data, [self.master, no_guid])
        self.assertEqual(again['import']['counts'].get('vouchers_unchanged'), 1)
        self.assertNotIn('vouchers_created', again['import']['counts'])
        self.assertEqual(count(), 1)

    def test_changed_voucher_is_replaced_not_duplicated(self):
        first = import_company(self.data, [self.master, self.daybook])
        changed = list(day_book())
        changed[2] = voucher(3, 'Payment', '20260407', [entry('Rent', '-1500.00'), entry('Cash', '1500.00')], 'P/1', alter=2)
        write(self.src, 'DayBook.xml', envelope(changed))
        second = import_company(self.data, [self.master, self.daybook])
        self.assertEqual(second['import']['counts']['vouchers_replaced'], 1)
        self.assertTrue(second['verification']['ok'])
        conn = self.db(first)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM voucher WHERE voucher_number='P/1'").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT total_amount FROM voucher WHERE voucher_number='P/1'").fetchone()[0], 1500.0)

    def test_unbalanced_voucher_rejected_without_suspense(self):
        rows = day_book() + [voucher(9, 'Journal', '20260410', [entry('Cash', '-10.00'), entry('Main Bank', '9.99')], 'J/2')]
        write(self.src, 'DayBook.xml', envelope(rows))
        report = import_company(self.data, [self.master, self.daybook])
        self.assertEqual(report['import']['counts']['vouchers_rejected_unbalanced'], 1)
        self.assertEqual(report['import']['unbalanced_rejected'][0]['difference'], '-0.01')
        self.assertTrue(report['verification']['ok'])
        conn = self.db(report)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ledger WHERE name LIKE '%uspense%'").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM voucher WHERE voucher_number='J/2'").fetchone()[0], 0)

    def test_postings_to_a_groupless_ledger_are_kept(self):
        rows = day_book() + [voucher(9, 'Journal', '20260412', [entry('Profit &amp; Loss A/c', '-5.00'), entry('Cash', '5.00')], 'J/9')]
        write(self.src, 'DayBook.xml', envelope(rows))
        report = import_company(self.data, [self.master, self.daybook])
        self.assertTrue(report['verification']['ok'], report['verification']['checks'])
        conn = self.db(report)
        self.assertEqual(conn.execute("SELECT entry_type, amount FROM voucher_entry WHERE ledger_name='Profit & Loss A/c'").fetchone()[:], ('Dr', 5.0))

    def test_non_posting_vouchers_are_kept_but_excluded_from_balances(self):
        def vtype(name, parent, n):
            return f'<VOUCHERTYPE NAME="{name}"><GUID>{GUID.format(900 + n)}</GUID><PARENT>{parent}</PARENT></VOUCHERTYPE>'
        masters = MASTERS + [vtype('Purchase Order', 'Purchase Order', 1), vtype('Import Order', 'Purchase Order', 2),
                             vtype('Sales', 'Sales', 3)]
        order = voucher(8, 'Purchase Order', '20260412', [entry('Rent', '-777.00'), entry('Cash', '777.00')], 'PO/1')
        custom = voucher(9, 'Import Order', '20260413', [entry('Rent', '-5.00'), entry('Cash', '5.00')], 'IO/1')
        stock = voucher(10, 'Stock Journal', '20260414', [], 'SJ/1')
        write(self.src, 'Master.xml', envelope(masters))
        write(self.src, 'DayBook.xml', envelope(day_book() + [order, custom, stock]))
        report = import_company(self.data, [self.master, self.daybook])
        self.assertTrue(report['verification']['ok'], report['verification']['checks'])
        self.assertEqual(report['import']['counts'].get('vouchers_rejected_unbalanced', 0), 0)
        self.assertEqual(report['import']['non_posting_vouchers_kept_out_of_balances'],
                         {'Purchase Order': 1, 'Import Order': 1, 'Stock Journal': 1})
        conn = self.db(report)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM voucher WHERE voucher_type IN ('Purchase Order','Import Order','Stock Journal') AND is_optional=1").fetchone()[0], 3)
        # Rent is still only the 1,200.25 posted by the Payment voucher.
        self.assertEqual(conn.execute("SELECT closing FROM (SELECT SUM(CASE entry_type WHEN 'Dr' THEN amount ELSE -amount END) closing FROM voucher_entry e JOIN voucher v ON v.id=e.voucher_id WHERE e.ledger_name='Rent' AND v.is_optional=0)").fetchone()[0], 1200.25)

    def test_foreign_currency_amount_rejected_and_rolled_back(self):
        rows = day_book() + [voucher(9, 'Receipt', '20260410', [entry('Cash', '-$10 @ &#8377; 80/$ = -&#8377; 800', 'Yes'), entry('Sales', '800')])]
        write(self.src, 'DayBook.xml', envelope(rows))
        with self.assertRaisesRegex(TallyImportError, 'unsupported amount format'):
            import_company(self.data, [self.master, self.daybook])
        self.assertEqual(list(self.data.iterdir()), [])

    def test_sign_flag_disagreement_is_counted_but_amount_sign_wins(self):
        # Real TallyPrime data: Round Off with ISDEEMEDPOSITIVE=No and a negative (debit) amount.
        rows = [voucher(9, 'Journal', '20260410', [entry('Cash', '-10.00', deemed='No'), entry('Main Bank', '10.00')], 'J/9')]
        write(self.src, 'DayBook.xml', envelope(rows))
        report = import_company(self.data, [self.master, self.daybook])
        self.assertEqual(report['import']['counts']['entries_flag_disagrees_with_sign'], 1)
        self.assertTrue(report['verification']['ok'])
        row = self.db(report).execute("SELECT entry_type FROM voucher_entry e JOIN ledger l ON l.name=e.ledger_name WHERE l.name='Cash'").fetchone()
        self.assertEqual(row[0], 'Dr')

    def test_flag_disagreement_cannot_hide_an_unbalanced_voucher(self):
        rows = [voucher(9, 'Journal', '20260410', [entry('Cash', '-10.00', deemed='No'), entry('Main Bank', '9.99')], 'J/9')]
        write(self.src, 'DayBook.xml', envelope(rows))
        report = import_company(self.data, [self.master, self.daybook])
        self.assertEqual(report['import']['counts']['vouchers_rejected_unbalanced'], 1)

    def test_conflicting_duplicates_across_files_rejected(self):
        other = write(self.src, 'Transactions.xml', envelope(
            [voucher(3, 'Payment', '20260407', [entry('Rent', '-1.00'), entry('Cash', '1.00')], 'P/1')]))
        with self.assertRaisesRegex(TallyImportError, 'Conflicting duplicate'):
            load_bundle([self.master, self.daybook, other])
        same = write(self.src, 'Transactions.xml', envelope([day_book()[2]]))
        self.assertEqual(load_bundle([self.master, self.daybook, same])['duplicates']['vouchers'], 1)

    def test_unknown_ledger_rejected(self):
        write(self.src, 'DayBook.xml', envelope([voucher(9, 'Journal', '20260410', [entry('Ghost', '-1.00'), entry('Cash', '1.00')])]))
        with self.assertRaisesRegex(TallyImportError, 'absent from the master export'):
            import_company(self.data, [self.master, self.daybook])
        self.assertEqual(list(self.data.iterdir()), [])

    def test_utf8_export_and_dtd_rejection(self):
        utf8 = write(self.src, 'DayBook.xml', envelope(day_book()), encoding='utf-8')
        self.assertEqual(len(parse_export(utf8.read_bytes())['vouchers']), 5)
        with self.assertRaises(TallyImportError):
            parse_export(b'<!DOCTYPE x [<!ENTITY a "b">]><ENVELOPE/>')


REAL = ROOT / '100124'


@unittest.skipUnless((REAL / 'Master.xml').is_file(), 'operator reference exports not present')
class RealReferenceExportTests(unittest.TestCase):
    def test_reference_company_imports_and_reconciles(self):
        files = [REAL / n for n in ('Master.xml', 'DayBook.xml', 'Transactions.xml') if (REAL / n).is_file()]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        with tempfile.TemporaryDirectory() as tmp:
            report = import_company(tmp, files)
            self.assertTrue(report['verification']['ok'], report['verification']['checks'])
            counts = report['import']['counts']
            self.assertGreater(counts['ledgers_created'], 0)
            self.assertGreater(counts['vouchers_created'], 0)
            ref = report['verification']['reference']['trial_balance']
            self.assertEqual(ref['debit'], ref['credit'])
            again = import_company(tmp, files)
            self.assertEqual(again['import']['counts']['vouchers_unchanged'], counts['vouchers_created'])
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in files])


if __name__ == '__main__':
    unittest.main()
