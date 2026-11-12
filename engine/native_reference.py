"""Read-only comparisons against operator-supplied Tally XML reference exports.

Reference exports validate a subset, never silently activate native books or
import XML records into the local accounting engine.
"""
import datetime
import hashlib
import io
import os
import re
import struct
from collections import Counter,defaultdict
from decimal import Decimal
from xml.etree import ElementTree as ET

from engine.native_records import checksum,chain_data,text_fields,MAX_BYTES
from engine.native_tally import detect,folders,name_hint
from engine.native_tlv import decode_fields

MAX_XML_BYTES=64*1024*1024
REFERENCE_FILES=('Master.xml','Transactions.xml','DayBook.xml')
GUID=re.compile(r'^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})-([0-9a-fA-F]{8})$')
PACKED=re.compile(r'^([0-9a-fA-F]{8})-([0-9a-fA-F]{8})-([0-9a-fA-F]{8})$')


def _stable(path,maximum):
    fd=os.open(str(path),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not 0<before.st_size<=maximum:raise ValueError('Reference file is empty or exceeds its size limit')
        raw=stream.read(maximum+1)
        after=os.fstat(stream.fileno())
    from engine.native_tally import file_identity as identity
    if len(raw)!=before.st_size or identity(before)!=identity(after) or identity(after)!=identity(path.stat()):
        raise ValueError('Reference file changed while being read')
    return raw


def parse_reference(raw):
    if not isinstance(raw,bytes) or len(raw)>MAX_XML_BYTES:raise ValueError('Invalid or oversized reference XML')
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):text=raw.decode('utf-16')
    elif raw[:4] in (b'<\0E\0',b'<\0?\0'):text=raw.decode('utf-16-le')
    else:text=raw.decode('utf-8-sig')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)',text,re.I):raise ValueError('XML DTD/entity declarations are not accepted')
    # Replace forbidden XML-1.0 controls with collision-free private characters,
    # then restore exact values in parsed strings. Do not decode references inside CDATA/comments.
    bad=[v for v in range(32) if v not in (9,10,13)]
    base=next((b for b in range(0xf0000,0xf1000,32) if all(chr(b+v) not in text for v in bad)),None)
    if base is None:raise ValueError('Cannot reserve control-character placeholders')
    counts=Counter()
    def numeric(match):
        token=match[1]
        if len(token)>10:raise ValueError('Invalid numeric character reference')
        value=int(token[1:],16) if token.lower().startswith('x') else int(token)
        if value in bad:counts['numeric_control_references']+=1;return chr(base+value)
        return match[0]
    parts=re.split(r'(<!\[CDATA\[.*?\]\]>|<!--.*?-->)',text,flags=re.S)
    for i,part in enumerate(parts):
        if not part.startswith(('<![CDATA[','<!--')):
            part=re.sub(r'&#(x[0-9a-fA-F]+|[0-9]+);',numeric,part)
        def literal(match):counts['literal_controls']+=1;return chr(base+ord(match[0]))
        parts[i]=re.sub('[\x00-\x08\x0b\x0c\x0e-\x1f]',literal,part)
    restore={base+v:v for v in bad}
    fix=lambda value:(value or '').translate(restore)
    company=set();objects=[];stack=[];elements=0
    try:
        for event,node in ET.iterparse(io.StringIO(''.join(parts)),events=('start','end')):
            if event=='start':
                stack.append(node.tag);elements+=1
                if elements>2000000:raise ValueError('XML element limit exceeded')
                continue
            if node.tag=='SVCURRENTCOMPANY' and fix(node.text).strip():company.add(fix(node.text).strip())
            if node.tag in ('LEDGER','GROUP','VOUCHER') and len(stack)>1 and stack[-2]=='TALLYMESSAGE':
                get=lambda tag:fix(node.findtext(tag)).strip()
                obj={'kind':node.tag,'name':fix(node.attrib.get('NAME')),'guid':get('GUID'),
                     'master_id':get('MASTERID'),'alter_id':get('ALTERID'),'parent':get('PARENT'),
                     'opening_balance_raw':get('OPENINGBALANCE')}
                if node.tag=='VOUCHER':
                    obj.update(voucher_key=get('VOUCHERKEY'),date=get('DATE'),number=get('VOUCHERNUMBER'),
                               voucher_type=get('VOUCHERTYPENAME'),cancelled=get('ISCANCELLED'),optional=get('ISOPTIONAL'))
                    entries=[]
                    for entry in node.iter():
                        if entry.tag not in ('LEDGERENTRIES.LIST','ALLLEDGERENTRIES.LIST','ACCOUNTINGALLOCATIONS.LIST'):continue
                        name=fix(entry.findtext('LEDGERNAME'));amount=fix(entry.findtext('AMOUNT')).strip()
                        if name:entries.append({'ledger':name,'amount_raw':amount,'source_tag':entry.tag})
                    obj['entries']=entries
                objects.append(obj);node.clear()
            if node.tag=='TALLYMESSAGE':node.clear()
            stack.pop()
    except ET.ParseError as exc:raise ValueError('Malformed reference XML: '+str(exc)) from exc
    return {'companies':sorted(company),'objects':objects,'normalizations':dict(counts),
            'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}


def _scan(path,callback):
    fd=os.open(str(path),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    digest=hashlib.sha256();bad=0
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if before.st_size<1024 or before.st_size>MAX_BYTES or before.st_size%512:raise ValueError('Unsupported native source size')
        pages=before.st_size//512
        for number in range(pages):
            page=stream.read(512)
            if len(page)!=512:raise ValueError('Native source changed during reference comparison')
            digest.update(page)
            if int.from_bytes(page[:4],'little')!=checksum(page,number):bad+=1;continue
            if number and int.from_bytes(page[8:12],'little') and int.from_bytes(page[16:18],'little')==11:
                callback(stream,page,number,pages)
        after=os.fstat(stream.fileno())
    from engine.native_tally import file_identity as identity
    if identity(before)!=identity(after) or identity(after)!=identity(path.stat()):raise ValueError('Native source changed during comparison')
    if bad:raise ValueError('Native source failed checksum validation')
    return digest.hexdigest()


def _values(nodes,fid,word,top=False):
    return [n.get('value',n.get('signed_integer_raw')) for n in nodes if n['field_id']==fid and
            n['encoding_word']==word and (not top or n['parent_id'] is None)]


def canonical_name(layout):
    """First language-name child, not the mailing-name or unrelated field 2."""
    nodes=layout['nodes']
    containers={n['node_id'] for n in nodes if n['field_id']==3 and n['kind']=='container' and n['parent_id'] is None}
    names=[n['value'] for n in nodes if n['kind']=='text' and n['field_id']==2 and n['parent_id'] in containers]
    return names[0] if names else None


def _amount_pairs(entries):
    values=[]
    for entry in entries:
        raw=entry['amount_raw']
        if len(raw)>80 or not re.fullmatch(r'[+-]?\d+(?:\.\d+)?',raw):return None
        values.append((entry['ledger'],Decimal(raw)))
    return Counter(values) if values else None


def verify_references(root,key,include_books=False):
    folder=next((p for identity,p in folders(root) if identity==key),None)
    if folder is None:raise ValueError('Native company not found')
    version,files=detect(folder)
    if version!='1800':raise ValueError('Reference comparison supports .1800 files only')
    hint=name_hint(files['company.1800']);company_name=hint['value'] if hint else None
    if not company_name:raise ValueError('Native company name could not be established')
    references=[];missing=[]
    for name in REFERENCE_FILES:
        path=files.get(name.casefold())
        if path is None:missing.append(name);continue
        parsed=parse_reference(_stable(path,MAX_XML_BYTES));parsed['filename']=name
        if parsed['companies']!=[company_name]:raise ValueError(name+': exported company does not exactly match the selected native company')
        references.append(parsed)
    if not references:raise ValueError('No Master.xml, Transactions.xml or DayBook.xml found in this company folder')
    masters={};native_sources={};voucher_keys=set()
    for ref in references:
        for obj in ref['objects']:
            if obj['kind']=='VOUCHER' and obj['voucher_key'].isdigit() and 0<=int(obj['voucher_key'])<2**64:
                voucher_keys.add(int(obj['voucher_key']))
    def master(stream,page,number,pages):
        data,spans,_=chain_data(stream,page,number,pages)
        # Directory prefix length varies (groups: 16, ledgers: 20).
        # The serialized fields follow that prefix and its 50-byte envelope.
        start=int.from_bytes(page[28:32],'little')+50
        masters[int.from_bytes(page[4:8],'little')]=decode_fields(data,spans,start=start)
    native_sources['Manager.1800']=_scan(files['manager.1800'],master)
    vouchers=defaultdict(list)
    def voucher(stream,page,number,pages):
        if struct.unpack_from('<II',page,32)!=(1,3):return
        target=int.from_bytes(page[40:44],'little');position=stream.tell()
        try:
            if not 0<target<pages:raise ValueError('Invalid native metadata reference')
            stream.seek(target*512);metadata=stream.read(512)
            if metadata[4:8]!=page[4:8] or int.from_bytes(metadata[16:18],'little')!=66 or int.from_bytes(metadata[:4],'little')!=checksum(metadata,target):raise ValueError('Invalid native metadata identity/checksum')
            data,spans,_=chain_data(stream,metadata,target,pages)
            fields,_=text_fields(data,target,spans)
            keys=set()
            for f in fields:
                m=PACKED.fullmatch(f['value']) if f['field_id']==3 else None
                if m:
                    packed=(int(m[1],16)<<32)|int(m[3],16)
                    if packed in voucher_keys:keys.add(packed)
            if keys:
                layout=decode_fields(data,spans)
                other=int.from_bytes(page[44:48],'little')
                if not 0<other<pages:raise ValueError('Invalid secondary metadata reference')
                stream.seek(other*512);other_page=stream.read(512)
                if len(other_page)!=512 or other_page[4:8]!=page[4:8] or int.from_bytes(other_page[16:18],'little')!=66 or int.from_bytes(other_page[:4],'little')!=checksum(other_page,other):raise ValueError('Invalid secondary metadata identity/checksum')
                other_data,other_spans,_=chain_data(stream,other_page,other,pages)
                record={'sequence':int.from_bytes(page[4:8],'little'),'page_number':number,'layout':layout,
                        'allocation_layout':decode_fields(other_data,other_spans)}
                for packed in keys:vouchers[packed].append(record)
        finally:stream.seek(position)
    if voucher_keys:native_sources['TranMgr.1800']=_scan(files['tranmgr.1800'],voucher)
    master_names={seq:canonical_name(layout) for seq,layout in masters.items() if canonical_name(layout)}
    rows=[];totals=Counter();book_masters={};book_vouchers={}
    for ref in references:
        stats=Counter();failures=[];dates=[]
        for obj in ref['objects']:
            stats[obj['kind'].lower()+'_references']+=1
            m=GUID.fullmatch(obj['guid'])
            reason=None
            if not m:reason='Missing or unsupported reference GUID'
            elif obj['kind'] in ('LEDGER','GROUP'):
                record=masters.get(int(m[2],16))
                if record is None:reason='Native identity not found'
                elif not record['complete']:reason='Native layout is partial'
                else:
                    nodes=record['nodes']
                    if m[1] not in _values(nodes,507,0x0f00):reason='Company GUID differs'
                    elif obj['name'] != canonical_name(record):reason='Native name differs'
                    else:
                        stats['names_verified']+=1
                        stats[obj['kind'].lower()+'_names_verified']+=1
                        alts=_values(nodes,504,0x600,True)
                        if obj['alter_id'] and alts and alts[0]==obj['alter_id']:stats['alter_ids_verified']+=1
                        elif obj['alter_id']:reason='Master alteration ID differs or is unavailable'
                        if obj['kind']=='LEDGER':
                            parents=_values(nodes,3,0x300,True)
                            parent=masters.get(int(parents[0])) if len(parents)==1 else None
                            if (parents==['0'] and not obj['parent']) or (parent and parent['complete'] and obj['parent'] == canonical_name(parent)):
                                stats['ledger_parents_verified']+=1
                            else:reason='Ledger parent group differs or is unavailable'
                        if reason is None:
                            stats['reference_identity_verified']+=1
                            if include_books:
                                parents=_values(nodes,3,0x300,True)
                                parent=masters.get(int(parents[0])) if len(parents)==1 else None
                                book_masters[obj['guid']]={'kind':obj['kind'],'name':canonical_name(record),
                                    'native_sequence':int(m[2],16),'guid':obj['guid'],
                                    'parent':canonical_name(parent) if parent and parent['complete'] else None,
                                    'opening_balance':None,'closing_balance':None}
            else:
                packed=int(obj['voucher_key']) if obj['voucher_key'].isdigit() else None
                candidates=vouchers.get(packed,[])
                if len(candidates)!=1:reason='Voucher key is missing or ambiguous'
                elif not candidates[0]['layout']['complete']:reason='Native voucher metadata layout is partial'
                else:
                    nodes=candidates[0]['layout']['nodes']
                    if m[1] not in _values(nodes,3004,0x0f00):reason='Voucher company GUID differs'
                    elif not obj['master_id'].isdigit() or int(m[2],16)!=int(obj['master_id']):reason='Reference GUID and master ID disagree'
                    elif _values(nodes,3003,0x600,True)!=[obj['master_id']]:reason='Voucher master ID differs'
                    elif _values(nodes,3004,0x600,True)!=[obj['alter_id']]:reason='Voucher alteration ID differs'
                    else:
                        try:expected=(datetime.datetime.strptime(obj['date'],'%Y%m%d').date()-datetime.date(1899,12,31)).days
                        except ValueError:expected=None
                        if expected is None or _values(nodes,103,0x0d00,True)!=[str(expected)]:reason='Voucher date differs or is unavailable'
                        else:
                            stats['voucher_identity_and_date_verified']+=1;dates.append(obj['date'])
                            from engine.native_voucher import decode_allocation_evidence
                            expected_pairs=_amount_pairs(obj['entries'])
                            try:evidence=decode_allocation_evidence(candidates[0]['allocation_layout'],master_names)
                            except ValueError as exc:evidence=None;reason=str(exc)
                            if obj['voucher_type']!='Sales' or obj['cancelled']!='No' or obj['optional']!='No':
                                reason='Voucher type or status is outside the reference calibration scope'
                            elif len(_values(nodes,203,0x300,True))!=1 or master_names.get(int(_values(nodes,203,0x300,True)[0]))!='Sales':
                                reason='Native voucher class is outside the Sales calibration scope'
                            elif expected_pairs is None:reason='Reference ledger amounts are missing or unsupported'
                            elif evidence is not None:
                                actual_pairs=Counter((entry['ledger_name'],Decimal(entry['amount_decimal'])) for entry in evidence['entries'])
                                if actual_pairs!=expected_pairs:reason='Ledger/amount allocation multiset differs'
                                elif not evidence['balanced']:reason='Native allocation entries do not balance'
                                elif not all(entry['calibrated_flag_pattern'] for entry in evidence['entries']):reason='Allocation flags are outside the reference calibration scope'
                                else:
                                    stats['voucher_allocation_multisets_verified']+=1
                                    stats['ledger_entries_verified']+=sum(expected_pairs.values())
                                    if include_books:
                                        candidate=candidates[0]
                                        book_vouchers[obj['guid']]={'guid':obj['guid'],
                                            'native_sequence':candidate['sequence'],
                                            'master_id':_values(nodes,3003,0x600,True)[0],
                                            'date':(datetime.date(1899,12,31)+datetime.timedelta(days=expected)).isoformat(),
                                            'reference_number':obj['number'],
                                            'voucher_type':'Sales','allocations':evidence['entries'],
                                            'posting_status':None}
            if reason:
                stats['unresolved_references']+=1
                if len(failures)<20:failures.append({'kind':obj['kind'],'identity':obj['guid'],'reason':reason})
        totals.update(stats)
        rows.append({'filename':ref['filename'],'sha256':ref['sha256'],'bytes':ref['bytes'],
                     'normalizations':ref['normalizations'],'counts':dict(stats),'unresolved_examples':failures,
                     'date_from':min(dates) if dates else None,'date_to':max(dates) if dates else None})
    result={'company':company_name,'storage_key':key,'files':rows,'missing_files':missing,
            'totals':dict(totals),'native_sources':native_sources,'source_files_written':False,
            'complete_books_verified':False,'amounts_verified':False,
            'reference_allocations_verified':totals['voucher_references']>0 and totals['voucher_allocation_multisets_verified']==totals['voucher_references'],
            'note':'Reference-subset comparison only. Matching identity/date and allocation values do not verify opening balances, posting-state flags or the complete native books. Missing opening balances are not treated as zero.'}
    if include_books:
        if totals['unresolved_references']:
            raise ValueError('Company books preview requires matching references; run Compare XML references to review unresolved records')
        result['books']={'masters':sorted(book_masters.values(),key=lambda r:(r['kind'],r['name'].casefold())),
            'vouchers':sorted(book_vouchers.values(),key=lambda r:(r['date'],r['master_id'])),
            'coverage':'Only native records matched to the supplied XML exports. Voucher numbers are labels from those exports. This is not the complete company register.',
            'opening_balances_available':False,'posting_status_verified':False,'read_only':True}
    return result
