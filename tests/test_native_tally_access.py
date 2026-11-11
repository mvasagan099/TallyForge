"""Native detection is distinct from financial decoding; bridge requests are exports only."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.company_store import CompanyStore
from engine.native_tally import catalogue,inspect,name_hint
from engine.tally_bridge import endpoint_url,export_request,parse_response,read


def native_fixture(folder,name='Company <&> பெயர்'):
    folder.mkdir(exist_ok=True)
    encoded=name.encode('utf-16-le')+b'\0\0'
    payload=b'\0'*512+b'\x02\x10\x1b\0\0\x0f'+len(encoded).to_bytes(2,'little')+encoded
    (folder/'Company.1800').write_bytes(payload)
    (folder/'Manager.1800').write_bytes(b'\0'*512)
    (folder/'TranMgr.1800').write_bytes(b'\0'*512)


class NativeTallyAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        native_fixture(self.root/'100124')

    def tearDown(self):self.temp.cleanup()

    def test_parent_and_direct_company_path_discovery(self):
        parent=catalogue(self.root);direct=catalogue(self.root/'100124')
        self.assertEqual(parent[0]['storage_key'],'native:100124')
        self.assertEqual(direct[0]['storage_key'],'native:.')
        self.assertEqual(parent[0]['name'],'Company <&> பெயர்')
        self.assertFalse(parent[0]['standalone_books_readable'])
        direct_store=CompanyStore(self.root/'100124',self.root/'prefs2.json')
        with self.assertRaisesRegex(ValueError,'separate'):direct_store.create({'name':'Wrong location'})
        store=CompanyStore(self.root,self.root/'prefs.json')
        self.assertEqual(store.catalogue()['companies'],[])
        self.assertEqual(len(store.catalogue()['native_companies']),1)
        with self.assertRaises(ValueError):store.open('native:100124')

    def test_inspection_is_read_only_and_rejects_arbitrary_paths(self):
        paths=list((self.root/'100124').iterdir());before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        result=inspect(self.root,'native:100124')
        self.assertTrue(result['metadata_only']);self.assertFalse(result['source_files_written'])
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
        with self.assertRaises(ValueError):inspect(self.root,'native:../outside')
        self.assertNotIn('ledgers',result)

    def test_invalid_string_length_never_becomes_a_company_name(self):
        p=self.root/'bad';p.write_bytes(b'\x02\x10\x1b\0\0\x0f\xff\xff'+b'A\0'*30)
        self.assertIsNone(name_hint(p))

    def test_only_explicit_local_or_private_server_origins_are_accepted(self):
        self.assertEqual(endpoint_url('http://localhost:9000'),'http://127.0.0.1:9000/')
        self.assertEqual(endpoint_url('http://192.168.1.10:9000'),'http://192.168.1.10:9000/')
        for value in ('file:///etc/passwd','http://169.254.169.254/','http://8.8.8.8/','http://example.org:9000','http://localhost:9000/admin','http://user:pw@localhost:9000','http://127.0.0.1:9000/?write=1'):
            with self.subTest(value=value),self.assertRaises(ValueError):endpoint_url(value)

    def test_bridge_builds_export_only_xml_and_escapes_company_name(self):
        raw=export_request('vouchers','Company <&>','2026-04-01','2026-04-30');root=ET.fromstring(raw)
        self.assertEqual(root.findtext('HEADER/TALLYREQUEST'),'Export')
        self.assertEqual(root.findtext('BODY/DESC/STATICVARIABLES/SVCURRENTCOMPANY'),'Company <&>')
        self.assertEqual(root.findtext('BODY/DESC/STATICVARIABLES/SVFROMDATE'),'20260401')
        self.assertNotIn(b'Import Data',raw)
        self.assertEqual(root.findtext('HEADER/ID'),'DayBook')
        self.assertEqual(root.findtext('HEADER/TYPE'),'Data')
        for args in [('ledgers',''),('unsupported','Company')]:
            with self.assertRaises(ValueError):export_request(*args)
        with self.assertRaises(ValueError):export_request('vouchers','Company','2026-04-30','2026-04-01')

    def test_utf16_company_list_and_raw_ledger_balances(self):
        companies='<?xml version="1.0" encoding="UTF-16"?><ENVELOPE><BODY><DATA><COLLECTION><COMPANY NAME="Sample"><NAME>Sample</NAME><GUID>g1</GUID></COMPANY></COLLECTION></DATA></BODY></ENVELOPE>'
        result=parse_response(companies.encode('utf-16'),'companies')
        self.assertEqual(result['rows'][0]['name'],'Sample')
        ledgers=b'<ENVELOPE><BODY><DATA><COLLECTION><LEDGER NAME="Cash"><PARENT>Cash-in-Hand</PARENT><OPENINGBALANCE>-100.00</OPENINGBALANCE><CLOSINGBALANCE>-250.50</CLOSINGBALANCE></LEDGER></COLLECTION></DATA></BODY></ENVELOPE>'
        result=parse_response(ledgers,'ledgers')
        self.assertEqual(result['rows'][0]['closing_balance_raw'],'-250.50')
        self.assertTrue(result['read_only']);self.assertFalse(result['local_books_modified'])

    def test_voucher_entries_and_response_pagination(self):
        raw=b'<ENVELOPE><BODY><DATA><COLLECTION>'+b''.join(b'<VOUCHER><DATE>20260401</DATE><VOUCHERNUMBER>'+str(n).encode()+b'</VOUCHERNUMBER><ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><AMOUNT>-50.00</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER>' for n in range(3))+b'</COLLECTION></DATA></BODY></ENVELOPE>'
        result=parse_response(raw,'vouchers',limit=1,offset=1)
        self.assertEqual(result['rows'][0]['number'],'1');self.assertTrue(result['has_more'])
        self.assertEqual(result['rows'][0]['entries'][0]['amount_raw'],'-50.00')
        self.assertEqual(result['rows'][0]['amount_raw'],'')
        daybook=raw.replace(b'<COLLECTION>',b'<TALLYMESSAGE>').replace(b'</COLLECTION>',b'</TALLYMESSAGE>')
        self.assertEqual(parse_response(daybook,'vouchers',limit=1)['rows'][0]['number'],'0')

    def test_xml_errors_and_entities_never_produce_fake_empty_books(self):
        for raw in (b'<html>Not Tally</html>',b'<ENVELOPE><STATUS>0</STATUS><LINEERROR>Denied</LINEERROR></ENVELOPE>',b'<!DOCTYPE a [<!ENTITY e "x">]><ENVELOPE>&e;</ENVELOPE>',b'<ENVELOPE><BODY/></ENVELOPE>'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_response(raw,'ledgers')

    def test_mock_transport_is_read_only_and_does_not_claim_native_decoder(self):
        observed=[]
        def transport(endpoint,body):
            observed.append((endpoint,body));return b'<ENVELOPE><BODY><DATA><COLLECTION><LEDGER NAME="Actual ledger"/></COLLECTION></DATA></BODY></ENVELOPE>'
        result=read({'endpoint':'http://127.0.0.1:9000','kind':'ledgers','company':'Company'},transport)
        self.assertEqual(result['rows'][0]['name'],'Actual ledger')
        self.assertFalse(result['native_file_decoder']);self.assertEqual(len(observed),1)


if __name__=='__main__':unittest.main()
