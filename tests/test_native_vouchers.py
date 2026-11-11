"""Batch native voucher reader on synthetic native files (no real company data)."""
import datetime
import hashlib
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.native_vouchers import (compare_with_bundle, ledger_nets, read_native_vouchers,  # noqa: E402
                                    select_active)
from engine.tally_xml_import import NON_POSTING_KINDS  # noqa: E402
from test_native_records import make_page, seal, records_fixture  # noqa: E402
from test_native_reference import COMPANY_GUID, container, integer, text  # noqa: E402
from test_native_voucher import allocation  # noqa: E402

NAMES = ((10, 'Ledger A'), (11, 'Ledger B'), (38, 'Sales'), (39, 'Payment'), (40, 'Purchase Order'), (41, 'Journal'))
TYPE = {name: seq for seq, name in NAMES}


def serial(iso):
    return (datetime.date.fromisoformat(iso) - datetime.date(1899, 12, 31)).days


def build(folder, vouchers):
    """vouchers: dicts(seq, master, alter, type, date, guid(bool), entries[(ledger_seq, raw_int)])."""
    records_fixture(folder)
    pages = [make_page(0)]
    for number, (seq, name) in enumerate(NAMES, start=1):
        page = bytearray(make_page(number, seq=seq))
        page[28:32] = (20).to_bytes(4, 'little')
        body = text(507, COMPANY_GUID) + integer(504, 0x600, 22) + integer(3, 0x300, 0) + container(3, text(2, name))
        page[70:70 + len(body)] = body
        pages.append(seal(page, number))
    (folder / 'Manager.1800').write_bytes(b''.join(pages))
    out = [make_page(0)]
    for i, v in enumerate(vouchers):
        n = 1 + i * 3
        header = bytearray(make_page(n, seq=v['seq']))
        header[28:48] = b''.join(x.to_bytes(4, 'little') for x in (24, 1, 3, n + 1, n + 2))
        primary = bytearray(make_page(n + 1, seq=v['seq'], object_type=66))
        primary[24:28] = b'\x0b\xff\xff\0'
        body = text(3, format(serial(v['date']), '08x') + '-00000007-000000b0')
        if v.get('guid', True):
            body += text(3004, COMPANY_GUID)
        body += integer(3003, 0x600, v['master']) + integer(3004, 0x600, v['alter'])
        body += integer(103, 0x0d00, serial(v['date'])) + integer(203, 0x300, TYPE[v['type']])
        primary[28:28 + len(body)] = body
        secondary = bytearray(make_page(n + 2, seq=v['seq'], object_type=66))
        secondary[24:28] = b'\x0b\xff\xff\0'
        if v['entries']:
            body = container(10003, container(2, b''.join(allocation(s, raw) for s, raw in v['entries'])))
            secondary[28:28 + len(body)] = body
        out += [seal(header, n), seal(primary, n + 1), seal(secondary, n + 2)]
    (folder / 'TranMgr.1800').write_bytes(b''.join(out))


V = [
    dict(seq=100, master=41, alter=42, type='Sales', date='2026-04-01', entries=[(10, -10000000), (11, 10000000)]),
    # master 50 was altered: the older version has a lower alter id and different amounts.
    dict(seq=101, master=50, alter=3, type='Payment', date='2026-04-02', entries=[(10, 1000000), (11, -1000000)]),
    dict(seq=110, master=50, alter=5, type='Payment', date='2026-04-02', entries=[(10, 2550000), (11, -2550000)]),
    dict(seq=102, master=60, alter=9, type='Payment', date='2026-04-03', guid=False, entries=[(10, 777700000), (11, -777700000)]),
    dict(seq=103, master=70, alter=1, type='Purchase Order', date='2026-04-04', entries=[(10, 500000), (11, -500000)]),
    dict(seq=104, master=80, alter=1, type='Journal', date='2026-04-05', entries=[]),
]


class NativeVoucherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / '100124'
        build(self.folder, V)
        self.native = read_native_vouchers(self.root, 'native:100124')

    def tearDown(self):
        self.temp.cleanup()

    def test_every_transaction_record_is_decoded_with_type_date_and_versions(self):
        by_seq = {r['sequence']: r for r in self.native['records']}
        self.assertEqual(len(by_seq), 6)
        sales = by_seq[100]
        self.assertEqual((sales['type'], sales['date'], sales['master_id'], sales['alter_id']), ('Sales', '2026-04-01', 41, 42))
        self.assertEqual(sales['guid'], COMPANY_GUID + '-00000029')
        self.assertEqual([e[1] for e in sales['entries']], ['-100.00000', '100.00000'])
        self.assertTrue(sales['balanced'])
        self.assertFalse(by_seq[102]['has_company_guid'])
        self.assertIn('error', by_seq[104])   # no postings at all: reason recorded, nothing invented
        self.assertNotIn('entries', by_seq[104])
        self.assertFalse(self.native['source_files_written'])

    def test_active_selection_keeps_latest_version_and_drops_records_without_the_guid_field(self):
        active = select_active(self.native['records'])
        masters = {r['master_id']: r for r in active}
        self.assertEqual(sorted(masters), [41, 50, 70, 80])
        self.assertEqual(masters[50]['alter_id'], 5)      # newest version wins
        self.assertNotIn(60, masters)                      # no company-GUID field: treated as deleted history

    def test_ledger_nets_skip_non_posting_and_undecoded_vouchers_and_say_so(self):
        active = select_active(self.native['records'])
        nets, skipped = ledger_nets(active, NON_POSTING_KINDS)
        # Sales debits Ledger A 100 (negative amount = debit); the newest Payment credits A 25.50.
        self.assertEqual(nets, {'Ledger A': Decimal('100.00000') - Decimal('25.50000'), 'Ledger B': Decimal('-100.00000') + Decimal('25.50000')})
        self.assertEqual(skipped, {'non-posting type': 1, 'postings not decoded': 1})

    def test_comparison_with_a_reference_bundle(self):
        active = select_active(self.native['records'])

        def v(guid_n, vtype, date, alter, entries):
            return {'guid': COMPANY_GUID + '-' + format(guid_n, '08x'), 'type': vtype, 'date': date, 'alter_id': str(alter),
                    'entries': [{'ledger': l, 'amount': Decimal(a)} for l, a in entries]}
        truth = {x['guid']: x for x in (
            v(41, 'Sales', '2026-04-01', 42, [('Ledger A', '-100.00'), ('Ledger B', '100.00')]),
            v(50, 'Payment', '2026-04-02', 5, [('Ledger A', '25.50'), ('Ledger B', '-25.50')]),
            v(80, 'Journal', '2026-04-05', 1, [('Ledger A', '-1.00'), ('Ledger B', '1.00')]),
            v(99, 'Receipt', '2026-04-09', 1, []))}
        counts, mismatches = compare_with_bundle(active, {'vouchers': truth})
        self.assertEqual(counts['matched by GUID'], 3)
        self.assertEqual(counts['per-ledger net equal'], 2)
        self.assertEqual(counts['postings not decoded'], 1)       # the Journal
        self.assertEqual(counts['truth voucher missing from native'], 1)
        self.assertEqual(counts['native active but not in truth'], 1)  # the Purchase Order
        self.assertEqual(mismatches, {})
        truth[COMPANY_GUID + '-00000029']['entries'][0]['amount'] = Decimal('-100.01')
        counts, mismatches = compare_with_bundle(active, {'vouchers': truth})
        self.assertEqual(counts['per-ledger net DIFFERS'], 1)
        self.assertEqual(mismatches['net'], [COMPANY_GUID + '-00000029'])

    def test_native_files_are_never_modified(self):
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir() if p.is_file()}
        read_native_vouchers(self.root, 'native:100124')
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir() if p.is_file()})

    def test_unknown_company_and_corrupt_pages_are_rejected_not_guessed(self):
        with self.assertRaises(ValueError):
            read_native_vouchers(self.root, 'native:../outside')
        path = self.folder / 'TranMgr.1800'
        raw = bytearray(path.read_bytes())
        raw[512 + 100] ^= 0xFF          # corrupt a page: checksum validation must refuse the whole read
        path.write_bytes(bytes(raw))
        with self.assertRaises(ValueError):
            read_native_vouchers(self.root, 'native:100124')


if __name__ == '__main__':
    unittest.main()
