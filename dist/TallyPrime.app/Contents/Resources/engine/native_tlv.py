"""Bounded sequential decoding of observed serialized field layouts.

No resynchronization: an unknown encoding ends this decode with an explicit
diagnostic. Correct field boundaries do not establish monetary/date semantics.
"""
import struct

from engine.native_records import physical_spans

SIZES = {1:0, 2:4, 3:4, 5:4, 6:4, 7:8, 8:8, 9:8, 13:4, 22:8}
# Observed internal structures. Their contents remain opaque; these widths
# establish boundaries only, not ledger, amount or tree-pointer semantics.
OPAQUE_WORDS = {0x0021:16, 0x0022:20,
                0x0041:16, 0x0042:20, 0x0044:32, 0x0046:40,
                0x0061:16, 0x0062:20, 0x0064:32, 0x0066:40}
# Observed compound payload lengths, keyed by the presence of flag 0x4000.
# The extended 0x0c00 form has not been established and remains unsupported.
COMPOUND_WORDS = {0x0b00:{False:12,True:24},0x0c00:{False:20}}
ALLOWED_FLAGS = 0x7c8e


def decode_fields(data, spans, start=28, max_nodes=8192, max_depth=32):
    """Return a flat field tree, exact bytes and an explicit coverage boundary.

    The 6-byte header contains flags, field ID and encoding word. Flag 0x0400
    and 0x0800 each add an opaque 4-byte reference. Flag 0x1000 adds a 16-bit
    payload length (32-bit with 0x2000). Type 0x1000 contains bounded fields.
    Type 0x0100 has no payload here; it is not interpreted as a Boolean value.
    """
    if not isinstance(data,(bytes,bytearray)) or not 0 <= start <= len(data):
        raise ValueError('Invalid serialized field buffer or start')
    if (isinstance(max_nodes,bool) or not isinstance(max_nodes,int) or not 1 <= max_nodes <= 16384 or
            isinstance(max_depth,bool) or not isinstance(max_depth,int) or not 1 <= max_depth <= 64):
        raise ValueError('Invalid serialized field decoding limits')
    nodes = []
    diagnostic = None
    stop = len(data)

    def failure(at,reason):
        nonlocal diagnostic,stop
        diagnostic = reason
        stop = at
        return False

    def parse(left,right,depth,parent):
        if depth > max_depth:
            return failure(left,'Maximum nested field depth reached')
        cursor = left
        while cursor < right:
            # Padding is accepted only if the entire remaining bounded region is zero.
            if not any(data[cursor:right]):
                return True
            at = cursor
            if len(nodes) >= max_nodes:
                return failure(at,'Maximum decoded field count reached')
            if cursor+6 > right:
                return failure(at,'Truncated serialized field header')
            flags,fid,word = struct.unpack_from('<HHH',data,cursor)
            cursor += 6
            if flags & ~ALLOWED_FLAGS:
                return failure(at,f'Unsupported field flags 0x{flags:04x}')
            if flags & 0x2000 and not flags & 0x1000:
                return failure(at,'Length-width flag appears without a length prefix')
            references = []
            for bit in (0x400,0x800):
                if flags & bit:
                    if cursor+4 > right:
                        return failure(at,'Truncated field reference')
                    references.append({'flag_bit':bit,'payload_hex':data[cursor:cursor+4].hex(),
                                       'unsigned_integer_raw':str(int.from_bytes(data[cursor:cursor+4],'little')),
                                       'byte_spans':physical_spans(cursor,cursor+4,spans)})
                    cursor += 4
            if flags & 0x1000:
                width = 4 if flags & 0x2000 else 2
                if cursor+width > right:
                    return failure(at,'Truncated field length')
                size = int.from_bytes(data[cursor:cursor+width],'little')
                cursor += width
            elif word in OPAQUE_WORDS:
                size = OPAQUE_WORDS[word]
            elif word in COMPOUND_WORDS:
                size = COMPOUND_WORDS[word].get(bool(flags & 0x4000))
                if size is None:
                    return failure(at,'Unsupported compound field variant')
            elif word & 255 == 0 and word >> 8 in SIZES:
                size = SIZES[word >> 8]
            else:
                return failure(at,f'Unsupported field encoding 0x{word:04x}')
            end = cursor + size
            if end > right:
                return failure(at,'Field payload exceeds its bounded region')
            # A length does not make an unknown encoding understood.
            if word not in (0x0f00,0x1000) and word not in OPAQUE_WORDS and word not in COMPOUND_WORDS and (word & 255 or word >> 8 not in SIZES):
                return failure(at,f'Unsupported field encoding 0x{word:04x}')
            if word in (0x0f00,0x1000) and not flags & 0x1000:
                return failure(at,'Variable-length field has no length prefix')
            if word in OPAQUE_WORDS and size != OPAQUE_WORDS[word]:
                return failure(at,'Internal structure has an incompatible length')
            if word in COMPOUND_WORDS and size != COMPOUND_WORDS[word].get(bool(flags & 0x4000)):
                return failure(at,'Compound structure has an incompatible length')
            if word not in (0x0f00,0x1000) and word not in OPAQUE_WORDS and word not in COMPOUND_WORDS and size != SIZES[word >> 8]:
                return failure(at,'Fixed-width field has an incompatible length')
            payload = data[cursor:end]
            node = {'node_id':len(nodes),'parent_id':parent,'depth':depth,'field_id':fid,
                    'flags':flags,'encoding_word':word,'logical_offset':at,
                    'header_byte_spans':physical_spans(at,cursor,spans),
                    'payload_byte_spans':physical_spans(cursor,end,spans),
                    'byte_length':size,'references':references,
                    'semantic_status':'Unverified: no date, currency or account meaning assigned'}
            if word == 0x0f00:
                if size < 2 or size % 2 or payload[-2:] != b'\0\0':
                    return failure(at,'Invalid UTF-16 string length or terminator')
                try:
                    value = payload[:-2].decode('utf-16-le',errors='strict')
                except UnicodeError:
                    return failure(at,'Invalid UTF-16 string encoding')
                if any(ord(c)<32 and c not in '\t\r\n\x04\x05' for c in value):
                    return failure(at,'Unsupported string control character')
                node.update(kind='text',value=value)
            elif word == 0x1000:
                node['kind']='container'
            else:
                node.update(kind='opaque' if word in OPAQUE_WORDS or word in COMPOUND_WORDS or word in (0x0100,0x0500,0x0700,0x1600) else 'integer',payload_hex=payload.hex())
                if node['kind']=='integer':
                    node.update(signed_integer_raw=str(int.from_bytes(payload,'little',signed=True)),
                                unsigned_integer_raw=str(int.from_bytes(payload,'little')))
            nodes.append(node)
            if word == 0x1000 and not parse(cursor,end,depth+1,node['node_id']):
                return False
            cursor = end
        return True

    complete = parse(start,len(data),0,None)
    return {'complete':complete,'nodes':nodes,'node_count':len(nodes),'start_offset':start,
            'stop_offset':stop,'unparsed_bytes':len(data)-stop,'diagnostic':diagnostic,
            'note':'Sequential layout decoding only. A complete parse does not validate field meanings, active status or financial totals.'}
