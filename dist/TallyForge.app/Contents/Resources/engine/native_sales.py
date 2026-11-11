"""Read-only Sales preview using the explicitly reference-calibrated schema."""
import datetime
import re
from engine.native_reference import _scan,_values,canonical_name,PACKED
from engine.native_tally import folders,detect
from engine.native_records import checksum,chain_data
from engine.native_tlv import decode_fields
from engine.native_voucher import decode_allocation_evidence


def _one(nodes,fid,word):
    values=_values(nodes,fid,word,True)
    if len(values)!=1:raise ValueError('Required Sales header field is missing or ambiguous')
    return values[0]


def read_sales_preview(root,key,sequence):
    if isinstance(sequence,bool) or not isinstance(sequence,int) or not 1<=sequence<=0xffffffff:
        raise ValueError('Native Sales sequence must be a positive integer')
    folder=next((p for identity,p in folders(root) if identity==key),None)
    if folder is None:raise ValueError('Native company not found')
    version,files=detect(folder)
    if version!='1800':raise ValueError('The Sales preview supports .1800 only')
    names={};company_guids=set()
    def master(stream,page,number,pages):
        data,spans,_=chain_data(stream,page,number,pages)
        layout=decode_fields(data,spans,start=int.from_bytes(page[28:32],'little')+50)
        name=canonical_name(layout)
        if name:names[int.from_bytes(page[4:8],'little')]=name
        company_guids.update(_values(layout['nodes'],507,0x0f00,True))
    sources={'Manager.1800':_scan(files['manager.1800'],master)}
    found=[]
    def voucher(stream,page,number,pages):
        if int.from_bytes(page[4:8],'little')!=sequence:return
        if page[32:40]!=b'\x01\0\0\0\x03\0\0\0':raise ValueError('Unsupported transaction header')
        position=stream.tell();layouts=[];refs=[]
        try:
            for at in (40,44):
                target=int.from_bytes(page[at:at+4],'little')
                if not 0<target<pages:raise ValueError('Invalid Sales metadata reference')
                stream.seek(target*512);metadata=stream.read(512)
                if len(metadata)!=512 or metadata[4:8]!=page[4:8] or int.from_bytes(metadata[16:18],'little')!=66 or int.from_bytes(metadata[:4],'little')!=checksum(metadata,target):
                    raise ValueError('Sales metadata identity/checksum differs')
                data,spans,chain=chain_data(stream,metadata,target,pages)
                layout=decode_fields(data,spans)
                if not layout['complete']:raise ValueError('Sales metadata layout is incomplete: '+str(layout['diagnostic']))
                layouts.append(layout);refs.append({'page_number':target,'source_pages':chain})
        finally:stream.seek(position)
        nodes=layouts[0]['nodes']
        kind=int(_one(nodes,203,0x300))
        if names.get(kind)!='Sales':raise ValueError('This preview is calibrated for Sales only; other voucher types remain unverified')
        master_id=int(_one(nodes,3003,0x600));alter_id=int(_one(nodes,3004,0x600))
        company_guid=_one(nodes,3004,0x0f00)
        if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',company_guid) or not 0<master_id<=0xffffffff or not 0<=alter_id<=0x7fffffff:
            raise ValueError('Unsupported Sales identity fields')
        if company_guids!={company_guid}:
            raise ValueError('Native master and transaction company GUIDs do not agree')
        packed=PACKED.fullmatch(_one(nodes,3,0x0f00))
        if packed is None:raise ValueError('Unsupported Sales voucher-key layout')
        serial=int(_one(nodes,103,0x0d00))
        try:
            if serial<=0:raise ValueError('Invalid date serial')
            date=(datetime.date(1899,12,31)+datetime.timedelta(days=serial)).isoformat()
        except (ValueError,OverflowError) as exc:raise ValueError('Sales date is outside the calibrated date representation') from exc
        allocations=decode_allocation_evidence(layouts[1],names)
        if not allocations['balanced'] or not all(e['calibrated_flag_pattern'] for e in allocations['entries']):
            raise ValueError('Sales allocation balance or flags are outside the reference calibration')
        found.append({'native_sequence':sequence,'master_id':str(master_id),'alter_id':str(alter_id),
                      'guid':company_guid+'-'+format(master_id,'08x'),'date':date,'voucher_type':'Sales',
                      'voucher_key':str((int(packed[1],16)<<32)|int(packed[3],16)),
                      'header_page':number,'metadata':refs,'allocations':allocations})
    sources['TranMgr.1800']=_scan(files['tranmgr.1800'],voucher)
    if len(found)!=1:raise ValueError('Sales transaction sequence is missing or ambiguous')
    return {'storage_key':key,'voucher':found[0],'native_sources':sources,
            'source_files_written':False,'standalone_books_readable':False,'financial_status_verified':False,
            'note':'Read-only native Sales preview. Schema calibrated against 79 supplied Sales vouchers; posting/cancellation state, opening balances, other currencies and other voucher types remain unverified. No XML is used to produce this preview.'}
