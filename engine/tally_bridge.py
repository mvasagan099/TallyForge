"""Bounded, read-only XML-over-HTTP access to a running TallyPrime server."""
import datetime
import ipaddress
import re
from urllib import error, request
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

MAX_RESPONSE = 32*1024*1024
FIELDS = {
    'companies': ('Company', ('Name','GUID','StartingFrom','BooksBeginningFrom')),
    'ledgers': ('Ledger', ('Name','GUID','Parent','OpeningBalance','ClosingBalance','MasterID','AlterID')),
    'vouchers': ('Voucher', ('GUID','MasterID','AlterID','Date','VoucherNumber','VoucherTypeName','PartyLedgerName','Amount','IsCancelled','IsOptional','AllLedgerEntries.LedgerName','AllLedgerEntries.Amount'))}


def endpoint_url(value):
    if not isinstance(value,str):raise ValueError('Enter the TallyPrime server URL')
    parts=urlsplit(value.strip())
    if parts.scheme not in ('http','https') or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise ValueError('Use an HTTP(S) Tally server origin, without credentials or a path')
    host=parts.hostname
    if host=='localhost':host='127.0.0.1'
    try:
        address=ipaddress.ip_address(host or '')
        port=parts.port or (443 if parts.scheme=='https' else 80)
    except ValueError as exc:raise ValueError('Use localhost or the numeric private IP of the Tally computer') from exc
    allowed=address.is_loopback or any(address.version==network.version and address in network for network in map(ipaddress.ip_network,('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','fc00::/7')))
    if not allowed or not 1<=port<=65535:
        raise ValueError('The bridge accepts only loopback or private LAN server addresses')
    normalized='['+str(address)+']' if address.version==6 else str(address)
    return f'{parts.scheme}://{normalized}:{port}/'


def export_request(kind,company='',from_date='',to_date=''):
    if kind not in FIELDS:raise ValueError('Choose companies, ledgers or vouchers')
    if kind!='companies' and (not isinstance(company,str) or not company.strip() or len(company)>240):
        raise ValueError('Choose a company returned by the Tally server')
    if bool(from_date)!=bool(to_date):raise ValueError('Supply both from and to dates')
    if from_date:
        if datetime.date.fromisoformat(from_date)>datetime.date.fromisoformat(to_date):raise ValueError('From date must not be after to date')
    env=ET.Element('ENVELOPE');header=ET.SubElement(env,'HEADER')
    for name,value in (('VERSION','1'),('TALLYREQUEST','Export'),('TYPE','Data' if kind=='vouchers' else 'Collection'),('ID','DayBook' if kind=='vouchers' else 'TallyForgeReadOnly')):ET.SubElement(header,name).text=value
    desc=ET.SubElement(ET.SubElement(env,'BODY'),'DESC');variables=ET.SubElement(desc,'STATICVARIABLES')
    ET.SubElement(variables,'SVEXPORTFORMAT').text='$$SysName:XML'
    if company:ET.SubElement(variables,'SVCURRENTCOMPANY').text=company
    if from_date:
        ET.SubElement(variables,'SVFROMDATE',{'TYPE':'Date'}).text=from_date.replace('-','')
        ET.SubElement(variables,'SVTODATE',{'TYPE':'Date'}).text=to_date.replace('-','')
    if kind=='vouchers':
        return ET.tostring(env,encoding='utf-8',xml_declaration=True)
    definition=ET.SubElement(ET.SubElement(ET.SubElement(desc,'TDL'),'TDLMESSAGE'),'COLLECTION',{'NAME':'TallyForgeReadOnly','ISMODIFY':'No'})
    object_type,fields=FIELDS[kind]
    ET.SubElement(definition,'TYPE').text=object_type
    for field in fields:ET.SubElement(definition,'NATIVEMETHOD').text=field
    return ET.tostring(env,encoding='utf-8',xml_declaration=True)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Redirects are not accepted from the Tally endpoint')


def assert_read_only(content):
    """Refuse to transmit anything that is not a plain Export request.

    Tally import/alter/delete requests use TALLYREQUEST=Import, IMPORTDATA/IMPORT bodies or
    ACTION attributes (Create/Alter/Delete/Cancel). Collections must be ISMODIFY=No."""
    try:root=ET.fromstring(content)
    except ET.ParseError as exc:raise ValueError('Request is not well-formed XML') from exc
    if root.tag!='ENVELOPE' or (root.findtext('HEADER/TALLYREQUEST') or '').strip()!='Export':
        raise ValueError('Only TALLYREQUEST=Export requests may be sent to TallyPrime')
    for node in root.iter():
        if node.tag.upper() in ('IMPORTDATA','IMPORT','TALLYMESSAGE','VOUCHER','LEDGER','GROUP'):
            raise ValueError('Request carries data objects; the bridge never sends import, alter or delete requests')
        if any(k.upper()=='ACTION' for k in node.attrib):raise ValueError('ACTION attributes are not allowed')
        if node.tag.upper()=='COLLECTION' and node.get('ISMODIFY','No')!='No':raise ValueError('Collections must be ISMODIFY=No')
        if node.tag.upper()=='NATIVEMETHOD' and '*' in (node.text or ''):raise ValueError('Wildcard method fetches are refused; they can overload TallyPrime')
        if node.tag.upper()=='FETCH' and '*' in (node.text or ''):raise ValueError('Wildcard fetches are refused; they can overload TallyPrime')


def send_xml(endpoint,content,max_bytes=MAX_RESPONSE,timeout=8):
    assert_read_only(content)
    req=request.Request(endpoint_url(endpoint),data=content,headers={'Content-Type':'text/xml; charset=utf-8','Accept':'application/xml'},method='POST')
    opener=request.build_opener(request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(req,timeout=timeout) as response:
            raw=response.read(max_bytes+1)
    except (error.URLError,TimeoutError,OSError) as exc:
        raise ValueError('Cannot reach the Tally server. Start TallyPrime, load the company, enable Server/Both mode and check its configured port (commonly 9000).') from exc
    if len(raw)>max_bytes:raise ValueError(f'Tally response exceeds {max_bytes//(1024*1024)} MB; narrow the request')
    return raw


def _envelope(kind,report_id,company,variables,tdl=None):
    env=ET.Element('ENVELOPE');header=ET.SubElement(env,'HEADER')
    for name,value in (('VERSION','1'),('TALLYREQUEST','Export'),('TYPE',kind),('ID',report_id)):ET.SubElement(header,name).text=value
    desc=ET.SubElement(ET.SubElement(env,'BODY'),'DESC');sv=ET.SubElement(desc,'STATICVARIABLES')
    ET.SubElement(sv,'SVEXPORTFORMAT').text='$$SysName:XML'
    ET.SubElement(sv,'SVCURRENTCOMPANY').text=company
    for key,value in variables.items():
        node=ET.SubElement(sv,key)
        if key.endswith('DATE'):node.set('TYPE','Date')
        node.text=value
    if tdl is not None:desc.append(tdl)
    return ET.tostring(env,encoding='utf-8',xml_declaration=True)


def _yyyymmdd(value):
    return datetime.date.fromisoformat(value).strftime('%Y%m%d')


def report_request(report_id,company,variables=None):
    """Read-only request for a built-in Tally report, e.g. 'List of Accounts' or 'Trial Balance'."""
    if not company or len(company)>240:raise ValueError('Company name is required')
    body=_envelope('Data',report_id,company,variables or {})
    assert_read_only(body);return body


def collection_request(name,object_type,methods,company,from_date='',to_date='',fetch=()):
    """Read-only collection request with explicitly named methods (no wildcards)."""
    if not company or len(company)>240:raise ValueError('Company name is required')
    variables={}
    if from_date:variables.update(SVFROMDATE=_yyyymmdd(from_date),SVTODATE=_yyyymmdd(to_date))
    tdl=ET.Element('TDL');coll=ET.SubElement(ET.SubElement(tdl,'TDLMESSAGE'),'COLLECTION',{'NAME':name,'ISMODIFY':'No'})
    ET.SubElement(coll,'TYPE').text=object_type
    for method in methods:ET.SubElement(coll,'NATIVEMETHOD').text=method
    for item in fetch:ET.SubElement(coll,'FETCH').text=item
    body=_envelope('Collection',name,company,variables,tdl);assert_read_only(body);return body


def parse_response(raw,kind,limit=500,offset=0):
    if not isinstance(raw,bytes) or len(raw)>MAX_RESPONSE:raise ValueError('Invalid or oversized Tally response')
    # Tally may return UTF-16 XML. Decode explicitly before checking XML declarations.
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):text=raw.decode('utf-16')
    elif raw[:4] in (b'<\x00?\x00',b'<\x00E\x00'):text=raw.decode('utf-16-le')
    else:text=raw.decode('utf-8-sig')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)',text,re.I):raise ValueError('DTD/entity declarations are not accepted')
    try:root=ET.fromstring(text)
    except ET.ParseError as exc:raise ValueError('The endpoint did not return valid Tally XML') from exc
    errors=[(node.text or '').strip() for node in root.iter() if node.tag.upper()=='LINEERROR' and (node.text or '').strip()]
    statuses=[(node.text or '').strip() for node in root.iter() if node.tag.upper()=='STATUS']
    if errors or '0' in statuses:raise ValueError('Tally rejected the export request: '+('; '.join(errors[:3]) or 'status 0'))
    valid_data = any(n.tag.upper()=='COLLECTION' for n in root.iter()) or (kind=='vouchers' and any(n.tag.upper()=='DATA' for n in root.iter()))
    if root.tag.upper()!='ENVELOPE' or not valid_data:
        raise ValueError('No Tally collection was returned; verify the company is loaded and export access is allowed')
    expected=FIELDS[kind][0].upper();nodes=[n for n in root.iter() if n.tag.upper()==expected]
    def field(node,name):
        item=next((c for c in node if c.tag.upper()==name),None)
        return (item.text or '').strip() if item is not None else ''
    rows=[]
    for node in nodes[offset:offset+limit]:
        if kind=='companies':rows.append({'name':field(node,'NAME') or node.get('NAME',''),'guid':field(node,'GUID'),'starting_from':field(node,'STARTINGFROM'),'books_from':field(node,'BOOKSBEGINNINGFROM')})
        elif kind=='ledgers':rows.append({'name':field(node,'NAME') or node.get('NAME',''),'parent':field(node,'PARENT'),'opening_balance_raw':field(node,'OPENINGBALANCE'),'closing_balance_raw':field(node,'CLOSINGBALANCE'),'guid':field(node,'GUID')})
        else:
            entries=[]
            for entry in node:
                if entry.tag.upper() in ('ALLLEDGERENTRIES.LIST','LEDGERENTRIES.LIST'):
                    entries.append({'ledger':field(entry,'LEDGERNAME'),'amount_raw':field(entry,'AMOUNT')})
            rows.append({'date_raw':field(node,'DATE'),'number':field(node,'VOUCHERNUMBER'),'voucher_type':field(node,'VOUCHERTYPENAME'),'party':field(node,'PARTYLEDGERNAME'),'amount_raw':field(node,'AMOUNT'),'is_cancelled':field(node,'ISCANCELLED'),'is_optional':field(node,'ISOPTIONAL'),'guid':field(node,'GUID'),'entries':entries})
    return {'kind':kind,'rows':rows,'returned':len(rows),'total_in_response':len(nodes),'offset':offset,'has_more':offset+len(rows)<len(nodes),
            'read_only':True,'local_books_modified':False,'note':'Values are returned by TallyPrime. Raw amount strings retain Tally formatting/signs and are not converted into local accounting balances.'}


def read(data,transport=None):
    endpoint=endpoint_url(data.get('endpoint','http://127.0.0.1:9000'))
    kind=data.get('kind','companies')
    limit,offset=int(data.get('limit',500)),int(data.get('offset',0))
    if not 1<=limit<=1000 or not 0<=offset<=1000000:raise ValueError('Limit must be 1–1000 and offset 0–1000000')
    body=export_request(kind,data.get('company',''),data.get('from_date',''),data.get('to_date',''))
    raw=(transport or send_xml)(endpoint,body)
    result=parse_response(raw,kind,limit,offset)
    return {**result,'endpoint':endpoint,'company':data.get('company',''),'native_file_decoder':False}
