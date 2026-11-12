"""Experimental, read-only .1800 page/field reader; never an accounting adapter.

Physical pages can contain old versions or indexes. Checksums check page
bytes, not record semantics, completeness, current status, or financial totals.
See docs/native-reader.md and THIRD_PARTY_NOTICES.md for research provenance.
"""
import binascii
import hashlib
import os
import re
import struct

from engine.native_tally import detect, folders

PAGE_SIZE = 512
MAX_BYTES = 512 * 1024 * 1024
MAX_CHAIN_PAGES = 1024
STRING = re.compile(rb'\x02\x10(..)\x00\x0f(..)', re.DOTALL)
SCALAR = re.compile(rb'(?:\x00\x00|\x02\x10)(..)\x00([\x06\x08\x09\x0d])', re.DOTALL)
NOTE = ('Experimental native-record preview. Records may include historical or deleted data. '
        'Raw integers have no verified currency scale or date meaning. Active-record selection, '
        'unsupported page chains, opening balances and ledger allocations remain incomplete. '
        'This is not a complete ledger or voucher register.')


def checksum(page, number):
    if len(page) != PAGE_SIZE:
        raise ValueError('A native page must contain exactly 512 bytes')
    seed = number & 0xffff
    return (binascii.crc_hqx(page[4::2], seed) << 16) | binascii.crc_hqx(page[5::2], seed)


def physical_spans(start, end, spans):
    result = []
    for logical, file_offset, size in spans:
        left, right = max(start, logical), min(end, logical + size)
        if left < right:
            result.append({'byte_offset':file_offset + left - logical, 'bytes':right-left})
    return result


def text_fields(page, page_number, spans=None):
    """Return only complete strings inside this page, with source byte offsets.

    A logical chain supplies spans so split strings retain physical provenance.
    Adjacent pages alone never establish a continuation relation.
    """
    result, rejected = [], 0
    cursor = 24  # Never interpret the checksum/identity header as a field.
    while True:
        match = STRING.search(page, cursor)
        if match is None:
            break
        start = match.end()
        length = int.from_bytes(match[2], 'little')
        end = start + length
        cursor = start
        if length < 2 or length % 2 or end > len(page):
            rejected += 1
            continue
        raw = page[start:end]
        try:
            if raw[-2:] != b'\0\0':
                raise ValueError('Unterminated string')
            value = raw[:-2].decode('utf-16-le', errors='strict')
            # ENQ/EOT occur in search keys; preserve them as evidence.
            if any(ord(c) < 32 and c not in '\t\r\n\x04\x05' for c in value):
                raise ValueError('Invalid string control')
        except (UnicodeError, ValueError):
            rejected += 1
            continue
        cursor = end
        if value:
            field = {'field_id': int.from_bytes(match[1], 'little'), 'value': value,
                     'byte_offset': page_number * PAGE_SIZE + start,
                     'logical_offset':start, 'byte_length':length}
            if spans is not None:
                physical = physical_spans(start, end, spans)
                field.update(byte_offset=physical[0]['byte_offset'], byte_spans=physical)
            result.append(field)
    return result, rejected


def chain_data(stream, page, number, page_count):
    """Follow only observed same-object continuation pointers, with a hard bound.

    Offset 12 links pages, not necessarily adjacent. Continuation data starts at
    28. Validate CRC, sequence, object type, zero continuation count and marker.
    An unsupported/cyclic chain is rejected rather than joined speculatively.
    """
    position = stream.tell()
    data = bytearray(page)
    spans = [(0, number * PAGE_SIZE, PAGE_SIZE)]
    seen = {number}
    declared = struct.unpack_from('<I', page, 8)[0]
    if not 1 <= declared <= MAX_CHAIN_PAGES:
        raise ValueError('Unsupported native declared page count')
    next_page = struct.unpack_from('<I', page, 12)[0]
    try:
        while next_page:
            if next_page in seen or next_page >= page_count or len(seen) >= MAX_CHAIN_PAGES:
                raise ValueError('Unsupported native continuation chain (cycle, range or length)')
            stream.seek(next_page * PAGE_SIZE)
            continuation = stream.read(PAGE_SIZE)
            if (len(continuation) != PAGE_SIZE or
                    struct.unpack_from('<I', continuation)[0] != checksum(continuation, next_page) or
                    continuation[4:8] != page[4:8] or continuation[16:18] != page[16:18] or
                    continuation[8:12] != b'\0\0\0\0' or continuation[24:28] != page[24:28]):
                raise ValueError('Unsupported or damaged native continuation page')
            spans.append((len(data), next_page * PAGE_SIZE + 28, PAGE_SIZE - 28))
            data.extend(continuation[28:])
            seen.add(next_page)
            next_page = struct.unpack_from('<I', continuation, 12)[0]
        if len(seen) != declared:
            raise ValueError('Native declared page count does not match its continuation chain')
        return data, spans, [start // PAGE_SIZE for _, start, _ in spans]
    finally:
        stream.seek(position)


def chain_fields(stream, page, number, page_count):
    data, spans, pages = chain_data(stream, page, number, page_count)
    fields, rejected = text_fields(data, number, spans)
    return fields, rejected, pages


def scalar_candidates(data, spans, fields):
    """Raw integer evidence only: type/scale/date/amount semantics are unverified.

    Do not scan inside valid string fields, and do not convert integers to floats
    (64-bit integers exceed JavaScript's exact range). Keep zero values too.
    """
    excluded = [(f['logical_offset']-8, f['logical_offset']+f['byte_length']) for f in fields]
    result, cursor = [], 28
    while True:
        match = SCALAR.search(data, cursor)
        if match is None:
            break
        start = match.end()
        cursor = start
        field_id, code = int.from_bytes(match[1], 'little'), match[2][0]
        size = 8 if code in (8,9) else 4
        end = start + size
        if field_id == 0 or end > len(data) or any(match.start() < right and end > left for left,right in excluded):
            continue
        raw = data[start:end]
        locations = physical_spans(start, end, spans)
        result.append({'field_id':field_id, 'type_code':code, 'payload_hex':raw.hex(),
                       'signed_integer_raw':str(int.from_bytes(raw,'little',signed=True)),
                       'unsigned_integer_raw':str(int.from_bytes(raw,'little')),
                       'byte_offset':locations[0]['byte_offset'], 'byte_spans':locations,
                       'interpretation':'Unscaled integer candidate; financial/date meaning unverified'})
        cursor = end
    return result


def linked_metadata(stream, header, page_count):
    """Decode only explicit same-sequence metadata references in type-11 roots."""
    if struct.unpack_from('<II',header,32) != (1,3):
        raise ValueError('Unsupported transaction header layout')
    result = []
    position = stream.tell()
    try:
        for field_offset in (40,44):
            target = struct.unpack_from('<I',header,field_offset)[0]
            if not 0 < target < page_count:
                raise ValueError('Transaction metadata reference is out of range')
            stream.seek(target*PAGE_SIZE)
            page = stream.read(PAGE_SIZE)
            if (len(page)!=PAGE_SIZE or page[4:8]!=header[4:8] or
                    struct.unpack_from('<H',page,16)[0]!=66 or
                    struct.unpack_from('<I',page)[0]!=checksum(page,target)):
                raise ValueError('Transaction metadata reference failed identity/checksum validation')
            data, spans, pages = chain_data(stream,page,target,page_count)
            fields, rejected = text_fields(data,target,spans)
            result.append({'reference_offset':field_offset,'page_number':target,'source_pages':pages,
                           'fields':fields,
                           'rejected_string_candidates':rejected})
        return result
    finally:
        stream.seek(position)


def _integer(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{label} must be an integer from {low} to {high}')
    return value


def read(root, key, kind='masters', offset=0, limit=50, search='', sequence=None, source=None):
    if kind not in ('masters', 'vouchers', 'transactions', 'detail'):
        raise ValueError('Choose masters, vouchers, transactions or detail')
    _integer(offset, 'Offset', 0, 1000000)
    _integer(limit, 'Limit', 1, 100)
    if not isinstance(search, str) or len(search) > 120:
        raise ValueError('Search must contain at most 120 characters')
    if kind == 'detail':
        _integer(sequence, 'Sequence', 1, 0xffffffff)
        if source not in ('Manager.1800', 'TranMgr.1800'):
            raise ValueError('Unsupported native source')
    else:
        source = 'Manager.1800' if kind == 'masters' else 'TranMgr.1800'
    folder = next((folder for identity, folder in folders(root) if identity == key), None)
    if folder is None:
        raise ValueError('Native company not found in the selected data folder')
    version, files = detect(folder)
    if version != '1800':
        raise ValueError('The experimental record reader supports .1800 files only')
    path = files.get(source.casefold())
    if path is None:
        raise ValueError(f'{source} was not found')
    # Open read-only and reject a symlink swapped in after discovery.
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    rows, total, bad_pages, rejected, selected_pages = [], 0, 0, 0, 0
    digest = hashlib.sha256()
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if before.st_size < 1024 or before.st_size > MAX_BYTES or before.st_size % PAGE_SIZE:
            raise ValueError('Native source must be 512-byte aligned, between 1 KB and 512 MB')
        for number in range(before.st_size // PAGE_SIZE):
            page = stream.read(PAGE_SIZE)
            if len(page) != PAGE_SIZE:
                raise ValueError('Native source changed during reading')
            digest.update(page)
            if struct.unpack_from('<I', page)[0] != checksum(page, number):
                bad_pages += 1
                continue
            if number == 0:
                continue
            seq, declared_count = struct.unpack_from('<II', page, 4)
            object_type = struct.unpack_from('<H', page, 16)[0]
            selected = (declared_count > 0 and
                        (kind == 'detail' and seq == sequence or
                         kind == 'masters' and object_type == 11 or
                         kind == 'vouchers' and object_type == 3 or
                         kind == 'transactions' and object_type == 11))
            if not selected:
                continue
            selected_pages += 1
            data, spans, source_pages = chain_data(stream, page, number, before.st_size // PAGE_SIZE)
            fields, skipped = text_fields(data, number, spans)
            rejected += skipped
            row = {'sequence': seq, 'page_number': number, 'declared_page_count': declared_count,
                   'object_type': object_type, 'byte_offset': number * PAGE_SIZE,
                   'checksum_valid': True, 'source_pages': source_pages, 'fields': fields}
            if kind == 'detail':
                row['scalar_candidates'] = scalar_candidates(data,spans,fields)
                if object_type == 66 and data[24:28] == b'\x0b\xff\xff\x00':
                    from engine.native_tlv import decode_fields
                    row['typed_layout'] = decode_fields(data,spans)
            if kind == 'transactions':
                metadata = linked_metadata(stream,page,before.st_size // PAGE_SIZE)
                row['metadata'] = metadata
                fields = [f for block in metadata for f in block['fields']]
                row['fields'] = fields
                rejected += sum(block['rejected_string_candidates'] for block in metadata)
            if kind == 'masters':
                names = [f['value'] for f in fields if f['field_id'] == 2]
                if not names:
                    continue
                row['names'] = names
            elif kind == 'vouchers':
                keys = []
                for field in fields:
                    parts = field['value'].split('\x05')
                    if (field['field_id'] == 2 and len(parts) == 4 and
                            len(parts[0]) == 4 and parts[0].isascii() and parts[0].isdigit()
                            and parts[1].isascii() and parts[1].isdigit() and parts[2] and parts[3]):
                        keys.append({'year_token': parts[0], 'type_sequence': parts[1],
                                     'type_name': parts[2], 'number': parts[3]})
                if not keys:
                    continue
                row['search_keys'] = keys
            if search.casefold() not in ' '.join(f['value'] for f in fields).casefold():
                continue
            if offset <= total < offset + limit:
                rows.append(row)
            total += 1
        after = os.fstat(stream.fileno())
        current = path.stat()
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise ValueError('Native source changed during reading; use a stable company copy')
    if bad_pages:
        # A partial success could disguise a damaged or unsupported source.
        raise ValueError(f'{bad_pages} pages failed checksum validation; records were not returned')
    return {'storage_key': key, 'kind': kind, 'source': source, 'rows': rows,
            'total_candidates': total, 'offset': offset, 'limit': limit,
            'has_more': total > offset + len(rows), 'source_sha256': digest.hexdigest(),
            'pages_checked': before.st_size // PAGE_SIZE, 'selected_pages': selected_pages,
            'rejected_string_candidates': rejected, 'source_files_written': False,
            'experimental': True, 'standalone_books_readable': False, 'note': NOTE}
