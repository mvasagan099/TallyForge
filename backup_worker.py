"""Run from cron/Task Scheduler/launchd to back up even when the UI is closed."""
import argparse
from pathlib import Path
from engine.company_store import CompanyStore
from engine.backup_scheduler import scan

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    args = parser.parse_args()
    root = Path(args.data_dir).expanduser().resolve(strict=True)
    store = CompanyStore(root, root / 'company-path.json', explicit=True)
    for backup in scan(store):
        print(backup)
