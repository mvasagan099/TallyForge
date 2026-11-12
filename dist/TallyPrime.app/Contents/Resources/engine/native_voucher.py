"""Reference-calibrated allocation evidence from a complete sequential layout.

The exact path and divisor match the supplied 79 Sales XML references. This is
not universal Tally compatibility, posting-status verification or a book writer.
"""
from copy import deepcopy
from decimal import Decimal, localcontext
from collections.abc import Mapping

ALLOCATION_PARENT_PATH = (10003,2,2)
DIVISOR = 100000
CALIBRATION = ('Calibrated against 79 supplied Sales vouchers and 188 ledger '
               'allocations. Other voucher types, currencies and revisions remain unverified.')


def _decimal_amount(integer):
    with localcontext() as context:
        context.prec = max(50,len(str(abs(integer)))+10)
        return format(Decimal(integer)/Decimal(DIVISOR),'.5f')


def _integer(node, unsigned, width):
    key = 'unsigned_integer_raw' if unsigned else 'signed_integer_raw'
    raw = node.get(key)
    if not isinstance(raw,str):
        raise ValueError('Allocation integer evidence is missing')
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError('Invalid allocation integer evidence') from exc
    low,high = (0,2**(width*8)-1) if unsigned else (-2**(width*8-1),2**(width*8-1)-1)
    if str(value)!=raw or not low <= value <= high or node.get('byte_length')!=width:
        raise ValueError('Allocation integer evidence has an incompatible width or value')
    try:
        payload = bytes.fromhex(node['payload_hex'])
    except (KeyError,TypeError,ValueError) as exc:
        raise ValueError('Allocation payload bytes are missing or invalid') from exc
    if len(payload)!=width or int.from_bytes(payload,'little',signed=not unsigned)!=value:
        raise ValueError('Allocation integer evidence contradicts its payload bytes')
    return value


def _spans(value, width=None):
    if not isinstance(value,list) or not value or len(value)>1024:
        raise ValueError('Allocation source byte spans are missing or unsupported')
    for span in value:
        if (not isinstance(span,dict) or isinstance(span.get('byte_offset'),bool)
                or not isinstance(span.get('byte_offset'),int) or span['byte_offset']<0
                or isinstance(span.get('bytes'),bool) or not isinstance(span.get('bytes'),int) or span['bytes']<1):
            raise ValueError('Allocation source byte spans are invalid')
    if width is not None and sum(span['bytes'] for span in value)!=width:
        raise ValueError('Allocation byte spans contradict their payload width')
    return deepcopy(value)


def decode_allocation_evidence(layout, master_names):
    """Decode exact immediate ledger/amount pairs, never a first-number fallback.

    Full path to the amount is 10003/2/2/2. A same-parent field 2/type0300
    supplies the Manager sequence; field 2/type0900 supplies the signed int64.
    Bill/inventory descendants with repeated IDs are deliberately excluded.
    """
    if not isinstance(layout,dict) or layout.get('complete') is not True:
        raise ValueError('A complete sequential layout is required for allocation evidence')
    nodes = layout.get('nodes')
    if not isinstance(nodes,list) or not 1 <= len(nodes) <= 16384 or not isinstance(master_names,Mapping):
        raise ValueError('Invalid allocation layout or master-name mapping')
    paths,children = [],{}
    for index,node in enumerate(nodes):
        if not isinstance(node,dict) or node.get('node_id')!=index:
            raise ValueError('Allocation layout node identities are inconsistent')
        parent = node.get('parent_id')
        if parent is not None and (isinstance(parent,bool) or not isinstance(parent,int) or not 0 <= parent < index):
            raise ValueError('Allocation layout has an invalid parent reference')
        if parent is None:
            path = (node.get('field_id'),)
        else:
            if nodes[parent].get('kind')!='container' or len(paths[parent])>=64:
                raise ValueError('Allocation layout ancestry is unsupported')
            path = paths[parent]+(node.get('field_id'),)
        paths.append(path)
        children.setdefault(parent,[]).append(node)
    entries = []
    balance = 0
    for parent,path in enumerate(paths):
        if path != ALLOCATION_PARENT_PATH:
            continue
        if nodes[parent].get('kind')!='container':
            raise ValueError('Allocation entry parent is not a container')
        direct = children.get(parent,[])
        refs = [node for node in direct if node.get('field_id')==2 and node.get('encoding_word')==0x0300 and node.get('kind')=='integer']
        amounts = [node for node in direct if node.get('field_id')==2 and node.get('encoding_word')==0x0900 and node.get('kind')=='integer']
        if len(refs)!=1 or len(amounts)!=1:
            raise ValueError('Ambiguous or unsupported allocation entry: expected one ledger reference and one amount')
        reference,amount = refs[0],amounts[0]
        sequence = _integer(reference,True,4)
        if sequence == 0:
            raise ValueError('Allocation ledger sequence must be nonzero')
        raw_amount = _integer(amount,False,8)
        name = master_names.get(sequence)
        if not isinstance(name,str) or not name.strip():
            raise ValueError(f'Allocation ledger sequence {sequence} has no verified name mapping')
        flags = amount.get('flags')
        if isinstance(flags,bool) or not isinstance(flags,int):
            raise ValueError('Allocation field flags are missing')
        entries.append({'ledger_sequence':sequence,'ledger_name':name,
                        'signed_integer_raw':str(raw_amount),'amount_decimal':_decimal_amount(raw_amount),
                        'flags':flags,'node_id':amount['node_id'],'parent_id':parent,
                        'ledger_reference_node_id':reference['node_id'],
                        'ledger_reference_byte_spans':_spans(reference.get('payload_byte_spans'),4),
                        'amount_header_byte_spans':_spans(amount.get('header_byte_spans')),
                        'amount_payload_byte_spans':_spans(amount.get('payload_byte_spans'),8),
                        'calibrated_flag_pattern':flags==(0x80 if raw_amount<0 else 0)})
        balance += raw_amount
    if not entries:
        raise ValueError('No supported allocation entries were decoded; an empty balance cannot be inferred')
    return {'entries':entries,'entry_count':len(entries),'balanced':balance==0,
            'balance_raw':str(balance),'balance_decimal':_decimal_amount(balance),
            'scale_divisor':DIVISOR,'calibration':CALIBRATION,
            'structure_decoded':True,'financial_status_verified':False,
            'standalone_books_readable':False,'source_files_written':False,
            'note':'Signed int64 values retain their existing sign; flags are not applied as a second sign. Balance equality alone is not financial certification.'}
