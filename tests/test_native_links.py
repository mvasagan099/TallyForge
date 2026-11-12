"""Synthetic LinkMgr fixtures; no real company records are embedded."""
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.native_links import read_link_matches


def _crc(data, seed):
    crc = seed
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xffff
    return crc


def _seal(page, number):
    struct.pack_into('<I',page,0,(_crc(page[4::2],number & 65535)<<16)|_crc(page[5::2],number & 65535))
    return bytes(page)


def _page(number, seq=1, count=1, next_page=0):
    page = bytearray(512)
    struct.pack_into('<III',page,4,seq,count,next_page)
    return page


def _string(fid, value):
    raw=value.encode('utf-16-le')+b'\0\0'
    return b'\x02\x10'+struct.pack('<H',fid)+b'\0\x0f'+struct.pack('<H',len(raw))+raw


def link_fixture(folder):
    folder.mkdir(exist_ok=True)
    (folder/'Company.1800').write_bytes(bytes(512))
    (folder/'Manager.1800').write_bytes(bytes(512))
    guid='ExactGUID1234567'
    field=_string(0x235b,guid)
    root=_page(1,seq=600,count=2,next_page=4)
    root[16:24]=b'UNKNOWN!'
    root[496:512]=field[:16]
    other=_page(2,seq=601)
    same=_string(0x235b,guid);other[16:16+len(same)]=same
    unrelated=_page(3,seq=602)
    value=_string(3,guid);unrelated[16:16+len(value)]=value
    continuation=_page(4,seq=600,count=0)
    continuation[16:16+len(field)-16]=field[16:]
    pages=[_page(0,count=0),root,other,unrelated,continuation]
    (folder/'LinkMgr.1800').write_bytes(b''.join(_seal(page,n) for n,page in enumerate(pages)))
    return guid


class NativeLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.folder=self.root/'100124'
        self.guid=link_fixture(self.folder)

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_guid_field_nonadjacent_continuation_and_provenance(self):
        result=read_link_matches(self.root,'native:100124',self.guid)
        self.assertEqual(result['total_candidates'],2)
        self.assertEqual(result['rows'][0]['source_pages'],[1,4])
        self.assertEqual(result['rows'][0]['sequence'],600)
        field=result['rows'][0]['matching_guid_fields'][0]
        self.assertEqual(field['value'],self.guid)
        self.assertEqual(field['byte_spans'],[{'byte_offset':1016,'bytes':8},{'byte_offset':2064,'bytes':26}])
        self.assertEqual(result['rejected_string_candidates'],0)
        self.assertFalse(result['standalone_books_readable'])
        self.assertNotIn('amount',result['rows'][0])

    def test_sequential_guid_boundary_is_verified_when_layout_is_supported(self):
        root=_page(1,seq=700)
        payload=_string(0x235b,self.guid)
        root[38:38+len(payload)]=payload
        (self.folder/'LinkMgr.1800').write_bytes(_seal(_page(0,count=0),0)+_seal(root,1))
        result=read_link_matches(self.root,'native:100124',self.guid)
        self.assertEqual(result['total_candidates'],1)
        self.assertEqual(result['sequentially_verified_matches'],1)
        self.assertTrue(result['rows'][0]['guid_layout_verified'])

    def test_guid_marker_inside_opaque_payload_is_not_a_supported_field_match(self):
        root=_page(1,seq=700)
        payload=bytearray(32);hidden=_string(0x235b,'X');payload[:len(hidden)]=hidden
        serialized=struct.pack('<HHH',0,502,0x44)+payload
        root[38:38+len(serialized)]=serialized
        (self.folder/'LinkMgr.1800').write_bytes(_seal(_page(0,count=0),0)+_seal(root,1))
        result=read_link_matches(self.root,'native:100124','X')
        self.assertEqual(result['total_candidates'],0)

    def test_no_match_does_not_use_same_sequence_or_other_field(self):
        self.assertEqual(read_link_matches(self.root,'native:100124','600')['total_candidates'],0)
        self.assertEqual(read_link_matches(self.root,'native:100124',self.guid.lower())['total_candidates'],0)
        result=read_link_matches(self.root,'native:100124',self.guid)
        self.assertNotIn(602,[row['sequence'] for row in result['rows']])

    def test_pagination_direct_root_and_source_hashes(self):
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()}
        first=read_link_matches(self.folder,'native:.',self.guid,limit=1)
        second=read_link_matches(self.folder,'native:.',self.guid,limit=1,offset=1)
        self.assertTrue(first['has_more']);self.assertFalse(second['has_more'])
        self.assertEqual(second['rows'][0]['sequence'],601)
        self.assertEqual(first['source_sha256'],before['LinkMgr.1800'])
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()})

    def test_corrupt_unrelated_page_prevents_partial_success(self):
        path=self.folder/'LinkMgr.1800';raw=bytearray(path.read_bytes());raw[2*512+100]^=1;path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'checksum'):
            read_link_matches(self.root,'native:100124',self.guid)

    def test_continuation_identity_and_declared_count_fail_closed(self):
        path=self.folder/'LinkMgr.1800';raw=bytearray(path.read_bytes());page=bytearray(raw[4*512:5*512]);struct.pack_into('<I',page,4,999)
        raw[4*512:5*512]=_seal(page,4);path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'sequence'):
            read_link_matches(self.root,'native:100124',self.guid)
        link_fixture(self.folder);raw=bytearray(path.read_bytes());page=bytearray(raw[512:1024]);struct.pack_into('<I',page,8,3)
        raw[512:1024]=_seal(page,1);path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'page count'):
            read_link_matches(self.root,'native:100124',self.guid)

    def test_cycles_and_out_of_range_pointers_are_rejected(self):
        path=self.folder/'LinkMgr.1800'
        for target in (1,5000):
            link_fixture(self.folder);raw=bytearray(path.read_bytes());page=bytearray(raw[512:1024]);struct.pack_into('<I',page,12,target)
            raw[512:1024]=_seal(page,1);path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'cycle, range'):
                read_link_matches(self.root,'native:100124',self.guid)

    def test_source_changes_are_rejected(self):
        import engine.native_links as native_links
        original=native_links._fields
        def changing(*args):
            with (self.folder/'LinkMgr.1800').open('ab') as f:f.write(b'\0'*512)
            return original(*args)
        with patch.object(native_links,'_fields',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'changed'):
                read_link_matches(self.root,'native:100124',self.guid)

    def test_validation_traversal_symlink_and_truncation(self):
        for kwargs in ({'limit':0},{'limit':101},{'offset':-1},{'limit':True}):
            with self.assertRaises(ValueError):read_link_matches(self.root,'native:100124',self.guid,**kwargs)
        for guid in ('','x'*129,'a\0b'):
            with self.assertRaises(ValueError):read_link_matches(self.root,'native:100124',guid)
        with self.assertRaises(ValueError):read_link_matches(self.root,'native:../100124',self.guid)
        path=self.folder/'LinkMgr.1800';raw=path.read_bytes();path.write_bytes(raw[:-1])
        with self.assertRaisesRegex(ValueError,'aligned'):read_link_matches(self.root,'native:100124',self.guid)
        path.unlink();path.symlink_to(self.folder/'Manager.1800')
        with self.assertRaises(ValueError):read_link_matches(self.root,'native:100124',self.guid)


if __name__=='__main__':
    unittest.main()
