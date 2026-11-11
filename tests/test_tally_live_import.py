"""Live-import path with a fake TallyPrime transport (synthetic data only; no network)."""
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import tally_bridge  # noqa: E402
from engine.tally_live import (import_from_tally, month_ranges, parse_ledger_balances,  # noqa: E402
                               parse_trial_balance, Source)
from engine.tally_xml_import import TallyImportError  # noqa: E402
from test_tally_xml_import import (MASTERS, entry, envelope, ledger, voucher)  # noqa: E402

CO = 'Synthetic Traders'
HEAD = '<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DESC><CMPINFO><COMPANY>0</COMPANY></CMPINFO></DESC><DATA><COLLECTION>{}</COLLECTION></DATA></BODY></ENVELOPE>'

# Masters as TallyPrime exports them: no OPENINGBALANCE element.
LIVE_MASTERS = [m for m in MASTERS if 'OPENINGBALANCE' not in m]
LIVE_MASTERS = [m.replace('<OPENINGBALANCE>250000.00</OPENINGBALANCE>', '')
                 .replace('<OPENINGBALANCE>-200000.00</OPENINGBALANCE>', '')
                 .replace('<OPENINGBALANCE>-50000.00</OPENINGBALANCE>', '') for m in MASTERS]

VOUCHERS = [
    voucher(1, 'Sales', '20260405', [entry('Acme Buyer&#13;&#10;Unit 2', '-100.00', bills=(
        '<BILLALLOCATIONS.LIST><NAME>INV-1</NAME><BILLTYPE>New Ref</BILLTYPE><AMOUNT>-100.00</AMOUNT></BILLALLOCATIONS.LIST>'
        '<BILLALLOCATIONS.LIST>      </BILLALLOCATIONS.LIST>')), entry('Sales', '100.00')], 'S/1'),
    voucher(2, 'Receipt', '20260406', [entry('Main Bank', '-60.50'), entry('Acme Buyer&#13;&#10;Unit 2', '60.50', bills=(
        '<BILLALLOCATIONS.LIST><NAME/><BILLTYPE>On Account</BILLTYPE><AMOUNT>60.50</AMOUNT></BILLALLOCATIONS.LIST>'))], 'R/1'),
    voucher(3, 'Payment', '20260407', [entry('Rent', '-1200.25'), entry('Cash', '1200.25')], 'P/1'),
    voucher(4, 'Journal', '20260408', [entry('Cash', '-0.10'), entry('Main Bank', '0.10')], 'J/1'),
    voucher(5, 'Contra', '20260409', [entry('Cash', '-0.07'), entry('Main Bank', '0.07')], 'C/1'),
    # Cancelled and optional vouchers must never reach the trial balance.
    voucher(6, 'Payment', '20260410', [entry('Rent', '-999.00'), entry('Cash', '999.00')], 'P/2').replace(
        '<ISCANCELLED>No</ISCANCELLED>', '<ISCANCELLED>Yes</ISCANCELLED>'),
    voucher(7, 'Journal', '20260411', [entry('Rent', '-5.00'), entry('Cash', '5.00')], 'J/2').replace(
        '<ISOPTIONAL>No</ISOPTIONAL>', '<ISOPTIONAL>Yes</ISOPTIONAL>'),
]


def tb_rows(rows):
    """rows: [(name, net_debit)] -> Trial Balance report XML in TallyPrime's layout."""
    out = '<ENVELOPE>'
    for name, net in rows:
        net = Decimal(net)
        dr, cr = (f'{-net:.2f}', '') if net > 0 else ('', f'{-net:.2f}')
        name = name.replace('&', '&amp;')
        out += (f'<DSPACCNAME><DSPDISPNAME>{name}</DSPDISPNAME></DSPACCNAME><DSPACCINFO><DSPCLDRAMT><DSPCLDRAMTA>{dr}'
                f'</DSPCLDRAMTA></DSPCLDRAMT><DSPCLCRAMT><DSPCLCRAMTA>{cr}</DSPCLCRAMTA></DSPCLCRAMT></DSPACCINFO>')
    return (out + '</ENVELOPE>').encode('utf-8')


# Hand-computed (not produced by the importer): opening Capital Cr 250,000; Bank Dr 200,000; Cash Dr 50,000.
OPEN_ROWS = [('Capital Account', '-250000.00'), ('Owner Capital', '-250000.00'), ('Current Assets', '250000.00'),
             ('Bank Accounts', '200000.00'), ('Main Bank', '200000.00'), ('Cash-in-Hand', '50000.00'), ('Cash', '50000.00')]
CLOSE_ROWS = [('Capital Account', '-250000.00'), ('Owner Capital', '-250000.00'),
              ('Current Assets', '248899.75'), ('Bank Accounts', '200060.33'), ('Main Bank', '200060.33'),
              ('Cash-in-Hand', '48799.92'), ('Cash', '48799.92'), ('Sundry Debtors', '39.50'), ('Acme Buyer- Unit 2', '39.50'),
              ('Sales Accounts', '-100.00'), ('Sales', '-100.00'), ('Indirect Expenses', '1200.25'), ('Office Costs', '1200.25'), ('Rent', '1200.25')]
# Tally's ledger collection: credit positive, debit negative.
LEDGER_BALANCES = {'Owner Capital': ('250000.00', '250000.00'), 'Main Bank': ('-200000.00', '-200060.33'),
                   'Cash': ('-50000.00', '-48799.92'), 'Acme Buyer\r\nUnit 2': ('0.00', '-39.50'),
                   'Sales': ('0.00', '100.00'), 'Rent': ('0.00', '-1200.25')}


def ledger_collection(balances=LEDGER_BALANCES):
    items = ''
    for l in LIVE_MASTERS:
        if l.startswith('<LEDGER'):
            name = ET.fromstring('<r>' + l.replace('&#4;', '') + '</r>').find('LEDGER').get('NAME')
            op, cl = balances.get(name, ('0.00', '0.00'))
            safe = name.replace('&', '&amp;').replace('<', '&lt;').replace('"', '&quot;').replace(chr(13) + chr(10), '&#13;&#10;')
            items += (f'<LEDGER NAME="{safe}"><PARENT>x</PARENT>'
                      f'<CLOSINGBALANCE>{cl}</CLOSINGBALANCE><OPENINGBALANCE>{op}</OPENINGBALANCE></LEDGER>')
    return HEAD.format(items).encode('utf-8')


def census_xml(vs=VOUCHERS):
    out = ''
    for v in vs:
        node = ET.fromstring('<r>' + v.replace('\x05', '') + '</r>').find('VOUCHER')
        out += ''.join([f'<VOUCHER><GUID>{node.findtext("GUID")}</GUID><DATE>{node.findtext("DATE")}</DATE>',
                        f'<VOUCHERTYPENAME>{node.findtext("VOUCHERTYPENAME")}</VOUCHERTYPENAME>',
                        f'<ISCANCELLED>{node.findtext("ISCANCELLED")}</ISCANCELLED><ISOPTIONAL>{node.findtext("ISOPTIONAL")}</ISOPTIONAL>',
                        '<ISDELETED>No</ISDELETED></VOUCHER>'])
    return HEAD.format(out).encode('utf-8')


class FakeTally:
    """Answers read-only export requests like TallyPrime. Records every request body."""

    def __init__(self, **overrides):
        self.requests = []
        self.overrides = overrides
        self.company = ('<COMPANY NAME="%s"><NAME TYPE="String">%s</NAME><GUID TYPE="String">g-1</GUID>'
                        '<STARTINGFROM TYPE="Date">20260401</STARTINGFROM><BOOKSFROM TYPE="Date">20260401</BOOKSFROM>'
                        '<ENDINGAT TYPE="Date">20270331</ENDINGAT><LASTVOUCHERDATE TYPE="Date">20260411</LASTVOUCHERDATE></COMPANY>' % (CO, CO))

    def __call__(self, endpoint, body, **kw):
        self.requests.append(body)
        root = ET.fromstring(body)
        key = root.findtext('HEADER/ID')
        variables = {n.tag: n.text for n in root.find('BODY/DESC/STATICVARIABLES')}
        if key in self.overrides:
            value = self.overrides[key]
            if isinstance(value, Exception):
                raise value
            return value(variables) if callable(value) else value
        if key == 'TFCompany':
            return HEAD.format(self.company).encode()
        if key == 'List of Accounts':
            return envelope([m for m in LIVE_MASTERS]).encode('utf-16')
        if key == 'TFLedgers':
            return ledger_collection()
        if key == 'TFCensus':
            return census_xml()
        if key == 'Trial Balance':
            return tb_rows(OPEN_ROWS if variables['SVTODATE'] == variables['SVFROMDATE'] else CLOSE_ROWS)
        if key == 'TFVouchers':
            month = variables['SVFROMDATE'][:6]
            inside = ''.join(v for v in VOUCHERS if f'<DATE>{month}' in v)
            return HEAD.format(inside).encode('utf-8')
        raise AssertionError('unexpected request ' + str(key))


class LiveImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / 'data'
        self.data.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_import(self, tally=None, **kw):
        tally = tally or FakeTally()
        report = import_from_tally(self.data, CO, endpoint='http://127.0.0.1:9000', transport=tally, **kw)
        return tally, report

    def failed(self, report):
        return [c for c in report['verification']['checks'] if not c['ok']]

    def test_live_import_matches_tally_trial_balance(self):
        tally, report = self.run_import()
        self.assertEqual(self.failed(report), [], report['verification']['checks'])
        self.assertTrue(report['verification']['ok'])
        names = [c['check'] for c in report['verification']['checks']]
        for expected in ('opening trial balance', 'every ledger row listed by TallyPrime', 'every group total',
                         'no unexplained nonzero rows', 'Profit & Loss A/c row', 'ledger-collection closing'):
            self.assertTrue(any(expected in n for n in names), expected)
        self.assertEqual(report['verification']['tally_reference']['tally_top_level_net_debit'], '0.00')
        self.assertEqual(report['live']['opening_filled_from_ledger_collection'], 7)  # 6 books + the group-less P&L ledger
        self.assertEqual(report['live']['opening_still_unknown'], 0)
        self.assertEqual(report['live']['census'].get('Payment/cancelled'), 1)
        self.assertEqual(report['live']['census'].get('Journal/optional'), 1)
        self.assertEqual(report['import']['counts']['vouchers_created'], 7)
        # 1 company + masters + ledgers + census + 2 trial balances + 1 voucher month
        self.assertEqual(len(tally.requests), 7)

    def test_bill_references_are_requested_and_imported(self):
        tally, report = self.run_import()
        voucher_requests = [b for b in tally.requests if b'TFVouchers' in b]
        self.assertTrue(voucher_requests)
        for method in (b'AllLedgerEntries.BillAllocations.Name', b'AllLedgerEntries.BillAllocations.BillType',
                       b'AllLedgerEntries.BillAllocations.Amount'):
            self.assertIn(method, voucher_requests[0])
        import sqlite3
        conn = sqlite3.connect(Path(report['folder']) / 'company.db')
        try:
            rows = conn.execute('SELECT ledger_name, ref_type, ref_name, amount, direction FROM bill_allocation').fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [('Acme Buyer\r\nUnit 2', 'New Ref', 'INV-1', 100.0, 'Dr'),
                                ('Acme Buyer\r\nUnit 2', 'On Account', '', 60.5, 'Cr')])

    def test_only_read_only_export_requests_are_ever_sent(self):
        tally, _ = self.run_import()
        for body in tally.requests:
            root = ET.fromstring(body)
            self.assertEqual(root.findtext('HEADER/TALLYREQUEST'), 'Export')
            self.assertFalse([n.tag for n in root.iter() if n.tag in ('IMPORTDATA', 'TALLYMESSAGE', 'VOUCHER', 'LEDGER')])
            self.assertFalse([n for n in root.iter() if 'ACTION' in n.attrib])
            self.assertFalse([n.text for n in root.iter() if n.tag in ('NATIVEMETHOD', 'FETCH') and '*' in (n.text or '')])
            for c in root.iter('COLLECTION'):
                self.assertEqual(c.get('ISMODIFY'), 'No')

    def test_bridge_guard_refuses_writes_and_wildcards(self):
        bad = [b'<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER></ENVELOPE>',
               b'<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER><BODY><IMPORTDATA/></BODY></ENVELOPE>',
               b'<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER><BODY><DESC><X><VOUCHER ACTION="Delete"/></X></DESC></BODY></ENVELOPE>',
               tally_bridge.collection_request('X', 'Voucher', ['GUID'], 'Co').replace(b'ISMODIFY="No"', b'ISMODIFY="Yes"')]
        for body in bad:
            with self.assertRaises(ValueError):
                tally_bridge.assert_read_only(body)
        with self.assertRaises(ValueError):
            tally_bridge.collection_request('X', 'Voucher', ['GUID'], 'Co', fetch=['AllLedgerEntries.*'])
        with self.assertRaises(ValueError):
            tally_bridge.collection_request('X', 'Voucher', ['*'], 'Co')
        tally_bridge.assert_read_only(tally_bridge.report_request('Trial Balance', 'Co', {'SVFROMDATE': '20260401'}))

    def test_one_paisa_difference_from_tally_trial_balance_fails(self):
        rows = [(n, v if n not in ('Main Bank', 'Bank Accounts') else '200060.34') for n, v in CLOSE_ROWS]
        tally = FakeTally(**{'Trial Balance': lambda v: tb_rows(OPEN_ROWS if v['SVTODATE'] == v['SVFROMDATE'] else rows)})
        _, report = self.run_import(tally)
        self.assertFalse(report['verification']['ok'])
        failing = [c['check'] for c in self.failed(report)]
        self.assertTrue(any('every ledger row listed by TallyPrime' in c for c in failing), failing)
        self.assertTrue(any('every group total' in c for c in failing), failing)

    def test_one_paisa_difference_in_a_group_total_fails(self):
        rows = [(n, v if n != 'Sales Accounts' else '-100.01') for n, v in CLOSE_ROWS]
        tally = FakeTally(**{'Trial Balance': lambda v: tb_rows(OPEN_ROWS if v['SVTODATE'] == v['SVFROMDATE'] else rows)})
        _, report = self.run_import(tally)
        failing = [c['check'] for c in self.failed(report)]
        self.assertTrue(any('every group total' in c for c in failing), failing)
        self.assertFalse(report['verification']['ok'])

    def test_ledger_collection_disagreement_fails(self):
        bad = dict(LEDGER_BALANCES, Cash=('-50000.00', '-48799.91'))
        tally = FakeTally(TFLedgers=ledger_collection(bad))
        _, report = self.run_import(tally)
        self.assertFalse(report['verification']['ok'])
        self.assertTrue(any('ledger-collection' in c['check'] for c in self.failed(report)))

    def test_voucher_missing_from_fetch_is_detected(self):
        tally = FakeTally(TFVouchers=lambda v: HEAD.format(''.join(VOUCHERS[:4])).encode())
        _, report = self.run_import(tally)
        self.assertFalse(report['verification']['ok'])
        self.assertTrue(any('census was fetched' in c['check'] for c in self.failed(report)))

    def test_empty_opening_trial_balance_means_all_zero(self):
        zero = {n: ('0.00', cl) for n, (op, cl) in LEDGER_BALANCES.items()}
        tally = FakeTally(TFLedgers=ledger_collection(zero),
                          **{'Trial Balance': lambda v: b'<ENVELOPE></ENVELOPE>' if v['SVTODATE'] == v['SVFROMDATE'] else tb_rows(CLOSE_ROWS)})
        _, report = self.run_import(tally)
        opening = [c for c in report['verification']['checks'] if c['check'].startswith('opening trial balance')]
        self.assertTrue(opening[0]['ok'])
        self.assertIn('empty report', opening[0]['check'])

    def test_profit_and_loss_row_that_vouchers_cannot_explain_is_reported(self):
        # Real TallyPrime reports a Profit & Loss A/c balancing row; balanced vouchers cannot produce one.
        rows = CLOSE_ROWS + [('Profit & Loss A/c', '239.27'), ('Sales', '-100.00')][:1]
        rows = [r for r in rows if r[0] != 'Capital Account'] + [('Capital Account', '-250239.27')]
        tally = FakeTally(**{'Trial Balance': lambda v: tb_rows(OPEN_ROWS if v['SVTODATE'] == v['SVFROMDATE'] else rows)})
        _, report = self.run_import(tally)
        failing = [c['check'] for c in self.failed(report)]
        self.assertTrue(any(c.startswith('Profit & Loss A/c row') for c in failing), failing)
        self.assertFalse(report['verification']['ok'])

    def test_unexpected_trial_balance_row_and_signs_are_not_guessed(self):
        rows = CLOSE_ROWS + [('Mystery Ledger', '7.00')]
        tally = FakeTally(**{'Trial Balance': lambda v: tb_rows(OPEN_ROWS if v['SVTODATE'] == v['SVFROMDATE'] else rows)})
        _, report = self.run_import(tally)
        self.assertTrue(any('no unexplained nonzero rows' in c['check'] for c in self.failed(report)))
        positive_debit = (b'<ENVELOPE><DSPACCNAME><DSPDISPNAME>A</DSPDISPNAME></DSPACCNAME><DSPACCINFO><DSPCLDRAMT>'
                          b'<DSPCLDRAMTA>5.00</DSPCLDRAMTA></DSPCLDRAMT><DSPCLCRAMT><DSPCLCRAMTA></DSPCLCRAMTA></DSPCLCRAMT></DSPACCINFO></ENVELOPE>')
        with self.assertRaisesRegex(TallyImportError, 'unexpected sign'):
            parse_trial_balance(positive_debit)

    def test_trial_balance_debit_column_is_negative(self):
        raw = tb_rows([('A', '10.05')])
        self.assertIn(b'<DSPCLDRAMTA>-10.05</DSPCLDRAMTA>', raw)
        self.assertEqual(parse_trial_balance(raw), {'A': Decimal('10.05')})

    def test_voucher_listed_by_several_sources_is_not_double_counted(self):
        """A live collection lists the same postings in ALLLEDGERENTRIES.LIST, LEDGERENTRIES.LIST and the
        inventory allocation (seen on a real TallyPrime). They must collapse to one posting each."""
        from engine.tally_xml_import import parse_export

        def led(tag, name, amount, deemed):
            return (f'<{tag}><LEDGERNAME>{name}</LEDGERNAME><ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>'
                    f'<AMOUNT>{amount}</AMOUNT></{tag}>')
        alloc = ('<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>Rice</STOCKITEMNAME><ACTUALQTY> 4 Kg</ACTUALQTY><AMOUNT>100.00</AMOUNT>'
                 '<ACCOUNTINGALLOCATIONS.LIST><LEDGERNAME>Sales</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>'
                 '<AMOUNT>100.00</AMOUNT></ACCOUNTINGALLOCATIONS.LIST></ALLINVENTORYENTRIES.LIST>')
        body = ('<VOUCHER VCHTYPE="Sales"><DATE>20260405</DATE><GUID>g-live-1</GUID><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>'
                '<VOUCHERNUMBER>S/9</VOUCHERNUMBER>' + alloc + led('LEDGERENTRIES.LIST', 'Acme', '-100.00', 'Yes')
                + led('ALLLEDGERENTRIES.LIST', 'Acme', '-100.00', 'Yes') + led('ALLLEDGERENTRIES.LIST', 'Sales', '100.00', 'No')
                + '</VOUCHER>')
        live = parse_export(HEAD.format(body).encode())['vouchers'][0]
        self.assertEqual(sorted((e['ledger'], str(e['amount'])) for e in live['entries']),
                         [('Acme', '-100.00'), ('Sales', '100.00')])
        # File-export shape: party in LEDGERENTRIES, sales only inside the allocation -> still two postings.
        file_shape = ('<VOUCHER VCHTYPE="Sales"><DATE>20260405</DATE><GUID>g-file-1</GUID><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>'
                      + alloc + led('LEDGERENTRIES.LIST', 'Acme', '-100.00', 'Yes') + '</VOUCHER>')
        filed = parse_export(HEAD.format(file_shape).encode())['vouchers'][0]
        self.assertEqual(sorted((e['ledger'], str(e['amount'])) for e in filed['entries']),
                         [('Acme', '-100.00'), ('Sales', '100.00')])
        # Two genuinely identical postings inside ONE list are both kept.
        twin = ('<VOUCHER VCHTYPE="Journal"><DATE>20260405</DATE><GUID>g-twin-1</GUID><VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>'
                + led('ALLLEDGERENTRIES.LIST', 'Cash', '-5.00', 'Yes') * 2 + led('ALLLEDGERENTRIES.LIST', 'Bank', '10.00', 'No') + '</VOUCHER>')
        kept = parse_export(HEAD.format(twin).encode())['vouchers'][0]
        self.assertEqual(len(kept['entries']), 3)

    def test_profit_and_loss_postings_are_compared_with_tallys_pl_row(self):
        extra = voucher(8, 'Journal', '20260411', [entry('Profit &amp; Loss A/c', '-5.00'), entry('Cash', '5.00')], 'J/8')
        vs = VOUCHERS + [extra]
        rows = [(n, v) for n, v in CLOSE_ROWS if n not in ('Cash', 'Cash-in-Hand', 'Current Assets')] + [
            ('Cash', '48794.92'), ('Cash-in-Hand', '48794.92'), ('Current Assets', '248894.75'), ('Profit & Loss A/c', '5.00')]
        balances = dict(LEDGER_BALANCES, Cash=('-50000.00', '-48794.92'))
        tally = FakeTally(TFCensus=census_xml(vs), TFLedgers=ledger_collection(balances),
                          TFVouchers=lambda v: HEAD.format(''.join(x for x in vs if '<DATE>' + v['SVFROMDATE'][:6] in x)).encode(),
                          **{'Trial Balance': lambda v: tb_rows(OPEN_ROWS if v['SVTODATE'] == v['SVFROMDATE'] else rows)})
        _, report = self.run_import(tally)
        self.assertEqual(self.failed(report), [], report['verification']['checks'])
        pl = [c for c in report['verification']['checks'] if c['check'].startswith('Profit & Loss A/c row')][0]
        self.assertTrue(pl['ok'])
        self.assertEqual(pl['expected'], '5.00')

    def test_aggregate_ledger_line_versus_itemised_allocations(self):
        """Real TallyPrime: the complete list shows Sales as one aggregate line while the inventory allocations
        carry the same sales as item pieces. Summing both would double the sales and unbalance the voucher."""
        from engine.tally_xml_import import parse_export

        def led(tag, name, amount):
            return f'<{tag}><LEDGERNAME>{name}</LEDGERNAME><AMOUNT>{amount}</AMOUNT></{tag}>'

        def piece(item, amount):
            return (f'<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>{item}</STOCKITEMNAME><AMOUNT>{amount}</AMOUNT>'
                    + led('ACCOUNTINGALLOCATIONS.LIST', 'Sales', amount) + '</ALLINVENTORYENTRIES.LIST>')
        body = ('<VOUCHER VCHTYPE="Sales"><DATE>20260405</DATE><GUID>g-agg-1</GUID><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>'
                + piece('A', '60.00') + piece('B', '40.00')
                + led('LEDGERENTRIES.LIST', 'Acme', '-110.00') + led('LEDGERENTRIES.LIST', 'GST', '10.00')
                + led('ALLLEDGERENTRIES.LIST', 'Acme', '-110.00') + led('ALLLEDGERENTRIES.LIST', 'Sales', '100.00')
                + led('ALLLEDGERENTRIES.LIST', 'GST', '10.00') + '</VOUCHER>')
        v = parse_export(HEAD.format(body).encode())['vouchers'][0]
        self.assertEqual(sorted((e['ledger'], str(e['amount'])) for e in v['entries']),
                         [('Acme', '-110.00'), ('GST', '10.00'), ('Sales', '100.00')])
        # A voucher no reading can balance is left unbalanced so the importer rejects it (never patched).
        broken = body.replace('-110.00', '-109.00')
        v = parse_export(HEAD.format(broken).encode())['vouchers'][0]
        self.assertNotEqual(sum(e['amount'] for e in v['entries']), 0)

    def test_first_failure_aborts_without_retry_and_creates_nothing(self):
        tally = FakeTally(**{'List of Accounts': OSError('timed out')})
        with self.assertRaisesRegex(TallyImportError, 'Stopping without retry'):
            self.run_import(tally)
        self.assertEqual(len(tally.requests), 2)  # company info, then the failing masters request
        self.assertEqual(list(self.data.iterdir()), [])

    def test_wrong_company_is_refused(self):
        tally = FakeTally()
        tally.company = tally.company.replace(CO, 'Another Company')
        with self.assertRaisesRegex(TallyImportError, 'not .Synthetic Traders'):
            self.run_import(tally)

    def test_rerun_is_idempotent(self):
        self.run_import()
        _, again = self.run_import()
        self.assertFalse(again['created'])
        self.assertEqual(again['import']['counts']['vouchers_unchanged'], 7)
        self.assertNotIn('vouchers_created', again['import']['counts'])
        self.assertTrue(again['verification']['ok'])

    def test_capture_then_offline_replay(self):
        cap = Path(self.tmp.name) / 'capture'
        tally, first = self.run_import(capture_dir=cap)
        self.assertTrue((cap / 'manifest.json').is_file())
        # Offline: no endpoint, no transport. A different data folder gets an identical import.
        other = Path(self.tmp.name) / 'data2'
        other.mkdir()
        replay = import_from_tally(other, CO, replay_dir=cap)
        self.assertEqual(replay['mode'], 'replay')
        self.assertEqual(replay['requests'], [])
        self.assertTrue(replay['verification']['ok'], self.failed(replay))
        self.assertEqual(replay['import']['counts'], first['import']['counts'])
        with self.assertRaisesRegex(TallyImportError, 'empty'):
            Source(endpoint='http://127.0.0.1:9000', transport=tally, capture_dir=cap)

    def test_tampered_capture_is_rejected(self):
        cap = Path(self.tmp.name) / 'capture'
        self.run_import(capture_dir=cap)
        target = cap / 'masters.xml'
        target.write_bytes(target.read_bytes() + b' ')
        with self.assertRaisesRegex(TallyImportError, 'modified'):
            import_from_tally(Path(self.tmp.name) / 'data3', CO, replay_dir=cap)

    def test_source_requires_exactly_one_origin(self):
        with self.assertRaises(TallyImportError):
            Source()
        with self.assertRaises(TallyImportError):
            Source(endpoint='http://127.0.0.1:9000', replay_dir=self.tmp.name)

    def test_parsers_and_month_ranges(self):
        self.assertEqual(parse_trial_balance(b'<ENVELOPE></ENVELOPE>'), {})
        self.assertEqual(parse_trial_balance(tb_rows([('A', '10.05'), ('B', '-3.00')])), {'A': Decimal('10.05'), 'B': Decimal('-3.00')})
        with self.assertRaises(TallyImportError):
            parse_trial_balance(tb_rows([('A', '1'), ('A', '1')]))
        self.assertEqual(parse_ledger_balances(ledger_collection())['Main Bank']['closing'], Decimal('-200060.33'))
        self.assertEqual(list(month_ranges('2026-01-15', '2026-03-10')),
                         [('2026-01-15', '2026-01-31'), ('2026-02-01', '2026-02-28'), ('2026-03-01', '2026-03-10')])


if __name__ == '__main__':
    unittest.main()
