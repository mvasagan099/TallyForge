"""Folder-backed company catalogue. Discovery never migrates or modifies databases."""
import json
import os
from pathlib import Path
import sqlite3

from engine.accounting_db import EnterpriseAccountingDB


class CompanyStore:
    def __init__(self, default_path, settings_path, explicit=False, legacy_name='tally_data.db'):
        self.settings_path = Path(settings_path)
        self.legacy_name = legacy_name
        selected = default_path
        if not explicit:
            try:
                selected = json.loads(self.settings_path.read_text())['data_path']
            except (OSError, ValueError, KeyError, TypeError):
                pass
        self.root = Path(selected).expanduser().resolve()
        if not self.root.is_dir():
            self.root = Path(default_path).resolve()

    def set_path(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Enter an existing company data folder')
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Company data path must be a folder')
        # Persist only after validation, without changing the currently open company.
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.settings_path.with_suffix('.tmp')
        temp.write_text(json.dumps({'data_path': str(root)}))
        os.replace(temp, self.settings_path)
        self.root = root

    def _files(self):
        for folder in sorted(self.root.iterdir()):
            if folder.name.isascii() and folder.name.isdigit() and folder.is_dir() and not folder.is_symlink():
                path = folder / 'company.db'
                if path.is_file() and not path.is_symlink() and not (folder / 'company.archived').exists():
                    yield folder.name, path, False
        legacy = self.root / self.legacy_name
        if legacy.is_file() and not legacy.is_symlink():
            yield 'legacy', legacy, True

    def catalogue(self, active_path=None, active_id=None):
        companies, errors = [], []
        for number, path, legacy in self._files():
            try:
                conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    for row in conn.execute('SELECT * FROM company ORDER BY id'):
                        c = dict(row)
                        c.pop('vault_password', None)
                        cid = c['id']
                        last = conn.execute('SELECT MAX(date) FROM voucher WHERE company_id=?', (cid,)).fetchone()[0]
                        c.update(storage_key=f'{number}:{cid}', company_number=number,
                                 legacy=legacy, last_voucher_date=last,
                                 is_active=(str(path) == active_path and cid == active_id))
                        companies.append(c)
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                errors.append({'folder': number, 'error': str(exc)})
        from engine.native_tally import catalogue as native_catalogue
        return {'data_path': str(self.root), 'companies': companies, 'native_companies': native_catalogue(self.root), 'errors': errors}

    def open(self, key):
        if isinstance(key,str) and key.startswith('native:'):
            raise ValueError('Native Tally books require the read-only TallyPrime connection; standalone financial decoding is not implemented')
        # Resolve exclusively through the catalogue; never accept a client-supplied filename.
        for number, path, legacy in self._files():
            prefix = number + ':'
            if isinstance(key, str) and key.startswith(prefix) and key[len(prefix):].isdigit():
                cid = int(key[len(prefix):])
                check = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
                try:
                    if not check.execute('SELECT 1 FROM company WHERE id=?', (cid,)).fetchone():
                        break
                finally:
                    check.close()
                # Existing books must never receive demo balances merely because a report opens them.
                candidate = EnterpriseAccountingDB(str(path), seed_demo=False)
                candidate.seed_demo = legacy
                candidate.select_company(cid)
                return candidate
        raise ValueError('Company not found in the selected data folder')

    def create(self, data):
        from engine.native_tally import detect
        if detect(self.root):
            raise ValueError('Choose a separate TallyForge data folder before creating local books; the selected folder contains native Tally files')
        name = str(data.get('name', '')).strip()
        if not name:
            raise ValueError('Company name is required')
        from datetime import date
        fy_from = data.get('financial_year_from', '2026-04-01')
        fy_to = data.get('financial_year_to', '2027-03-31')
        books = data.get('books_from', fy_from)
        if not date.fromisoformat(fy_from) <= date.fromisoformat(books) <= date.fromisoformat(fy_to):
            raise ValueError('Books beginning date must fall within the financial year')
        number = 10000
        while True:
            folder = self.root / str(number)
            try:
                folder.mkdir()
                break
            except FileExistsError:
                number += 1
        candidate = None
        try:
            candidate = EnterpriseAccountingDB(str(folder / 'company.db'), seed_demo=False)
            cid = candidate.create_company(name=name, **{k: data[k] for k in (
                'formal_name', 'gstin', 'state', 'state_code', 'address',
                'financial_year_from', 'financial_year_to', 'currency_symbol') if k in data})
            candidate.conn.execute('UPDATE company SET books_from=?, pincode=? WHERE id=?',
                                   (books, data.get('pincode', ''), cid))
            candidate.conn.execute('UPDATE currency SET symbol=? WHERE is_base=1',
                                   (data.get('currency_symbol', '₹'),))
            candidate.conn.commit()
            candidate.checkpoint()
            return f'{number}:{cid}'
        except Exception:
            if candidate:
                candidate.close()
                candidate = None
            import shutil
            shutil.rmtree(folder)
            raise
        finally:
            if candidate:
                candidate.close()

    def archive_company(self, candidate):
        import datetime
        path = Path(candidate.db_path).resolve()
        if path.name != 'company.db' or path.parent.parent != self.root or not path.parent.name.isdigit():
            raise ValueError('Only a numbered company in the selected folder can be removed')
        archive = self.root / 'deleted-companies' / (path.parent.name + '-' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        recovery = candidate.create_local_backup(str(archive))
        # Keep the original folder for manual recovery, outside the discoverable numeric namespace.
        candidate.checkpoint()
        (path.parent / 'company.archived').write_text(recovery)
        return {'recovery_backup': recovery}

    def consolidated(self, keys, eliminations=None, authorizations=None, contexts=None):
        from engine.consolidation import consolidate
        return consolidate(self, keys, eliminations, authorizations, contexts)

    def search(self, query, active_db=None):
        query = str(query).strip().lower()
        if len(query) < 2:
            return {'results': [], 'skipped_protected': 0}
        results, skipped = [], 0
        paths = {number: str(path.resolve()) for number, path, legacy in self._files()}
        for company in self.catalogue()['companies']:
            own = bool(active_db and company['id'] == active_db.active_company_id and paths.get(company['storage_key'].split(':')[0]) == str(Path(active_db.db_path).resolve()))
            if company.get('security_enabled') and not own:
                skipped += 1
                continue
            member = self.open(company['storage_key'])
            try:
                pattern = '%' + query + '%'
                for kind, sql in (
                    ('Ledger', 'SELECT name AS label FROM ledger WHERE company_id=? AND LOWER(name) LIKE ? LIMIT 30'),
                    ('Stock item', 'SELECT name AS label FROM stock_item WHERE company_id=? AND LOWER(name) LIKE ? LIMIT 30'),
                    ('Voucher', "SELECT voucher_number || ' · ' || COALESCE(narration,'') AS label FROM voucher WHERE company_id=? AND LOWER(voucher_number || ' ' || COALESCE(narration,'')) LIKE ? LIMIT 30")):
                    for row in member.conn.execute(sql, (member.active_company_id, pattern)):
                        results.append({'storage_key':company['storage_key'],'company_name':company['name'],'kind':kind,'label':row['label']})
            finally:
                member.close()
        return {'results': results[:100], 'skipped_protected': skipped}
