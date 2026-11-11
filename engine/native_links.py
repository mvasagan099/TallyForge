"""Read-only LinkMgr string-reference evidence, not financial allocations.

LinkMgr uses a 16-byte common page header. Unlike Manager/TranMgr pages, its
continuations do not repeat a type/marker at bytes 16..27. Never join independent
file sequence spaces; match only the explicitly selected GUID string field.
"""
import hashlib
import os
import struct

from engine.native_records import checksum, physical_spans, STRING, MAX_BYTES, MAX_CHAIN_PAGES
from engine.native_tally import detect, folders

PAGE_SIZE = 512
GUID_FIELD = 0x235B
NOTE = ('Experimental exact string-reference matches in LinkMgr.1800. File sequence '
        'spaces are independent. These matches do not establish active status, '
        'ledger ownership, monetary values or complete allocations.')


def _fields(data, spans):
    result, rejected, cursor = [], 0, 16
    while True:
        match = STRING.search(data, cursor)
        if match is None:
            break
        start = match.end()
        cursor = start
        size = int.from_bytes(match[2], 'little')
        end = start + size
        if size < 2 or size % 2 or end > len(data):
            rejected += 1
            continue
        raw = data[start:end]
        try:
            if raw[-2:] != b'\0\0':
                raise ValueError('Unterminated string')
            value = raw[:-2].decode('utf-16-le', errors='strict')
            if any(ord(c) < 32 and c not in '\t\r\n\x04\x05' for c in value):
                raise ValueError('Invalid string control')
        except (UnicodeError, ValueError):
            rejected += 1
            continue
        cursor = end
        if value:
            locations = physical_spans(start, end, spans)
            result.append({'field_id':int.from_bytes(match[1], 'little'), 'value':value,
                           'byte_offset':locations[0]['byte_offset'], 'byte_spans':locations,
                           'logical_offset':start, 'byte_length':size})
    return result, rejected


def _chain(stream, root, number, page_count):
    """Reconstruct a LinkMgr root using only common header facts and CRC."""
    declared = struct.unpack_from('<I', root, 8)[0]
    if not 1 <= declared <= MAX_CHAIN_PAGES:
        raise ValueError('Unsupported LinkMgr declared page count')
    position = stream.tell()
    data = bytearray(root)
    spans = [(0, number*PAGE_SIZE, PAGE_SIZE)]
    pages, seen = [number], {number}
    next_page = struct.unpack_from('<I', root, 12)[0]
    try:
        while next_page:
            if next_page in seen or not 0 < next_page < page_count or len(seen) >= MAX_CHAIN_PAGES:
                raise ValueError('Invalid LinkMgr continuation chain: cycle, range or length')
            stream.seek(next_page*PAGE_SIZE)
            page = stream.read(PAGE_SIZE)
            if (len(page) != PAGE_SIZE or struct.unpack_from('<I',page)[0] != checksum(page,next_page)
                    or page[4:8] != root[4:8] or page[8:12] != b'\0'*4):
                raise ValueError('LinkMgr continuation failed sequence/checksum validation')
            spans.append((len(data), next_page*PAGE_SIZE+16, PAGE_SIZE-16))
            data.extend(page[16:])
            seen.add(next_page)
            pages.append(next_page)
            next_page = struct.unpack_from('<I',page,12)[0]
        if len(pages) != declared:
            raise ValueError('LinkMgr declared page count does not match its chain')
        return data, spans, pages
    finally:
        stream.seek(position)


def read_link_matches(root, key, voucher_guid, limit=50, offset=0):
    """Find exact 0x235B GUID fields in stable, fully CRC-checked LinkMgr data."""
    if (not isinstance(voucher_guid,str) or not 1 <= len(voucher_guid) <= 128
            or any(ord(c) < 32 for c in voucher_guid)):
        raise ValueError('Voucher GUID must contain 1 to 128 printable characters')
    for value, name, low, high in ((limit,'Limit',1,100),(offset,'Offset',0,1000000)):
        if isinstance(value,bool) or not isinstance(value,int) or not low <= value <= high:
            raise ValueError(f'{name} must be an integer from {low} to {high}')
    folder = next((folder for identity,folder in folders(root) if identity == key),None)
    if folder is None:
        raise ValueError('Native company not found in the selected data folder')
    version, files = detect(folder)
    if version != '1800':
        raise ValueError('Link reference reading supports .1800 files only')
    path = files.get('linkmgr.1800')
    if path is None:
        raise ValueError('LinkMgr.1800 was not found')
    fd = os.open(str(path), os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
    rows, total, roots, rejected, bad_pages, verified_matches = [], 0, 0, 0, 0, 0
    digest = hashlib.sha256()
    with os.fdopen(fd,'rb') as stream:
        before = os.fstat(stream.fileno())
        if before.st_size < 1024 or before.st_size > MAX_BYTES or before.st_size % PAGE_SIZE:
            raise ValueError('LinkMgr must be 512-byte aligned, between 1 KB and 512 MB')
        page_count = before.st_size//PAGE_SIZE
        for number in range(page_count):
            page = stream.read(PAGE_SIZE)
            if len(page) != PAGE_SIZE:
                raise ValueError('LinkMgr changed during reading')
            digest.update(page)
            if struct.unpack_from('<I',page)[0] != checksum(page,number):
                bad_pages += 1
                continue
            if number == 0 or struct.unpack_from('<I',page,8)[0] == 0:
                continue
            roots += 1
            data, spans, pages = _chain(stream,page,number,page_count)
            fields, skipped = _fields(data,spans)
            rejected += skipped
            matches = [field for field in fields if field['field_id'] == GUID_FIELD and field['value'] == voucher_guid]
            if not matches:
                continue
            from engine.native_tlv import decode_fields
            layout = decode_fields(data,spans,start=38)
            layout_verified = layout['complete'] and any(node['kind']=='text' and
                node['field_id']==GUID_FIELD and node['value']==voucher_guid for node in layout['nodes'])
            if layout['complete'] and not layout_verified:
                # A marker inside an opaque structure is not a serialized GUID field.
                continue
            verified_matches += layout_verified
            if offset <= total < offset + limit:
                rows.append({'sequence':struct.unpack_from('<I',page,4)[0], 'page_number':number,
                             'declared_page_count':len(pages), 'source_pages':pages,
                             'byte_offset':number*PAGE_SIZE, 'checksum_valid':True,
                             'matching_guid_fields':matches, 'fields':fields,
                             'typed_layout':layout,'guid_layout_verified':layout_verified,
                             'match_basis':'Sequential field boundaries' if layout_verified else 'Literal marker candidate; layout unverified'})
            total += 1
        after = os.fstat(stream.fileno())
        current = path.stat()
        from engine.native_tally import file_identity as identity
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise ValueError('LinkMgr changed during reading; use a stable company copy')
    if bad_pages:
        raise ValueError(f'{bad_pages} LinkMgr pages failed checksum validation; matches were not returned')
    return {'storage_key':key, 'source':'LinkMgr.1800', 'voucher_guid':voucher_guid,
            'guid_field_id':GUID_FIELD, 'rows':rows, 'total_candidates':total,
            'offset':offset, 'limit':limit, 'has_more':total > offset+len(rows),
            'sequentially_verified_matches':verified_matches,
            'pages_checked':page_count, 'roots_checked':roots,
            'rejected_string_candidates':rejected, 'source_sha256':digest.hexdigest(),
            'experimental':True, 'standalone_books_readable':False,
            'source_files_written':False, 'note':NOTE}
