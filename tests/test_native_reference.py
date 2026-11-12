"""Synthetic reference exports and native pages; no production company data."""
from pathlib import Path
import datetime
import tempfile
import unittest

from engine.native_reference import parse_reference,verify_references
from engine.native_sales import read_sales_preview
from test_native_records import make_page,seal,records_fixture
from test_native_voucher import field,container,allocation

COMPANY_GUID='11111111-2222-3333-4444-555555555555'


def text(fid,value):return field(fid,0x0f00,value.encode('utf-16-le')+b'\0\0',0x1002)
def integer(fid,word,value):return field(fid,word,int(value).to_bytes(4,'little',signed=True))


def reference_fixture(folder):
    records_fixture(folder)
    pages=[make_page(0)]
    for number,seq,name in ((1,10,'Synthetic ledger'),(2,38,'Sales')):
        page=bytearray(make_page(number,seq=seq));page[28:32]=(20).to_bytes(4,'little')
        body=text(507,COMPANY_GUID)+integer(504,0x600,22)+integer(3,0x300,0)+container(3,text(2,name))
        page[70:70+len(body)]=body;pages.append(seal(page,number))
    (folder/'Manager.1800').write_bytes(b''.join(pages))
    header=bytearray(make_page(1,seq=999));header[28:48]=b''.join(x.to_bytes(4,'little') for x in (24,1,3,2,3))
    date='20260401';serial=(datetime.date(2026,4,1)-datetime.date(1899,12,31)).days
    key=(serial<<32)|176
    primary=bytearray(make_page(2,seq=999,object_type=66));primary[24:28]=b'\x0b\xff\xff\0'
    body=text(3,format(serial,'08x')+'-00000007-000000b0')+text(3004,COMPANY_GUID)
    body+=integer(3003,0x600,41)+integer(3004,0x600,42)+integer(103,0x0d00,serial)+integer(203,0x300,38)
    primary[28:28+len(body)]=body
    secondary=bytearray(make_page(3,seq=999,object_type=66));secondary[24:28]=b'\x0b\xff\xff\0'
    body=container(10003,container(2,allocation(10,-10000000)+allocation(10,10000000)))
    secondary[28:28+len(body)]=body
    (folder/'TranMgr.1800').write_bytes(make_page(0)+seal(header,1)+seal(primary,2)+seal(secondary,3))
    prefix='<ENVELOPE><SVCURRENTCOMPANY>Fixture company</SVCURRENTCOMPANY><TALLYMESSAGE>'
    suffix='</TALLYMESSAGE></ENVELOPE>'
    master=f'<LEDGER NAME="Synthetic ledger"><GUID>{COMPANY_GUID}-0000000a</GUID><ALTERID>22</ALTERID><PARENT></PARENT></LEDGER>'
    voucher=f'<VOUCHER><GUID>{COMPANY_GUID}-00000029</GUID><MASTERID>41</MASTERID><ALTERID>42</ALTERID><DATE>{date}</DATE><VOUCHERKEY>{key}</VOUCHERKEY><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME><ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL><LEDGERENTRIES.LIST><LEDGERNAME>Synthetic ledger</LEDGERNAME><AMOUNT>-100.00</AMOUNT></LEDGERENTRIES.LIST><ALLINVENTORYENTRIES.LIST><ACCOUNTINGALLOCATIONS.LIST><LEDGERNAME>Synthetic ledger</LEDGERNAME><AMOUNT>100.00</AMOUNT></ACCOUNTINGALLOCATIONS.LIST></ALLINVENTORYENTRIES.LIST></VOUCHER>'
    (folder/'Master.xml').write_bytes((prefix+master+suffix).encode('utf-16'))
    (folder/'Transactions.xml').write_bytes((prefix+voucher+suffix).encode('utf-16'))


class NativeReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.folder=self.root/'100124'
        reference_fixture(self.folder)
    def tearDown(self):self.temp.cleanup()

    def test_reference_identity_uses_voucher_key_not_native_sequence(self):
        result=verify_references(self.root,'native:100124')
        self.assertTrue(result['reference_allocations_verified'])
        self.assertEqual(result['totals']['voucher_identity_and_date_verified'],1)
        self.assertEqual(result['totals']['ledger_entries_verified'],2)
        self.assertEqual(result['totals']['ledger_names_verified'],1)
        self.assertEqual(result['totals']['ledger_parents_verified'],1)
        self.assertFalse(result['complete_books_verified'])
        self.assertFalse(result['amounts_verified'])

    def test_native_sales_preview_reads_no_xml_and_retains_exact_amounts(self):
        for path in self.folder.glob('*.xml'):path.unlink()
        result=read_sales_preview(self.root,'native:100124',999)
        self.assertEqual(result['voucher']['master_id'],'41')
        self.assertEqual(result['voucher']['date'],'2026-04-01')
        self.assertEqual([e['amount_decimal'] for e in result['voucher']['allocations']['entries']],['-100.00000','100.00000'])
        self.assertFalse(result['financial_status_verified'])

    def test_company_books_are_native_verified_deduplicated_and_have_no_fake_balances(self):
        # Identical overlapping exports must not duplicate the Sales register.
        (self.folder/'DayBook.xml').write_bytes((self.folder/'Transactions.xml').read_bytes())
        before={p.name:p.read_bytes() for p in self.folder.iterdir() if p.is_file()}
        result=verify_references(self.root,'native:100124',include_books=True)
        self.assertEqual(len(result['books']['vouchers']),1)
        voucher=result['books']['vouchers'][0]
        self.assertEqual(voucher['native_sequence'],999)
        self.assertEqual(voucher['master_id'],'41')
        self.assertEqual(voucher['date'],'2026-04-01')
        self.assertEqual(voucher['allocations'][0]['amount_decimal'],'-100.00000')
        self.assertIsNone(voucher['posting_status'])
        self.assertIsNone(result['books']['masters'][0]['opening_balance'])
        self.assertIsNone(result['books']['masters'][0]['closing_balance'])
        self.assertTrue(result['books']['read_only'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.folder.iterdir() if p.is_file()})
        p=self.folder/'Transactions.xml'
        p.write_text(p.read_text(encoding='utf-16').replace('100.00</AMOUNT>','101.00</AMOUNT>'),encoding='utf-16')
        with self.assertRaisesRegex(ValueError,'matching references'):
            verify_references(self.root,'native:100124',include_books=True)

    def test_group_layout_offset_and_ledger_parent_use_native_prefix(self):
        path=self.folder/'Manager.1800';pages=bytearray(path.read_bytes())
        # A group has a shorter directory prefix than the ledger fixture.
        page=bytearray(make_page(3,seq=12));page[28:32]=(16).to_bytes(4,'little')
        body=text(507,COMPANY_GUID)+integer(504,0x600,23)+container(3,text(2,'Synthetic group')+text(2,'Alias'))
        page[66:66+len(body)]=body;pages.extend(seal(page,3))
        ledger=bytearray(pages[512:1024])
        body=text(507,COMPANY_GUID)+integer(504,0x600,22)+integer(3,0x300,12)+container(3,text(2,'Synthetic ledger'))
        ledger[70:]=b'\0'*(512-70);ledger[70:70+len(body)]=body
        pages[512:1024]=seal(ledger,1);path.write_bytes(pages)
        xml=self.folder/'Master.xml';original=xml.read_text(encoding='utf-16')
        original=original.replace('<PARENT></PARENT>','<PARENT>Synthetic group</PARENT>')
        group=f'<GROUP NAME="Synthetic group"><GUID>{COMPANY_GUID}-0000000c</GUID><ALTERID>23</ALTERID></GROUP>'
        xml.write_text(original.replace('</TALLYMESSAGE>',group+'</TALLYMESSAGE>'),encoding='utf-16')
        result=verify_references(self.root,'native:100124')
        self.assertEqual(result['totals']['group_names_verified'],1)
        self.assertEqual(result['totals']['ledger_parents_verified'],1)
        self.assertEqual(result['totals']['reference_identity_verified'],2)
        self.assertEqual(result['totals'].get('unresolved_references',0),0)
        # An alias must not satisfy the exported primary group name.
        xml.write_text(xml.read_text(encoding='utf-16').replace('NAME="Synthetic group"','NAME="Alias"'),encoding='utf-16')
        result=verify_references(self.root,'native:100124')
        self.assertEqual(result['totals']['unresolved_references'],1)
        self.assertEqual(result['files'][0]['unresolved_examples'][0]['reason'],'Native name differs')

    def test_xml_amount_mismatch_is_not_a_success_or_a_native_import(self):
        p=self.folder/'Transactions.xml';p.write_text(p.read_text(encoding='utf-16').replace('100.00</AMOUNT>','101.00</AMOUNT>'),encoding='utf-16')
        result=verify_references(self.root,'native:100124')
        self.assertFalse(result['reference_allocations_verified'])
        self.assertEqual(result['totals']['voucher_identity_and_date_verified'],1)
        self.assertEqual(result['totals'].get('ledger_entries_verified',0),0)
        self.assertFalse(result['source_files_written'])

    def test_foreign_company_and_arbitrary_folder_are_rejected(self):
        p=self.folder/'Master.xml';p.write_text(p.read_text(encoding='utf-16').replace('Fixture company','Other company'),encoding='utf-16')
        with self.assertRaisesRegex(ValueError,'company'):verify_references(self.root,'native:100124')
        with self.assertRaises(ValueError):verify_references(self.root,'native:../outside')
        with self.assertRaises(ValueError):read_sales_preview(self.root,'native:100124',True)

    def test_preserves_invalid_tally_controls_without_decoding_cdata_literals(self):
        data=b'<ENVELOPE><SVCURRENTCOMPANY>X</SVCURRENTCOMPANY><TALLYMESSAGE><LEDGER NAME="A&#4;B"><GUID>g</GUID><PARENT><![CDATA[&#4;]]></PARENT></LEDGER></TALLYMESSAGE></ENVELOPE>'
        result=parse_reference(data)
        self.assertEqual(result['objects'][0]['name'],'A\x04B')
        self.assertEqual(result['objects'][0]['parent'],'&#4;')
        self.assertEqual(result['normalizations']['numeric_control_references'],1)
        raw=data.replace(b'A&#4;B',b'A\x05B')
        self.assertEqual(parse_reference(raw)['objects'][0]['name'],'A\x05B')

    def test_placeholder_collision_does_not_change_valid_unicode(self):
        raw=('<ENVELOPE><TALLYMESSAGE><LEDGER NAME="'+chr(0xf0004)+'&#4;"/></TALLYMESSAGE></ENVELOPE>').encode()
        self.assertEqual(parse_reference(raw)['objects'][0]['name'],chr(0xf0004)+'\x04')

    def test_dtd_entities_malformed_and_oversized_controls_are_rejected(self):
        for raw in (b'<!DOCTYPE a [<!ENTITY x "payload">]><a>&x;</a>',b'<a>',b'<a>&#99999999999999999;</a>'):
            with self.assertRaises(ValueError):parse_reference(raw)


if __name__=='__main__':unittest.main()
