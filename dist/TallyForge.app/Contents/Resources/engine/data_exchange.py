"""Bounded XLSX reading and reusable, staged ledger/stock imports."""
import base64
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile
from xml.etree import ElementTree as ET

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def workbook_rows(encoded):
    content = base64.b64decode(encoded, validate=True)
    if len(content) > 10 * 1024 * 1024:
        raise ValueError('Workbook exceeds 10 MB')
    with zipfile.ZipFile(BytesIO(content)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 40 * 1024 * 1024:
            raise ValueError('Expanded workbook exceeds 40 MB')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            strings = [''.join(r.itertext()) for r in root.findall('s:si', NS)]
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        sheet = workbook.find('s:sheets/s:sheet', NS)
        if sheet is None:
            raise ValueError('Workbook has no worksheets')
        rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        target = next((r.get('Target') for r in rels if r.get('Id') == rid), None)
        if not target or '..' in target.split('/'):
            raise ValueError('Invalid worksheet reference')
        filename = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        root = ET.fromstring(archive.read(filename))
        rows = []
        for row in root.findall('s:sheetData/s:row', NS):
            values = {}
            for cell in row.findall('s:c', NS):
                ref = ''.join(c for c in cell.get('r', '') if c.isalpha())
                if not ref or len(ref) > 3:
                    raise ValueError('Invalid column reference')
                col = 0
                for char in ref:
                    col = col * 26 + ord(char.upper()) - 64
                if col > 256:
                    raise ValueError('Import supports at most 256 columns')
                if cell.find('s:f', NS) is not None:
                    raise ValueError('Replace spreadsheet formulae with values before importing')
                val = cell.findtext('s:v', default='', namespaces=NS)
                if cell.get('t') == 's':
                    val = strings[int(val)]
                elif cell.get('t') == 'inlineStr':
                    val = ''.join(cell.find('s:is', NS).itertext())
                values[col-1] = val
            if values:
                rows.append([values.get(i, '') for i in range(max(values)+1)])
            if len(rows) > 20001:
                raise ValueError('Import supports at most 20,000 data rows')
        if not rows:
            raise ValueError('Worksheet is empty')
        headers = [str(v).strip() for v in rows[0]]
        if len(set(headers)) != len(headers) or any(not h for h in headers):
            raise ValueError('Header names must be unique and non-empty')
        return headers, [dict(zip(headers, r + [''] * (len(headers)-len(r)))) for r in rows[1:]]


def import_workbook(db, data):
    headers, rows = workbook_rows(data['content_base64'])
    name = data.get('mapping_name', 'Default')
    saved = db.conn.execute('SELECT mapping FROM import_mapping WHERE company_id=? AND name=?', (db.active_company_id, name)).fetchone()
    mapping = data.get('mapping') or (json.loads(saved[0]) if saved else {})
    kind = data.get('kind', 'Ledgers')
    fields = {'Ledgers': {'name','group_name','opening_balance','opening_type'},
              'Stock Items': {'name','group_name','uom_symbol','opening_qty','opening_rate'}}
    if kind not in fields or 'name' not in mapping or any(k not in fields[kind] or v not in headers for k,v in mapping.items()):
        raise ValueError('Choose a name column and valid mappings for Ledgers or Stock Items')
    mapped = [{key: row.get(column, '') for key,column in mapping.items()} for row in rows if any(row.values())]
    if data.get('preview', True):
        return {'headers': headers, 'rows': mapped[:20], 'row_count': len(mapped), 'applied': False}
    from engine.accounting_db import EnterpriseAccountingDB
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'staged.db'
        connection = sqlite3.connect(str(path))
        db.conn.backup(connection)
        connection.close()
        candidate = EnterpriseAccountingDB(str(path), seed_demo=False)
        candidate.active_company_id = db.active_company_id
        try:
            for index, row in enumerate(mapped, 2):
                if not row['name'].strip():
                    raise ValueError(f'Row {index}: name is required')
                try:
                    if kind == 'Ledgers':
                        candidate.add_ledger(**row)
                    else:
                        candidate.add_stock_item(**row)
                except Exception as exc:
                    raise ValueError(f'Row {index}: {exc}') from exc
            with candidate.conn:
                candidate.conn.execute('INSERT OR REPLACE INTO import_mapping VALUES(?,?,?)', (db.active_company_id, name, json.dumps(mapping)))
            candidate.conn.backup(db.conn)
        finally:
            candidate.close()
    with db.conn:
        db._log_edit('Company', str(db.active_company_id), 'EXCEL_IMPORT', f'{len(mapped)} {kind}')
    return {'applied': True, 'imported': len(mapped), 'mapping_name': name}


def export_workbook(db, kind='Ledgers'):
    from xml.sax.saxutils import escape
    if kind == 'Ledgers':
        headers = ['name','group_name','opening_balance','opening_type']
        records = [{**dict(r),'opening_type':r['opening_balance_type']} for r in db.conn.execute('SELECT * FROM ledger WHERE company_id=? ORDER BY name',(db.active_company_id,))]
    elif kind == 'Stock Items':
        headers = ['name','group_name','uom_symbol','opening_qty','opening_rate']
        records = [dict(r) for r in db.conn.execute('SELECT * FROM stock_item WHERE company_id=? ORDER BY name',(db.active_company_id,))]
    else:
        raise ValueError('Choose Ledgers or Stock Items')
    def column(number):
        name=''
        while number:
            number,rem=divmod(number-1,26)
            name=chr(65+rem)+name
        return name
    sheet='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for i,row in enumerate([headers]+[[record.get(k,'') for k in headers] for record in records],1):
        sheet+='<row r="'+str(i)+'">'
        for j,value in enumerate(row,1):
            sheet+=f'<c r="{column(j)}{i}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'
        sheet+='</row>'
    sheet+='</sheetData></worksheet>'
    output=BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml',sheet)
    return output.getvalue()
