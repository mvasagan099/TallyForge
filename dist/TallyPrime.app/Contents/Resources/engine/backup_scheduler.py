"""Persistent local backup schedules, usable by app thread or an OS scheduler."""
import datetime
import json
import time
from pathlib import Path


def configure(db, data):
    interval, keep = int(data.get('interval_minutes', 60)), int(data.get('keep', 14))
    if interval < 5 or interval > 10080 or keep < 1 or keep > 365:
        raise ValueError('Backup interval must be 5–10080 minutes; retain 1–365 versions')
    if not isinstance(data.get('enabled', False), bool):
        raise ValueError('enabled must be a boolean')
    return db.set_setting('backup_schedule', {'enabled': data.get('enabled', False), 'interval_minutes': interval, 'keep': keep})


def run_due(db, now=None):
    now = now or datetime.datetime.now()
    schedule = db.get_setting('backup_schedule', {})
    if not schedule.get('enabled') or db.db_path == ':memory:':
        return None
    last = db.get_setting('backup_last_success')
    if last and (now - datetime.datetime.fromisoformat(last)).total_seconds() < schedule['interval_minutes'] * 60:
        return None
    folder = Path(db.db_path).resolve().parent / 'backups' / 'scheduled'
    result = db.create_local_backup(str(folder))
    db.set_setting('backup_last_success', now.isoformat())
    versions = sorted(folder.glob('TallyBackup_*.db'), key=lambda p: p.name, reverse=True)
    for path in versions[schedule['keep']:]:
        meta = Path(str(path)+'.meta.json')
        try:
            if json.loads(meta.read_text()).get('format') != 'TallyForge SQLite snapshot v2':
                continue
            path.unlink()
            meta.unlink()
        except (OSError, ValueError):
            continue
    return result


def scan(store):
    results, seen = [], set()
    for company in store.catalogue()['companies']:
        # Settings are company-specific even in a legacy shared DB.
        member = store.open(company['storage_key'])
        try:
            key = (member.db_path, member.active_company_id)
            if key not in seen:
                seen.add(key)
                result = run_due(member)
                if result:
                    results.append(result)
        finally:
            member.close()
    return results


def start(store, lock):
    import threading
    def worker():
        while True:
            try:
                with lock:
                    scan(store)
            except Exception as exc:
                # A failed backup never advances last_success.
                print(f'[Backup scheduler] {exc}', flush=True)
            time.sleep(30)
    thread = threading.Thread(target=worker, daemon=True, name='company-backups')
    thread.start()
    return thread
