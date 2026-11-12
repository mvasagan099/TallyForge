"""Observed field serialization, with synthetic values and no company data."""
import struct
import unittest
from engine.native_tlv import decode_fields


def field(fid,word,payload=b'',flags=0,refs=()):
    header=struct.pack('<HHH',flags,fid,word)
    for ref in refs:header+=struct.pack('<I',ref)
    if flags&0x1000:header+=len(payload).to_bytes(4 if flags&0x2000 else 2,'little')
    return header+payload


def text(fid,value):
    return field(fid,0x0f00,value.encode('utf-16-le')+b'\0\0',0x1002)


def decode(data,**kwargs):
    return decode_fields(data,[(0,1000,len(data))],start=0,**kwargs)


class NativeTLVTests(unittest.TestCase):
    def test_nested_length_prefixed_fields_and_reference_metadata(self):
        data=field(3,0x1000,text(2,'பெயர் <&>')+field(4,0x0600,struct.pack('<I',0)),0x5400,[1033])
        result=decode(data)
        self.assertTrue(result['complete'])
        self.assertEqual([n['parent_id'] for n in result['nodes']],[None,0,0])
        self.assertEqual(result['nodes'][1]['value'],'பெயர் <&>')
        self.assertEqual(result['nodes'][2]['signed_integer_raw'],'0')
        self.assertEqual(result['nodes'][0]['references'][0]['unsigned_integer_raw'],'1033')

    def test_exact_signed_and_unsigned_integer_transport_and_split_spans(self):
        data=field(2,0x0900,struct.pack('<q',-1234567890123456789))
        result=decode_fields(data,[(0,1000,8),(8,2000,6)],start=0)
        self.assertTrue(result['complete'])
        node=result['nodes'][0]
        self.assertEqual(node['signed_integer_raw'],'-1234567890123456789')
        self.assertEqual(node['unsigned_integer_raw'],str(2**64-1234567890123456789))
        self.assertEqual(node['payload_byte_spans'],[{'byte_offset':1006,'bytes':2},{'byte_offset':2000,'bytes':6}])
        self.assertNotIn('amount',node)

    def test_zero_width_and_compound_fields_remain_opaque(self):
        data=field(8,0x0100)+field(9,0x0700,b'12345678')+field(10,0x1600,b'abcdefgh')
        result=decode(data)
        self.assertTrue(result['complete'])
        self.assertTrue(all(n['kind']=='opaque' for n in result['nodes']))
        self.assertTrue(all('value' not in n and 'signed_integer_raw' not in n for n in result['nodes']))

    def test_no_resynchronization_after_unsupported_encoding(self):
        data=field(2,0x0023,b'unknown')+text(2,'Should not be decoded')
        result=decode(data)
        self.assertFalse(result['complete']);self.assertEqual(result['nodes'],[])
        self.assertEqual(result['stop_offset'],0)
        self.assertIn('0x0023',result['diagnostic'])

    def test_children_cannot_escape_declared_container_bounds(self):
        child=text(2,'Long string')
        data=field(3,0x1000,child[:-4],0x5000)+child[-4:]
        result=decode(data)
        self.assertFalse(result['complete'])
        self.assertEqual(len(result['nodes']),1)
        self.assertIn('bounded',result['diagnostic'])

    def test_truncated_reference_header_and_length_are_explicit(self):
        for data in (b'\0\0\x01',struct.pack('<HHH',0x5400,2,0x1000)+b'\0',
                     struct.pack('<HHH',0x1002,2,0x0f00)+b'\0'):
            result=decode(data)
            self.assertFalse(result['complete'])
            self.assertEqual(result['nodes'],[])

    def test_unicode_errors_and_unsupported_controls_are_rejected(self):
        for payload in (b'a',b'\0\xd8\0\0',b'a\0',b'\x01\0\0\0'):
            result=decode(field(2,0x0f00,payload,0x1002))
            self.assertFalse(result['complete'])
            self.assertEqual(result['nodes'],[])

    def test_node_and_depth_limits_do_not_silently_succeed(self):
        result=decode(text(2,'First')+text(3,'Second'),max_nodes=1)
        self.assertFalse(result['complete']);self.assertEqual(len(result['nodes']),1)
        data=text(2,'Nested')
        for _ in range(3):data=field(3,0x1000,data,0x5000)
        self.assertFalse(decode(data,max_depth=1)['complete'])

    def test_padding_32bit_lengths_and_unknown_flags(self):
        result=decode(field(3,0x1000,text(2,'Name')+b'\0'*8,0x7000)+b'\0'*20)
        self.assertTrue(result['complete'])
        self.assertEqual(result['node_count'],2)
        self.assertFalse(decode(field(3,0x0600,b'\0'*4,0x0001))['complete'])
        self.assertFalse(decode(field(3,0x0600,b'\0'*4,0x2000))['complete'])
        with self.assertRaises(ValueError):decode(b'',max_nodes=True)

    def test_internal_structure_words_remain_opaque_and_preserve_references(self):
        from engine.native_tlv import OPAQUE_WORDS
        for word,size in OPAQUE_WORDS.items():
            payload=bytes(range(size))
            result=decode(field(502,word,payload,0x0404,[165])+text(2,'Next field'))
            self.assertTrue(result['complete'],word)
            self.assertEqual(result['nodes'][0]['kind'],'opaque')
            self.assertEqual(result['nodes'][0]['payload_hex'],payload.hex())
            self.assertEqual(result['nodes'][0]['references'][0]['unsigned_integer_raw'],'165')
            self.assertEqual(result['nodes'][1]['value'],'Next field')
            self.assertNotIn('amount',result['nodes'][0])

    def test_sign_like_flags_are_retained_without_applying_a_currency_sign(self):
        result=decode(field(502,0x0900,struct.pack('<q',1234),0x0480,[165]))
        self.assertTrue(result['complete'])
        self.assertEqual(result['nodes'][0]['flags'],0x0480)
        self.assertEqual(result['nodes'][0]['signed_integer_raw'],'1234')

    def test_observed_compound_width_variant_preserves_following_field_boundaries(self):
        for flags,size in ((0,12),(0x4000,24),(0x4080,24)):
            payload=bytes(range(size))
            result=decode(field(2,0x0b00,payload,flags)+text(121,'Following field'))
            self.assertTrue(result['complete'])
            self.assertEqual(result['nodes'][0]['kind'],'opaque')
            self.assertEqual(result['nodes'][0]['byte_length'],size)
            self.assertEqual(result['nodes'][0]['flags'],flags)
            self.assertEqual(result['nodes'][0]['payload_hex'],payload.hex())
            self.assertEqual(result['nodes'][1]['value'],'Following field')
        result=decode(field(2,0x0c00,bytes(range(20)))+text(121,'Next quantity field'))
        self.assertTrue(result['complete'])
        self.assertEqual(result['nodes'][0]['kind'],'opaque')
        self.assertEqual(result['nodes'][1]['value'],'Next quantity field')

    def test_unobserved_compound_variant_and_wrong_lengths_stop_without_resync(self):
        result=decode(field(2,0x0c00,bytes(range(52)),0x4000)+text(3,'Must not resync'))
        self.assertFalse(result['complete'])
        self.assertEqual(result['nodes'],[])
        self.assertIn('Unsupported compound',result['diagnostic'])
        for size in (12,23,25):
            result=decode(field(2,0x0b00,bytes(size),0x5000))
            self.assertFalse(result['complete'])
            self.assertIn('incompatible length',result['diagnostic'])

    def test_extended_opaque_blocks_inside_container_keep_signed_scalar_exact(self):
        body=field(502,0x0046,bytes(range(40)),0x0400,[100])+field(302,0x0066,bytes(range(40)),0x0c00,[200,300])
        body+=field(21203,0x0900,struct.pack('<q',-1234567890123),0x0480,[400])
        result=decode(field(10000,0x1000,body,0x5000))
        self.assertTrue(result['complete'])
        self.assertEqual([n['parent_id'] for n in result['nodes']],[None,0,0,0])
        amount_candidate=result['nodes'][-1]
        self.assertEqual(amount_candidate['signed_integer_raw'],'-1234567890123')
        self.assertEqual(amount_candidate['flags'],0x0480)
        self.assertNotIn('amount',amount_candidate)

    def test_repeated_field_ids_keep_allocation_and_nested_reference_paths_distinct(self):
        bill=field(502,0x1000,field(2,0x0900,struct.pack('<q',-1234500000),0x80),0x5000)
        first=field(2,0x1000,field(2,0x0300,struct.pack('<I',17))+field(2,0x0900,struct.pack('<q',-1234500000),0x80)+bill,0x5000)
        second=field(2,0x1000,field(2,0x0300,struct.pack('<I',18))+field(2,0x0900,struct.pack('<q',1234500000)),0x5000)
        result=decode(field(10003,0x1000,field(2,0x1000,first+second,0x5000),0x5000))
        self.assertTrue(result['complete'])
        paths=[]
        for node in result['nodes']:
            if node['encoding_word']!=0x0900:continue
            path=[];current=node
            while current is not None:
                path.append(current['field_id'])
                current=result['nodes'][current['parent_id']] if current['parent_id'] is not None else None
            paths.append(tuple(reversed(path)))
        self.assertEqual(paths,[(10003,2,2,2),(10003,2,2,502,2),(10003,2,2,2)])


if __name__=='__main__':unittest.main()
