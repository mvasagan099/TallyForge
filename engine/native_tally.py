"""Read-only native company detection and bounded, explicitly partial metadata.

This does not decode ledger/voucher records. The observed length-prefixed UTF-16
company-name field is a preview hint, not a financial-data compatibility claim.
"""
import hashlib
from pathlib import Path
import re

VERSIONS = ('1800','900','500')
FIELD = re.compile(rb'\x02\x10(..)\x00\x0f(..)', re.DOTALL)


def file_map(folder):
    return {p.name.casefold():p for p in folder.iterdir() if p.is_file() and not p.is_symlink()}


def detect(folder):
    folder=Path(folder)
    if not folder.is_dir() or folder.is_symlink():
        return None
    files=file_map(folder)
    for version in VERSIONS:
        if f'company.{version}' in files and f'manager.{version}' in files:
            return version,files
    return None


def name_hint(path):
    with path.open('rb') as stream:
        data=stream.read(1024*1024)
    for match in FIELD.finditer(data):
        field=int.from_bytes(match[1],'little')
        length=int.from_bytes(match[2],'little')
        if field not in (27,28) or length<4 or length>2048 or length%2:
            continue
        start=match.end();raw=data[start:start+length]
        if len(raw)!=length or raw[-2:]!=b'\0\0':
            continue
        try:value=raw[:-2].decode('utf-16-le',errors='strict')
        except UnicodeError:continue
        if not value.strip() or len(value)>200 or any(ord(c)<32 for c in value):
            continue
        return {'value':value,'byte_offset':start,'field_id':field,'basis':'Observed length-prefixed company metadata; full schema unverified'}
    return None


def folders(root):
    root=Path(root)
    if detect(root):
        yield 'native:.',root
    for child in sorted(root.iterdir()):
        if child.name.isascii() and child.name.isdigit() and child.is_dir() and not child.is_symlink() and detect(child):
            yield 'native:'+child.name,child


def catalogue(root):
    result=[]
    for key,folder in folders(root):
        version,files=detect(folder)
        hint=name_hint(files['company.'+version]) if version=='1800' else None
        result.append({'storage_key':key,'company_number':folder.name,'name':hint['value'] if hint else folder.name,
                       'format':'Tally binary .'+version,'metadata_only':True,'standalone_books_readable':False})
    return result


def inspect(root,key):
    for identity,folder in folders(root):
        if identity!=key:continue
        version,files=detect(folder)
        hint=name_hint(files['company.'+version]) if version=='1800' else None
        inventory=[]
        for name,path in sorted(files.items()):
            if not (name.endswith('.'+version) or name.endswith('.tsf')):continue
            before=path.stat()
            with path.open('rb') as stream:header=stream.read(512)
            if path.stat().st_mtime_ns!=before.st_mtime_ns:
                raise ValueError('Tally files changed during inspection; close the company or inspect a stable copy')
            inventory.append({'name':path.name,'bytes':before.st_size,'header_sha256':hashlib.sha256(header).hexdigest()})
        return {'storage_key':key,'folder':str(folder),'format':'Tally binary .'+version,'name_hint':hint,
                'record_reader_version':5,
                'reference_files':[files[n].name for n in ('master.xml','transactions.xml','daybook.xml') if n in files],
                'files':inventory,'metadata_only':True,'standalone_books_readable':False,'source_files_written':False,
                'message':'Native company detected. Preview records, compare XML references and read calibrated Sales dates/allocations without running TallyPrime. Complete financial books, posting-state interpretation and opening balances remain unfinished.'}
    raise ValueError('Native company not found in the selected data folder')


def file_identity(st):
    """Stable identity of a file across an open handle and a path stat.

    On Windows fstat() and stat() report different inode/ctime for the same untouched file, which made
    every read look like "the file changed"; size and mtime are the portable signal there."""
    import os
    if os.name == 'nt':
        return (st.st_size, st.st_mtime_ns)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
