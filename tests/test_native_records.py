"""No production company data is embedded in these fixtures."""
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.native_records import checksum, read, text_fields


def reference_crc(data, seed):
    # Independent bitwise reference, rather than the production C-library CRC.
    crc = seed
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xffff
    return crc


def seal(page, number):
    struct.pack_into('<I',page,0,(reference_crc(page[4::2],number)<<16)|reference_crc(page[5::2],number))
    return bytes(page)


def make_page(number, seq=1, page_count=1, object_type=11, fields=()):
    page = bytearray(512)
    struct.pack_into('<II', page, 4, seq, page_count)
    struct.pack_into('<H', page, 16, object_type)
    position = 64
    for fid, value in fields:
        encoded = value.encode('utf-16-le') + b'\0\0'
        field = b'\x02\x10' + struct.pack('<H', fid) + b'\0\x0f' + struct.pack('<H', len(encoded)) + encoded
        page[position:position+len(field)] = field
        position += len(field)
    assert len(page) == 512
    crc = reference_crc(page[4::2], number & 65535) << 16 | reference_crc(page[5::2], number & 65535)
    struct.pack_into('<I', page, 0, crc)
    return bytes(page)


def records_fixture(folder):
    folder.mkdir(exist_ok=True)
    header = make_page(0)
    (folder / 'Company.1800').write_bytes(header + make_page(1, fields=[(27, 'Fixture company')]))
    (folder / 'Manager.1800').write_bytes(header + make_page(1, seq=10, fields=[(2, 'Master பெயர் <&>')]) +
                                        make_page(2, seq=11, fields=[(2, 'Other master')]))
    (folder / 'TranMgr.1800').write_bytes(header + make_page(1, seq=15, object_type=3,
                                        fields=[(2, '2026\x0510\x05Custom Voucher\x05V-0001')]) +
                                        make_page(2, seq=15, object_type=66,
                                        fields=[(205, 'Exact narration')]))


def transaction_fixture(folder):
    records_fixture(folder)
    header=bytearray(make_page(1,seq=15))
    struct.pack_into('<IIIII',header,28,24,1,3,2,3)
    payload=bytearray(make_page(2,seq=15,object_type=66,fields=[(205,'Transaction narration <&>')]))
    payload[200:214]=b'\0\0\x02\0\0\x09'+struct.pack('<q',-1234567890123456789)
    (folder/'TranMgr.1800').write_bytes(make_page(0)+seal(header,1)+seal(payload,2)+
        make_page(3,seq=15,object_type=66,fields=[(206,'Counterparty text')]))


class NativeRecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        records_fixture(self.root / '100124')

    def tearDown(self):
        self.temp.cleanup()

    def test_real_fields_pagination_search_and_provenance_without_financial_defaults(self):
        result = read(self.root, 'native:100124', limit=1)
        self.assertEqual(result['total_candidates'], 2)
        self.assertTrue(result['has_more'])
        self.assertEqual(result['rows'][0]['names'], ['Master பெயர் <&>'])
        self.assertEqual(result['rows'][0]['fields'][0]['byte_offset'], 584)
        self.assertFalse(result['standalone_books_readable'])
        self.assertNotIn('balance', str(result['rows']))
        result = read(self.root, 'native:100124', search='OTHER')
        self.assertEqual(result['total_candidates'], 1)
        self.assertEqual(result['rows'][0]['sequence'], 11)
        self.assertEqual(read(self.root, 'native:100124', offset=1)['rows'][0]['sequence'], 11)

    def test_voucher_identity_is_decoded_without_inferred_type_or_amount(self):
        result = read(self.root / '100124', 'native:.', kind='vouchers')
        row = result['rows'][0]
        self.assertEqual(row['search_keys'][0], {'year_token':'2026', 'type_sequence':'10', 'type_name':'Custom Voucher', 'number':'V-0001'})
        self.assertNotIn('amount', row)
        details = read(self.root, 'native:100124', kind='detail', source='TranMgr.1800', sequence=15)
        self.assertEqual(details['total_candidates'], 2)
        self.assertEqual(details['rows'][1]['fields'][0]['value'], 'Exact narration')

    def test_all_source_bytes_remain_unchanged(self):
        paths = list((self.root/'100124').iterdir())
        before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        for kind in ('masters','vouchers'):
            read(self.root, 'native:100124', kind=kind)
        self.assertEqual(before, {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_corrupt_and_truncated_sources_never_return_partial_success(self):
        path = self.root/'100124'/'Manager.1800'
        data = bytearray(path.read_bytes());data[-1] ^= 1;path.write_bytes(data)
        with self.assertRaisesRegex(ValueError,'checksum'):
            read(self.root,'native:100124')
        path.write_bytes(data[:-1])
        with self.assertRaisesRegex(ValueError,'aligned'):
            read(self.root,'native:100124')

    def test_stable_read_rejects_changes(self):
        from engine import native_records
        original = native_records.text_fields
        def changing(page, number, spans=None):
            path = self.root/'100124'/'Manager.1800'
            with path.open('ab') as f:
                f.write(b'\0' * 512)
            return original(page, number, spans)
        with patch.object(native_records, 'text_fields', side_effect=changing):
            with self.assertRaisesRegex(ValueError,'changed'):
                read(self.root,'native:100124')

    def test_split_invalid_and_nested_string_candidates_are_not_fabricated(self):
        page = bytearray(make_page(1, fields=[(2, 'Outer text')]))
        page[504:512] = b'\x02\x10\x02\0\0\x0f\x20\0'
        fields, rejected = text_fields(page,1)
        self.assertEqual([f['value'] for f in fields],['Outer text'])
        self.assertEqual(rejected,1)
        for value in ('unpaired\ud800', 'internal\0null'):
            if '\ud800' in value:
                raw=b'\x00\xd8\0\0'
            else:
                raw=value.encode('utf-16-le')+b'\0\0'
            page=bytearray(512);page[64:72]=b'\x02\x10\x02\0\0\x0f'+struct.pack('<H',len(raw));page[72:72+len(raw)]=raw
            self.assertEqual(text_fields(page,1)[0],[])

    def test_validation_blocks_traversal_limits_and_symlinks(self):
        for kwargs in ({'offset':-1},{'limit':101},{'offset':True},{'search':'x'*121},
                       {'kind':'detail','sequence':15,'source':'../Company.1800'},
                       {'kind':'detail','sequence':0,'source':'Manager.1800'}):
            with self.assertRaises(ValueError):read(self.root,'native:100124',**kwargs)
        with self.assertRaises(ValueError):read(self.root,'native:../100124')
        (self.root/'100125').symlink_to(self.root/'100124',target_is_directory=True)
        with self.assertRaises(ValueError):read(self.root,'native:100125')

    def test_checksum_matches_independent_reference_and_page_number_seed(self):
        for number in (0,1,65535,65536):
            page=make_page(number, fields=[(2,'checksum sample')])
            self.assertEqual(checksum(page,number),struct.unpack_from('<I',page)[0])
        with self.assertRaises(ValueError):checksum(b'short',1)

    def test_nonadjacent_continuation_recovers_split_unicode_with_source_spans(self):
        from engine.native_records import chain_fields
        from io import BytesIO
        value='Names across linked pages பெயர்'
        raw=value.encode('utf-16-le')+b'\0\0'
        root=bytearray(make_page(1,seq=20,page_count=2))
        struct.pack_into('<I',root,12,3)
        root[488:496]=b'\x02\x10\x02\0\0\x0f'+struct.pack('<H',len(raw))
        root[496:512]=raw[:16]
        continuation=bytearray(make_page(3,seq=20,page_count=0))
        continuation[28:28+len(raw)-16]=raw[16:]
        for number,page in ((1,root),(3,continuation)):
            struct.pack_into('<I',page,0,(reference_crc(page[4::2],number)<<16)|reference_crc(page[5::2],number))
        stream=BytesIO(make_page(0)+root+make_page(2,seq=99)+continuation)
        stream.seek(512*2)
        fields,rejected,pages=chain_fields(stream,root,1,4)
        self.assertEqual(rejected,0)
        self.assertEqual(fields[0]['value'],value)
        self.assertEqual(fields[0]['byte_spans'],[{'byte_offset':1008,'bytes':16},{'byte_offset':1564,'bytes':len(raw)-16}])
        self.assertEqual(pages,[1,3]);self.assertEqual(stream.tell(),1024)
        # Even a valid CRC must not allow a link into another object's page.
        struct.pack_into('<I',continuation,4,21)
        struct.pack_into('<I',continuation,0,(reference_crc(continuation[4::2],3)<<16)|reference_crc(continuation[5::2],3))
        stream=BytesIO(make_page(0)+root+make_page(2,seq=99)+continuation)
        with self.assertRaisesRegex(ValueError,'continuation'):
            chain_fields(stream,root,1,4)

    def test_cyclic_continuation_is_rejected(self):
        from engine.native_records import chain_fields
        from io import BytesIO
        page=bytearray(make_page(1))
        struct.pack_into('<I',page,12,1)
        with self.assertRaisesRegex(ValueError,'cycle'):
            chain_fields(BytesIO(make_page(0)+page),page,1,2)

    def test_page_count_is_not_a_record_subtype_and_must_match_chain(self):
        from engine.native_records import chain_fields
        from io import BytesIO
        page=make_page(1,page_count=2)
        with self.assertRaisesRegex(ValueError,'page count'):
            chain_fields(BytesIO(make_page(0)+page),page,1,2)
        # A one-page type-3 root is still a voucher-key candidate.
        result=read(self.root,'native:100124',kind='vouchers')
        self.assertEqual(result['rows'][0]['declared_page_count'],1)
        self.assertNotIn('page_index',result['rows'][0])

    def test_transaction_metadata_follows_header_references_and_exact_raw_integers(self):
        transaction_fixture(self.root/'100124')
        result=read(self.root,'native:100124',kind='transactions')
        self.assertEqual(result['total_candidates'],1)
        row=result['rows'][0]
        self.assertEqual([m['page_number'] for m in row['metadata']],[2,3])
        self.assertEqual([f['value'] for f in row['fields']],['Transaction narration <&>','Counterparty text'])
        self.assertEqual(read(self.root,'native:100124',kind='transactions',search='Counterparty')['total_candidates'],1)
        details=read(self.root,'native:100124',kind='detail',source='TranMgr.1800',sequence=15)
        raw=next(c for r in details['rows'] for c in r['scalar_candidates'] if c['type_code']==9)
        self.assertEqual(raw['signed_integer_raw'],'-1234567890123456789')
        self.assertNotIn('amount',raw)

    def test_reference_identity_mismatch_is_rejected_even_with_valid_checksum(self):
        transaction_fixture(self.root/'100124')
        path=self.root/'100124'/'TranMgr.1800'
        data=bytearray(path.read_bytes());bad=bytearray(data[1024:1536])
        struct.pack_into('<I',bad,4,16)
        data[1024:1536]=seal(bad,2);path.write_bytes(data)
        with self.assertRaisesRegex(ValueError,'identity'):
            read(self.root,'native:100124',kind='transactions')

    def test_numeric_candidates_exclude_text_and_preserve_zero_and_split_payloads(self):
        from engine.native_records import scalar_candidates
        text=make_page(1,fields=[(2,'Text contains characters')])
        page=bytearray(text)
        page[200:214]=b'\0\0\x03\0\0\x09'+struct.pack('<q',0)
        page[486:500]=b'\0\0\x04\0\0\x09'+struct.pack('<q',9223372036854775807)
        fields,_=text_fields(page,1,[(0,512,512)])
        raw=scalar_candidates(page,[(0,512,512)],fields)
        self.assertEqual([c['signed_integer_raw'] for c in raw],['0','9223372036854775807'])
        # Re-map a logical payload across a physical-page boundary.
        raw=scalar_candidates(page,[(0,512,496),(496,1564,16)],fields)
        self.assertEqual(raw[-1]['byte_spans'],[{'byte_offset':1004,'bytes':4},{'byte_offset':1564,'bytes':4}])


if __name__ == '__main__':
    unittest.main()
