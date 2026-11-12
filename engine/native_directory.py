"""Validate the observed TranMgr directory graph without asserting active books.

File-header uint32 fields: root page@16, first sibling@20, last sibling@24,
node count@28, sequence upper bound@12. Type-11 node layout word L@28 is
24/34/40; previous/left/right/next uint32 page links begin at L+34.
These are physical page references, never cross-file sequence references.
"""
from collections import Counter
import hashlib
import os
import struct

from engine.native_records import checksum, MAX_BYTES
from engine.native_tally import detect, folders

PAGE_SIZE = 512
MAX_DIRECTORY_NODES = 250000
LAYOUTS = (24,34,40)
NOTE = ('The observed TranMgr physical directory is structurally coherent. '
        'Directory membership does not establish active, posted, cancelled or '
        'deleted accounting status, or completeness of financial books.')


def validate_directory(header, nodes):
    """Validate a supplied bounded node map and return only structural counts.

    Nodes map physical page number to sequence/layout/previous/left/right/next.
    This pure helper assumes its caller has validated source bytes and checksums.
    """
    if len(header) != PAGE_SIZE:
        raise ValueError('Invalid native directory header size')
    expected_root, first, last, count = struct.unpack_from('<IIII',header,16)
    max_sequence = struct.unpack_from('<I',header,12)[0]
    if count != len(nodes) or len(nodes) > MAX_DIRECTORY_NODES:
        raise ValueError('Native directory count does not match observed nodes or exceeds the limit')
    if not nodes:
        if expected_root or first or last:
            raise ValueError('Empty native directory has nonzero entry pointers')
        return {'directory_verified':True,'node_count':0,'root_page':0,'first_page':0,'last_page':0,
                'tree_reachable':0,'sibling_reachable':0,'orphan_directory_nodes':0,'layouts':{}}
    if expected_root not in nodes or first not in nodes or last not in nodes:
        raise ValueError('Native directory header points outside the observed node set')
    incoming = Counter()
    sequences = set()
    layouts = Counter()
    for number,node in nodes.items():
        if not isinstance(number,int) or number <= 0 or node['layout'] not in LAYOUTS:
            raise ValueError('Unsupported native directory node layout')
        seq = node['sequence']
        if not isinstance(seq,int) or not 0 < seq <= max_sequence or seq in sequences:
            raise ValueError('Invalid or duplicate native directory sequence')
        sequences.add(seq)
        layouts[str(node['layout'])] += 1
        for name in ('previous','left','right','next'):
            target = node[name]
            if target and target not in nodes:
                raise ValueError('Native directory link points outside the observed node set')
        for name in ('left','right'):
            if node[name]:
                incoming[node[name]] += 1
        previous,next_page = node['previous'],node['next']
        if previous and nodes[previous]['next'] != number:
            raise ValueError('Native directory previous/next links are not reciprocal')
        if next_page and nodes[next_page]['previous'] != number:
            raise ValueError('Native directory next/previous links are not reciprocal')
    roots = [number for number in nodes if incoming[number] == 0]
    if roots != [expected_root] or any(value != 1 for value in incoming.values() if value):
        raise ValueError('Native directory does not have exactly one parent per non-root tree node')
    # Iterative in-order traversal avoids recursion on hostile/deep graphs.
    stack, seen, ordered = [(expected_root,False)], set(), []
    while stack:
        number,emit = stack.pop()
        if emit:
            ordered.append(number)
            continue
        if number in seen:
            raise ValueError('Native directory tree contains a cycle or shared child')
        seen.add(number)
        node = nodes[number]
        if node['right']:
            stack.append((node['right'],False))
        stack.append((number,True))
        if node['left']:
            stack.append((node['left'],False))
    if seen != set(nodes):
        raise ValueError('Native directory contains unreachable tree nodes')
    if nodes[first]['previous'] or nodes[last]['next']:
        raise ValueError('Native directory first/last endpoints are inconsistent')
    siblings, sibling_seen, number = [], set(), first
    while number:
        if number in sibling_seen:
            raise ValueError('Native directory sibling chain contains a cycle')
        sibling_seen.add(number)
        siblings.append(number)
        number = nodes[number]['next']
    if sibling_seen != set(nodes) or siblings[-1] != last:
        raise ValueError('Native directory sibling chain has unreachable nodes or a wrong endpoint')
    if siblings != ordered:
        raise ValueError('Native directory tree order differs from its sibling chain')
    if any(nodes[b]['sequence'] <= nodes[a]['sequence'] for a,b in zip(ordered,ordered[1:])):
        raise ValueError('Native directory sequences are not strictly increasing')
    return {'directory_verified':True,'node_count':len(nodes),'root_page':expected_root,
            'first_page':first,'last_page':last,'tree_reachable':len(seen),
            'sibling_reachable':len(sibling_seen),'orphan_directory_nodes':0,'layouts':dict(layouts)}


def read_directory(root, key):
    folder = next((folder for identity,folder in folders(root) if identity == key),None)
    if folder is None:
        raise ValueError('Native company not found in the selected data folder')
    version,files = detect(folder)
    if version != '1800':
        raise ValueError('Directory verification supports .1800 files only')
    path = files.get('tranmgr.1800')
    if path is None:
        raise ValueError('TranMgr.1800 was not found')
    fd = os.open(str(path),os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
    nodes, digest, bad_pages = {},hashlib.sha256(),0
    with os.fdopen(fd,'rb') as stream:
        before = os.fstat(stream.fileno())
        if before.st_size < 1024 or before.st_size > MAX_BYTES or before.st_size % PAGE_SIZE:
            raise ValueError('TranMgr must be 512-byte aligned, between 1 KB and 512 MB')
        page_count = before.st_size//PAGE_SIZE
        header = None
        for number in range(page_count):
            page = stream.read(PAGE_SIZE)
            if len(page) != PAGE_SIZE:
                raise ValueError('TranMgr changed during directory reading')
            digest.update(page)
            if number == 0:
                header = page
            if struct.unpack_from('<I',page)[0] != checksum(page,number):
                bad_pages += 1
                continue
            if number == 0 or struct.unpack_from('<H',page,16)[0] != 11 or not struct.unpack_from('<I',page,8)[0]:
                continue
            if len(nodes) >= MAX_DIRECTORY_NODES:
                raise ValueError('Native directory node limit exceeded')
            layout = struct.unpack_from('<I',page,28)[0]
            if layout not in LAYOUTS:
                raise ValueError('Unsupported native directory node layout')
            previous,left,right,next_page = struct.unpack_from('<IIII',page,layout+34)
            nodes[number] = {'sequence':struct.unpack_from('<I',page,4)[0], 'layout':layout,
                             'previous':previous,'left':left,'right':right,'next':next_page}
        after = os.fstat(stream.fileno())
        current = path.stat()
        from engine.native_tally import file_identity as identity
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise ValueError('TranMgr changed during directory reading; use a stable company copy')
    if bad_pages:
        raise ValueError(f'{bad_pages} TranMgr pages failed checksum validation; directory not verified')
    result = validate_directory(header,nodes)
    result.update(storage_key=key,source='TranMgr.1800',source_sha256=digest.hexdigest(),
                  pages_checked=page_count,source_files_written=False,experimental=True,
                  standalone_books_readable=False,financial_status_verified=False,note=NOTE)
    return result
