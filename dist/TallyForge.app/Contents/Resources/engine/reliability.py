"""Complete SQLite snapshots and company-scoped configuration."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

FEATURES = ('maintain-acc', 'bill-wise', 'cost-centres', 'multi-curr', 'interest-calc',
            'budget-scenario', 'maintain-inv', 'multi-godowns', 'batch-mfg', 'mfg-bom',
            'compound-uom', 'price-levels', 'gst', 'tds', 'einvoice', 'ewaybill', 'editlog', 'tdl')


class ReliabilityMixin:
    def init_reliability(self):
        with self.conn:
            self.conn.execute('CREATE TABLE IF NOT EXISTS voucher_gst_snapshot (voucher_id INTEGER PRIMARY KEY REFERENCES voucher(id) ON DELETE CASCADE, payload TEXT NOT NULL)')
            self.conn.execute('CREATE TABLE IF NOT EXISTS company_setting (company_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(company_id,key))')
            self.conn.execute('CREATE TABLE IF NOT EXISTS gst_registration (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL, gstin TEXT NOT NULL, state_code TEXT NOT NULL, address TEXT NOT NULL DEFAULT \'\', pincode TEXT NOT NULL DEFAULT \'\', UNIQUE(company_id,gstin))')
            self.conn.execute('CREATE TABLE IF NOT EXISTS ims_invoice (id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, period TEXT NOT NULL, supplier_gstin TEXT NOT NULL, invoice_number TEXT NOT NULL, invoice_date TEXT NOT NULL, taxable_value REAL NOT NULL, tax_value REAL NOT NULL, action TEXT NOT NULL DEFAULT \'Pending\', remarks TEXT NOT NULL DEFAULT \'\', portal_status TEXT NOT NULL DEFAULT \'Not uploaded\', UNIQUE(company_id,period,supplier_gstin,invoice_number))')
            from engine.payroll_workflows import init_schema
            init_schema(self)
            self.conn.execute('CREATE TABLE IF NOT EXISTS payroll_run (company_id INTEGER NOT NULL, month TEXT NOT NULL, voucher_id INTEGER NOT NULL, payslips TEXT NOT NULL, PRIMARY KEY(company_id,month))')
            self.conn.execute('CREATE TABLE IF NOT EXISTS import_mapping (company_id INTEGER NOT NULL, name TEXT NOT NULL, mapping TEXT NOT NULL, PRIMARY KEY(company_id,name))')
            for table, definition in (('stock_item', 'valuation_method TEXT DEFAULT \'Average Cost\''), ('stock_item', 'standard_cost REAL DEFAULT 0'), ('voucher', 'gst_registration_id INTEGER')):
                if definition.split()[0] not in {r[1] for r in self.conn.execute(f'PRAGMA table_info({table})')}:
                    self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')

    def get_setting(self, key, default=None):
        row = self.conn.execute('SELECT value FROM company_setting WHERE company_id=? AND key=?', (self.active_company_id, key)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.conn:
            self.conn.execute('INSERT OR REPLACE INTO company_setting VALUES (?,?,?)', (self.active_company_id, key, json.dumps(value)))
        return value

    def get_features(self):
        return {**{name: True for name in FEATURES}, **self.get_setting('features', {})}

    def set_features(self, values):
        if not isinstance(values, dict) or any(k not in FEATURES or not isinstance(v, bool) for k, v in values.items()):
            raise ValueError('Features must contain known feature names and boolean values')
        if any(values.get(k) is False for k in ('maintain-acc', 'editlog', 'tdl')):
            raise ValueError('Accounts, edit log and TDL are required')
        result = self.set_setting('features', {**self.get_features(), **values})
        with self.conn:
            self._log_edit('Company', str(self.active_company_id), 'FEATURES', json.dumps(result))
        return result

    def require_feature(self, name):
        if not self.get_features().get(name, False):
            raise ValueError(f'Company feature is disabled: {name}')

    def create_local_backup(self, backup_dir='backups'):
        if self.conn.in_transaction:
            raise ValueError('Finish the pending database transaction before taking a snapshot')
        folder = Path(backup_dir)
        folder.mkdir(parents=True, exist_ok=True)
        filename = folder / ('TallyBackup_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.db')
        temp = str(filename) + '.tmp'
        target = sqlite3.connect(temp)
        try:
            self.conn.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed')
        finally:
            target.close()
        os.replace(temp, filename)
        os.chmod(filename, 0o600)
        checksum = hashlib.sha256(filename.read_bytes()).hexdigest()
        metadata = {'format': 'TallyForge SQLite snapshot v2', 'sha256': checksum,
                    'company_id': self.active_company_id, 'timestamp': datetime.datetime.now().isoformat()}
        Path(str(filename) + '.meta.json').write_text(json.dumps(metadata))
        with self.conn:
            self._log_edit('Company', str(self.active_company_id), 'BACKUP', str(filename))
        return str(filename)

    def restore_local_backup(self, backup_path, target_db_path=None):
        source_path = Path(backup_path).resolve(strict=True)
        # Reject incomplete old exports rather than silently discarding unrepresented tables.
        if source_path.read_bytes()[:16] != b'SQLite format 3\x00':
            raise ValueError('Legacy JSON is an incomplete export. Import it into a separate company; full restore requires a SQLite snapshot.')
        metadata_path = Path(str(source_path) + '.meta.json')
        if not metadata_path.is_file():
            raise ValueError('Backup checksum metadata is required')
        metadata = json.loads(metadata_path.read_text())
        if not __import__('hmac').compare_digest(metadata.get('sha256', ''), hashlib.sha256(source_path.read_bytes()).hexdigest()):
            raise ValueError('Backup checksum verification failed')
        source = sqlite3.connect(source_path.as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or source.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Backup database integrity or foreign key validation failed')
            names = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'company', 'ledger', 'voucher', 'voucher_entry'} <= names:
                raise ValueError('Not a TallyForge database backup')
            if target_db_path is not None and str(target_db_path) != self.db_path:
                if Path(target_db_path).exists():
                    raise ValueError('Restore to a new destination to avoid overwriting another company')
                target = sqlite3.connect(target_db_path)
                try:
                    source.backup(target)
                finally:
                    target.close()
            else:
                directory = Path(self.db_path).resolve().parent if self.db_path != ':memory:' else Path(tempfile.gettempdir())
                self.create_local_backup(str(directory / 'snapshots' / 'pre_restore'))
                # SQLite backup is transactional, includes WAL content and preserves all tables/IDs.
                source.backup(self.conn)
                if not self.conn.execute('SELECT 1 FROM company WHERE id=?', (self.active_company_id,)).fetchone():
                    row = self.conn.execute('SELECT id FROM company ORDER BY id LIMIT 1').fetchone()
                    self.active_company_id = row[0] if row else 0
                self.init_reliability()
                with self.conn:
                    self._log_edit('Company', str(self.active_company_id), 'RESTORE', str(source_path))
        finally:
            source.close()
        return True

    def set_inventory_valuation(self, item, method, standard_cost=0):
        methods = ('Average Cost', 'FIFO', 'LIFO', 'Last Purchase Cost', 'Standard Cost', 'Zero Cost')
        if method not in methods or float(standard_cost) < 0:
            raise ValueError('Invalid valuation method or standard cost')
        with self.conn:
            cur = self.conn.execute('UPDATE stock_item SET valuation_method=?, standard_cost=? WHERE name=? AND company_id=?', (method, standard_cost, item, self.active_company_id))
            if not cur.rowcount:
                raise ValueError('Stock item not found')
            self._log_edit('StockItem', item, 'VALUATION', method)
        return {'item': item, 'method': method}

    def inventory_value(self, item, closing_qty, default):
        from decimal import Decimal
        row = self.conn.execute('SELECT opening_qty,opening_rate,valuation_method,standard_cost FROM stock_item WHERE name=?', (item,)).fetchone()
        method = row['valuation_method'] or 'Average Cost'
        if method == 'Average Cost':
            return default
        qty = Decimal(str(closing_qty))
        if qty <= 0 or method == 'Zero Cost':
            return Decimal('0.00')
        if method == 'Standard Cost':
            return (qty * Decimal(str(row['standard_cost']))).quantize(Decimal('0.01'))
        layers = [[Decimal(str(row['opening_qty'])), Decimal(str(row['opening_rate']))]]
        events = self.conn.execute("SELECT vi.qty,vi.rate,vi.direction FROM voucher_inventory vi JOIN voucher v ON v.id=vi.voucher_id WHERE vi.stock_item_name=? AND v.company_id=? AND v.is_cancelled=0 AND v.is_optional=0 ORDER BY v.date,v.id,vi.id", (item, self.active_company_id))
        last_cost = Decimal(str(row['opening_rate']))
        for e in events:
            amount = Decimal(str(e['qty']))
            if e['direction'] == 'In':
                last_cost = Decimal(str(e['rate']))
                layers.append([amount, last_cost])
            elif method in ('FIFO', 'LIFO'):
                while amount > 0 and layers:
                    idx = 0 if method == 'FIFO' else -1
                    take = min(amount, layers[idx][0])
                    layers[idx][0] -= take
                    amount -= take
                    if layers[idx][0] <= 0:
                        layers.pop(idx)
                if amount > 0:
                    raise ValueError('FIFO/LIFO requires chronological non-negative stock; correct negative stock entries first')
        value = qty * last_cost if method == 'Last Purchase Cost' else sum((q * p for q, p in layers), Decimal(0))
        return value.quantize(Decimal('0.01'))

    def save_gst_registration(self, values):
        import re
        gstin = str(values.get('gstin', '')).upper().strip()
        if not re.fullmatch(r'[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]', gstin):
            raise ValueError('A valid 15-character GSTIN is required')
        if not values.get('name', '').strip() or values.get('state_code', gstin[:2]) != gstin[:2]:
            raise ValueError('Registration name and matching GST state code are required')
        with self.conn:
            self.conn.execute('INSERT INTO gst_registration(company_id,name,gstin,state_code,address,pincode) VALUES(?,?,?,?,?,?) ON CONFLICT(company_id,gstin) DO UPDATE SET name=excluded.name,address=excluded.address,pincode=excluded.pincode', (self.active_company_id, values['name'], gstin, gstin[:2], values.get('address', ''), values.get('pincode', '')))
            self._log_edit('Company', str(self.active_company_id), 'GST_REGISTRATION', gstin)
        return self.get_gst_registrations()

    def get_gst_registrations(self):
        return [dict(r) for r in self.conn.execute('SELECT * FROM gst_registration WHERE company_id=? ORDER BY id', (self.active_company_id,))]

    def import_ims(self, period, invoices):
        from datetime import date
        if not isinstance(invoices, list):
            raise ValueError('invoices must be an array')
        with self.conn:
            for invoice in invoices:
                date.fromisoformat(invoice['invoice_date'])
                self.conn.execute('INSERT INTO ims_invoice(company_id,period,supplier_gstin,invoice_number,invoice_date,taxable_value,tax_value) VALUES(?,?,?,?,?,?,?) ON CONFLICT(company_id,period,supplier_gstin,invoice_number) DO UPDATE SET taxable_value=excluded.taxable_value,tax_value=excluded.tax_value', (self.active_company_id, period, invoice['supplier_gstin'], invoice['invoice_number'], invoice['invoice_date'], invoice['taxable_value'], invoice['tax_value']))
            self._log_edit('Company', str(self.active_company_id), 'IMS_IMPORT', period)
        return self.get_ims(period)

    def get_ims(self, period):
        invoices = [dict(r) for r in self.conn.execute('SELECT * FROM ims_invoice WHERE company_id=? AND period=? ORDER BY id', (self.active_company_id, period))]
        return {'period': period, 'invoices': invoices, 'accepted_tax': sum(r['tax_value'] for r in invoices if r['action'] == 'Accepted'), 'portal_sync': False}

    def act_ims(self, invoice_id, action, remarks=''):
        if action not in ('Accepted', 'Rejected', 'Pending'):
            raise ValueError('Invalid IMS action')
        with self.conn:
            cur = self.conn.execute("UPDATE ims_invoice SET action=?,remarks=?,portal_status='Not uploaded' WHERE company_id=? AND id=?", (action, remarks, self.active_company_id, invoice_id))
            if not cur.rowcount:
                raise ValueError('Invoice not found')
            self._log_edit('IMSInvoice', str(invoice_id), action.upper(), remarks)
        return {'status': 'saved locally', 'portal_sync': False}
