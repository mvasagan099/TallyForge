"""Additional company workflows. Returns None for routes owned by the main handler."""
from pathlib import Path
import datetime
import importlib.util


def get_route(path, query, db, store):
    if path == '/api/search/companies':
        return 200, store.search(query.get('q', [''])[0], db)
    if path == '/api/vouchers/print':
        from engine.invoice_print import build_print
        return 200, build_print(db, int(query['voucher_id'][0]))
    if path == '/api/invoices/preview':
        return 200, db.generate_einvoice_json(int(query['voucher_id'][0]), for_submission=False)
    if path == '/api/company/features':
        return 200, db.get_features()
    if path == '/api/backup/schedule':
        return 200, {'schedule': db.get_setting('backup_schedule', {'enabled': False, 'interval_minutes': 60, 'keep': 14}), 'last_success': db.get_setting('backup_last_success')}
    if path == '/api/backup/versions':
        root = Path(db.db_path).resolve().parent / 'backups'
        versions = [{'path': str(p), 'bytes': p.stat().st_size} for p in sorted(root.rglob('TallyBackup_*.db'), reverse=True)] if root.exists() else []
        return 200, versions
    if path == '/api/gst/books':
        from engine.gst_books import book_report
        return 200, book_report(db, query.get('period', [''])[0], query.get('registration', ['all'])[0])
    if path == '/api/company/consolidation/groups':
        return 200, db.get_setting('consolidation_groups', [])
    if path == '/api/gst/registrations':
        return 200, db.get_gst_registrations()
    if path == '/api/gst/ims':
        return 200, db.get_ims(query.get('period', [''])[0])
    if path == '/api/payroll/month':
        from engine.payroll_workflows import get_inputs
        return 200, get_inputs(db,query.get('period',[''])[0])
    if path == '/api/payroll/preview':
        from engine.payroll_workflows import preview
        return 200, preview(db,query.get('period',[''])[0])
    if path == '/api/payroll/run':
        from engine.payroll_workflows import posted_run
        return 200, posted_run(db,query.get('period',[''])[0])
    if path == '/api/payroll/config':
        return 200, db.get_setting('payroll_config', {})
    if path == '/api/payroll/payslips':
        return 200, [dict(r) for r in db.conn.execute('SELECT * FROM payroll_run WHERE company_id=? ORDER BY month DESC', (db.active_company_id,))]
    if path == '/api/system/capabilities':
        return 200, {
            'full_sqlite_backups': True, 'independent_company_windows': True,
            'local_ims': True, 'connected_gst': False, 'connected_banking': False,
            'aes_gcm_archives': bool(importlib.util.find_spec('cryptography')),
            'live_database_encryption': False, 'native_tallyvault': False,
            'native_tally_data': False, 'native_tally_metadata': True, 'tally_readonly_bridge': True, 'compiled_tcp': False, 'odbc_server': False,
            'excel_mapped_import': True, 'scheduled_local_backup': True,
            'background_when_closed': 'Requires OS scheduling of backup_worker.py',
            'requirements': {
                'connected_gst': 'GST/IRP provider API contract, sandbox credentials and approved return schemas',
                'connected_banking': 'Bank/provider onboarding, API contract and sandbox consent',
                'native_tally_data': 'Standalone financial decoding remains unavailable. Metadata inspection and a read-only running-TallyPrime connection are available.',
                'compiled_tcp': 'Licensed Tally runtime/compiler; custom source TDL subset remains supported',
                'live_database_encryption': 'SQLCipher migration and platform-specific driver required',
                'aes_gcm_archives': 'cryptography dependency from requirements-optional.txt',
                'whatsapp': 'Business account, approved templates, provider API and credentials',
                'smtp': 'SMTP host, user and secret supplied through environment variables'}}
    return None


def post_route(path, data, db, store, user, contexts=None):
    if path.startswith('/api/payroll/') and user['role'] != 'Admin':
        return 403, {'error':'Administrator access required for payroll changes'}
    if path == '/api/payroll/month':
        from engine.payroll_workflows import save_input
        return 200, save_input(db,data)
    if path.startswith('/api/company/consolidation/') and user['role'] != 'Admin':
        return 403, {'error': 'Administrator access required for group reporting'}
    if path == '/api/gst/suggest':
        from engine.gst_entry import suggest_gst
        return 200, suggest_gst(db, data.get('voucher_type', ''), data.get('party_ledger'), data.get('items'),
                                data.get('taxable'), data.get('place_of_supply'))
    if path == '/api/tally/bridge/read':
        if user['role'] != 'Admin':
            return 403, {'error':'Administrator access required to configure/read a Tally connection'}
        from engine.tally_bridge import read
        return 200, read(data)
    if path.startswith('/api/integrations/'):
        return 501, {'error': 'Provider integration is not configured or verified. See Company Tools for requirements.'}
    if path == '/api/share/email':
        if user['role'] != 'Admin':
            return 403, {'error':'Administrator access required for sending company exports'}
        if data.get('confirm_send') is not True:
            raise ValueError('Confirm the recipient before sending')
        from engine.document_delivery import send_workbook
        return 200, send_workbook(db, data['recipient'], data.get('kind','Ledgers'))
    admin = ('/api/company/features', '/api/backup/schedule', '/api/payroll/config', '/api/gst/registrations', '/api/archive/export')
    if path in admin and user['role'] != 'Admin':
        return 403, {'error': 'Administrator access required'}
    if path == '/api/company/features':
        return 200, db.set_features(data)
    if path == '/api/backup/schedule':
        from engine.backup_scheduler import configure
        return 200, configure(db, data)
    if path == '/api/inventory/valuation':
        return 200, db.set_inventory_valuation(data['item'], data['method'], data.get('standard_cost', 0))
    if path == '/api/gst/registrations':
        return 201, db.save_gst_registration(data)
    if path == '/api/gst/ims/import':
        db.require_feature('gst')
        return 200, db.import_ims(data['period'], data['invoices'])
    if path == '/api/gst/ims/action':
        db.require_feature('gst')
        return 200, db.act_ims(int(data['invoice_id']), data['action'], data.get('remarks', ''))
    if path == '/api/import/excel/headers':
        from engine.data_exchange import workbook_rows
        headers, rows = workbook_rows(data['content_base64'])
        return 200, {'headers': headers, 'row_count': len(rows)}
    if path == '/api/import/excel':
        from engine.data_exchange import import_workbook
        return 200, import_workbook(db, data)
    if path == '/api/company/consolidation/authorize':
        from engine.consolidation import allowed_company
        import secrets
        candidate = store.open(data['storage_key'])
        keep = False
        try:
            account = candidate.authenticate_user(data.get('username',''),data.get('password',''))
            if not account or account.get('role') not in ('Admin','Auditor') or not allowed_company(account,candidate.active_company_id):
                return 403, {'error':'Company credentials with financial reporting permission are required'}
            token = candidate.create_session(account)
            if contexts is None:
                raise ValueError('Company contexts are unavailable')
            context = secrets.token_urlsafe(32)
            contexts[context] = candidate
            keep = True
            return 200, {'storage_key':data['storage_key'],'context':context,'token':token}
        finally:
            if not keep:
                candidate.close()
    if path == '/api/company/consolidation/ledgers':
        from engine.consolidation import authorize_member, root_group
        member = store.open(data['storage_key'])
        try:
            authorize_member(member, data['storage_key'], data.get('authorizations', {}), contexts or {})
            if member.conn.execute('SELECT COUNT(*) FROM company').fetchone()[0] != 1:
                raise ValueError('Split legacy shared-company books before file-based consolidation')
            groups = {g['name']:g['parent'] for g in member.get_groups()}
            return 200, [{**row,'root_group':root_group(row['group_name'],groups)} for row in member.get_trial_balance()]
        except PermissionError as exc:
            return 403, {'error':str(exc)}
        finally:
            member.close()
    if path == '/api/company/consolidation/groups':
        from engine.consolidation import save_group
        try:
            store.consolidated(data.get('storage_keys'), data.get('eliminations', []), data.get('authorizations', {}), contexts or {})
            return 201, save_group(db, store, data)
        except PermissionError as exc:
            return 403, {'error':str(exc)}
    if path == '/api/company/consolidation/groups/delete':
        group_id = data.get('id')
        groups = db.get_setting('consolidation_groups', [])
        if group_id not in {g['id'] for g in groups}:
            raise ValueError('Group not found')
        with db.conn:
            import json
            db.conn.execute('INSERT OR REPLACE INTO company_setting VALUES(?,?,?)',(db.active_company_id,'consolidation_groups',json.dumps([g for g in groups if g['id']!=group_id])))
            db._log_edit('ConsolidationGroup',group_id,'DELETE','Deleted saved group definition')
        return 200, {'deleted':group_id}
    if path == '/api/company/consolidated':
        config = data
        if data.get('group_id'):
            config = next((g for g in db.get_setting('consolidation_groups', []) if g['id']==data['group_id']), None)
            if not config:
                raise ValueError('Saved group not found in this company')
            if config['data_path']!=str(store.root):
                raise ValueError('Select the saved group data folder before running it')
        try:
            return 200, store.consolidated(config.get('storage_keys'),config.get('eliminations',[]),data.get('authorizations',{}),contexts or {})
        except PermissionError as exc:
            return 403, {'error':str(exc)}
    if path == '/api/payroll/config':
        names = ('expense_ledger', 'payment_ledger', 'deductions_ledger')
        if set(data) != set(names) or len(set(data.values())) != 3:
            raise ValueError('Choose three different payroll expense, payment and deductions ledgers')
        for field in names:
            if not db.conn.execute('SELECT 1 FROM ledger WHERE name=? AND company_id=?', (data[field], db.active_company_id)).fetchone():
                raise ValueError(f'Ledger not found: {data[field]}')
        return 200, db.set_setting('payroll_config', data)
    if path == '/api/archive/export':
        from engine.archive_crypto import encrypt_snapshot
        folder = Path(db.db_path).resolve().parent / 'archives'
        folder.mkdir(exist_ok=True)
        destination = folder / (datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.tfvault')
        return 201, encrypt_snapshot(db, data.get('password', ''), destination)
    return None
