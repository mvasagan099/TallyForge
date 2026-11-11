"""Import from / export to Tally data for the app: validated requests, one background job at a time.

Imports can take minutes on a large company, so they run in a thread and the UI polls ``status``.
Only one import runs at once (a live import loads TallyPrime). Every path comes from a local Admin request;
nothing here ever writes to TallyPrime.
"""
import hashlib
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from engine.native_tally import detect
from engine.tally_xml_import import TallyImportError, default_sources, import_company

_lock = threading.Lock()
_jobs = {}
_order = []
MAX_JOBS = 20
MAX_PASTED_BYTES = 64 * 1024 * 1024


def _require_data_folder(company_store):
    root = Path(company_store.root)
    if detect(root):
        raise ValueError('Choose a separate TallyForge data folder before importing; the selected folder holds native Tally files')
    return root


def _existing_dir(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} is required')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError(f'{label} must be an absolute path')
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError(f'{label} must be a folder')
    return path


def summarize(report):
    verification = report['verification']
    failed = [c for c in verification['checks'] if not c['ok']]
    return {'storage_key': report['storage_key'], 'created': report['created'], 'tally_company': report['tally_company'],
            'counts': report['import']['counts'], 'checks': len(verification['checks']), 'verification_ok': verification['ok'],
            'failed_checks': failed[:10], 'non_posting_vouchers': report['import'].get('non_posting_vouchers_kept_out_of_balances', {}),
            'mode': report.get('mode', 'files'), 'requests': len(report.get('requests', []))}


def start(company_store, data):
    """Validate and start an import job. Returns {'job': id}."""
    root = _require_data_folder(company_store)
    mode = data.get('mode')
    if mode == 'files':
        folder = _existing_dir(data.get('folder'), 'Export folder')
        if folder == root:
            raise ValueError('The export folder must differ from the TallyForge data folder')
        sources = default_sources(folder)
        name = data.get('company_name') or None

        def run():
            return summarize(import_company(root, sources, name))
    elif mode in ('live', 'replay'):
        from engine.tally_live import import_from_tally
        company = data.get('company')
        if not isinstance(company, str) or not company.strip():
            raise ValueError('Company name is required')
        if mode == 'live':
            if data.get('confirm_live') is not True:
                raise ValueError('Confirm that you saved your work in TallyPrime: a live import can load it heavily')
            endpoint = data.get('endpoint', 'http://127.0.0.1:9000')
            capture = data.get('capture_dir') or None
            if capture:
                capture = str(Path(capture).expanduser())
                if not Path(capture).is_absolute():
                    raise ValueError('Capture folder must be an absolute path')

            def run():
                return summarize(import_from_tally(root, company.strip(), endpoint=endpoint, capture_dir=capture))
        else:
            replay = _existing_dir(data.get('replay_dir'), 'Capture folder')

            def run():
                return summarize(import_from_tally(root, company.strip(), replay_dir=str(replay)))
    else:
        raise ValueError("Choose a mode: 'files', 'live' or 'replay'")
    if not _lock.acquire(blocking=False):
        raise ValueError('Another import is already running')
    job = uuid.uuid4().hex[:12]
    _jobs[job] = {'job': job, 'mode': mode, 'state': 'running', 'started': time.time()}
    _order.append(job)
    while len(_order) > MAX_JOBS:
        _jobs.pop(_order.pop(0), None)

    def worker():
        try:
            _jobs[job].update(state='done', result=run())
        except (TallyImportError, ValueError, OSError) as exc:
            _jobs[job].update(state='error', error=str(exc))
        except Exception as exc:  # never lose the job; report the type, not internals
            _jobs[job].update(state='error', error=f'Import failed unexpectedly ({type(exc).__name__})')
        finally:
            _jobs[job]['finished'] = time.time()
            _lock.release()
    threading.Thread(target=worker, daemon=True, name='tally-import-' + job).start()
    return {'job': job}


def status(job):
    info = _jobs.get(job)
    if info is None:
        raise ValueError('Unknown import job')
    out = dict(info)
    out['seconds'] = round((info.get('finished') or time.time()) - info['started'], 1)
    return out


def import_xml_text(company_store, text, company_name=None):
    """Synchronous import of pasted XML text (small files). Returns counts in the shape the old screen expects."""
    root = _require_data_folder(company_store)
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Missing XML content')
    if len(text) > MAX_PASTED_BYTES:
        raise ValueError('XML is too large to paste; use the folder import instead')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'Master.xml'
        path.write_bytes(text.encode('utf-8'))
        report = import_company(root, [path], company_name)
    c = report['import']['counts']
    return {'success': report['verification']['ok'], 'storage_key': report['storage_key'], 'summary': summarize(report),
            'imported_groups': c.get('groups_created', 0) + c.get('groups_updated', 0) + c.get('groups_unchanged', 0),
            'imported_ledgers': c.get('ledgers_created', 0) + c.get('ledgers_updated', 0) + c.get('ledgers_unchanged', 0),
            'imported_items': c.get('stock_items_created_from_vouchers', 0),
            'imported_vouchers': c.get('vouchers_created', 0) + c.get('vouchers_replaced', 0) + c.get('vouchers_unchanged', 0),
            'errors': [f"{x['check']}: {x['actual']}" for x in report['verification']['checks'] if not x['ok']]}


def export_to_file(company_store, active_db, data):
    """Write a company as Tally XML to a local path. Never overwrites unless asked."""
    from engine.tally_xml_export import export_company_xml
    key = data.get('storage_key')
    target_db = company_store.open(key) if key else active_db
    try:
        xml = export_company_xml(target_db)
        company = target_db.get_company()['name']
        voucher_count = target_db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0]
    finally:
        if key:
            target_db.close()
    value = data.get('path')
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Export path is required')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError('Export path must be absolute')
    if path.is_dir():
        path = path / ((re.sub(r'[^A-Za-z0-9._ -]+', '_', company).strip() or 'company') + '.xml')
    if not path.parent.is_dir():
        raise ValueError('The export folder does not exist')
    if path.exists() and data.get('overwrite') is not True:
        raise ValueError(f'{path.name} already exists; choose another name or confirm overwrite')
    fd, temp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(xml)
        os.replace(temp, path)
    except Exception:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    return {'path': str(path), 'bytes': len(xml), 'vouchers': voucher_count, 'sha256': hashlib.sha256(xml).hexdigest()}
