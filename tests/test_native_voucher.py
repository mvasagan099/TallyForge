"""Reference-calibrated path extraction with synthetic, lossless integers."""
import copy
from decimal import localcontext
import struct
import unittest

from engine.native_tlv import decode_fields
from engine.native_voucher import decode_allocation_evidence


def field(fid,word,payload=b'',flags=0):
    header=struct.pack('<HHH',flags,fid,word)
    if flags&0x1000:header+=struct.pack('<H',len(payload))
    return header+payload


def container(fid,body):return field(fid,0x1000,body,0x5000)


def allocation(ledger,raw,extra=b''):
    return container(2,field(2,0x0300,struct.pack('<I',ledger))+
        field(2,0x0900,struct.pack('<q',raw),0x80 if raw<0 else 0)+extra)


def layout(entries,extra=b''):
    data=container(10003,container(2,b''.join(entries)+extra))
    return decode_fields(data,[(0,1000,len(data))],start=0)


class NativeVoucherEvidenceTests(unittest.TestCase):
    def setUp(self):self.names={17:'Receivable <&>',18:'Sales'}

    def test_exact_named_pairs_and_balanced_evidence(self):
        result=decode_allocation_evidence(layout([allocation(17,-1234500000),allocation(18,1234500000)]),self.names)
        self.assertEqual([(x['ledger_name'],x['amount_decimal']) for x in result['entries']],
                         [('Receivable <&>','-12345.00000'),('Sales','12345.00000')])
        self.assertTrue(result['balanced'])
        self.assertEqual(result['balance_raw'],'0')
        self.assertEqual(result['balance_decimal'],'0.00000')
        self.assertFalse(result['financial_status_verified'])
        self.assertIn('79 supplied Sales vouchers',result['calibration'])
        self.assertNotIn('active',result['entries'][0])

    def test_nested_bill_and_inventory_duplicates_are_excluded(self):
        duplicate=container(502,field(2,0x0900,struct.pack('<q',-1234500000),0x80))
        inventory=container(3,container(502,field(2,0x0300,struct.pack('<I',18))+field(2,0x0900,struct.pack('<q',1234500000))))
        data=layout([allocation(17,-1234500000,duplicate),allocation(18,1234500000)],inventory)
        result=decode_allocation_evidence(data,self.names)
        self.assertEqual(result['entry_count'],2)
        self.assertTrue(result['balanced'])

    def test_sign_is_not_applied_twice_and_unmatched_flag_is_explicit(self):
        data=layout([allocation(17,-100000),allocation(18,100000)])
        result=decode_allocation_evidence(data,self.names)
        self.assertEqual(result['entries'][0]['amount_decimal'],'-1.00000')
        self.assertEqual(result['entries'][0]['flags'],0x80)
        self.assertTrue(result['entries'][0]['calibrated_flag_pattern'])
        amount=next(n for n in data['nodes'] if n.get('signed_integer_raw')=='100000')
        amount['flags']=0x80
        result=decode_allocation_evidence(data,self.names)
        self.assertEqual(result['entries'][1]['amount_decimal'],'1.00000')
        self.assertFalse(result['entries'][1]['calibrated_flag_pattern'])

    def test_exact_int64_beyond_javascript_precision_and_low_decimal_context(self):
        raw=2**63-1
        with localcontext() as context:
            context.prec=3
            result=decode_allocation_evidence(layout([allocation(17,-raw),allocation(18,raw)]),self.names)
        self.assertEqual(result['entries'][0]['signed_integer_raw'],'-9223372036854775807')
        self.assertEqual(result['entries'][1]['amount_decimal'],'92233720368547.75807')
        self.assertTrue(result['balanced'])

    def test_unbalanced_evidence_is_not_silently_corrected(self):
        result=decode_allocation_evidence(layout([allocation(17,-100001),allocation(18,100000)]),self.names)
        self.assertFalse(result['balanced'])
        self.assertEqual(result['balance_raw'],'-1')
        self.assertEqual(result['balance_decimal'],'-0.00001')

    def test_partial_missing_ledger_or_empty_path_rejected_without_zero_defaults(self):
        data=layout([allocation(17,-100000),allocation(18,100000)])
        partial=copy.deepcopy(data);partial['complete']=False
        with self.assertRaisesRegex(ValueError,'complete'):decode_allocation_evidence(partial,self.names)
        with self.assertRaisesRegex(ValueError,'no verified name'):decode_allocation_evidence(data,{17:'Receivable'})
        with self.assertRaisesRegex(ValueError,'No supported'):decode_allocation_evidence(layout([]),self.names)

    def test_ambiguous_shape_and_invalid_ancestry_rejected(self):
        extra=field(2,0x0900,struct.pack('<q',100000))
        data=layout([allocation(17,-100000,extra)])
        with self.assertRaisesRegex(ValueError,'Ambiguous'):decode_allocation_evidence(data,self.names)
        data=layout([allocation(17,-100000)])
        data['nodes'][-1]['parent_id']=data['nodes'][-1]['node_id']
        with self.assertRaisesRegex(ValueError,'parent'):decode_allocation_evidence(data,self.names)

    def test_payload_and_provenance_are_checked_and_input_is_unchanged(self):
        data=layout([allocation(17,-100000),allocation(18,100000)])
        original=copy.deepcopy(data)
        result=decode_allocation_evidence(data,self.names)
        self.assertEqual(data,original)
        self.assertTrue(result['entries'][0]['amount_payload_byte_spans'])
        result['entries'][0]['amount_payload_byte_spans'][0]['byte_offset']=0
        self.assertEqual(data,original)
        amount=next(n for n in data['nodes'] if n.get('signed_integer_raw')=='-100000')
        amount['signed_integer_raw']='-100001'
        with self.assertRaisesRegex(ValueError,'contradicts'):decode_allocation_evidence(data,self.names)
        data=copy.deepcopy(original)
        amount=next(n for n in data['nodes'] if n.get('signed_integer_raw')=='-100000')
        amount['payload_byte_spans']=[]
        with self.assertRaisesRegex(ValueError,'byte spans'):decode_allocation_evidence(data,self.names)


if __name__=='__main__':unittest.main()
