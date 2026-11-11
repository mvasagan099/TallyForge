#!/usr/bin/env python3
"""Import TallyPrime XML exports into a TallyForge company (re-runnable).

Usage:
    python import_tally_xml.py --data-dir <TallyForge data folder> --source 100124
    python import_tally_xml.py --data-dir <folder> --files Master.xml DayBook.xml Transactions.xml
    python import_tally_xml.py --data-dir <folder> --live http://127.0.0.1:9000 --company "Name" \
        --capture-dir <new empty folder> --confirm-live
    python import_tally_xml.py --data-dir <folder> --company "Name" --replay-dir <capture folder>   # offline

Live mode only sends read-only Export requests, one at a time, and stops at the first failure.
It can load TallyPrime heavily: save your work and avoid other activity in TallyPrime while it runs.

The first run creates a new numbered company folder; later runs re-sync the same
company by Tally GUID. Source files are only read. Exit code 1 if verification fails.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine.tally_xml_import import TallyImportError, default_sources, import_company  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--data-dir', required=True, help='TallyForge company data folder (must not be the native Tally folder)')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--source', help='Folder containing Master.xml and optional DayBook.xml / Transactions.xml')
    group.add_argument('--files', nargs='+', help='Explicit export files; Master.xml first')
    group.add_argument('--live', metavar='URL', help='Running TallyPrime server, e.g. http://127.0.0.1:9000 (read-only)')
    group.add_argument('--replay-dir', help='Re-run a previous live capture offline (no network)')
    parser.add_argument('--company', help='Company name as loaded in TallyPrime (required with --live / --replay-dir)')
    parser.add_argument('--capture-dir', help='With --live: save every raw response here (must be new/empty)')
    parser.add_argument('--as-at', help='With --live: trial balance date YYYY-MM-DD (default: last voucher date)')
    parser.add_argument('--confirm-live', action='store_true', help='Acknowledge that live export can load TallyPrime heavily')
    parser.add_argument('--name', help='Company name override')
    parser.add_argument('--json', action='store_true', help='Print the full JSON report')
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir).resolve()
    if args.live or args.replay_dir:
        if not args.company:
            parser.error('--company is required with --live / --replay-dir')
        if args.live and not args.confirm_live:
            parser.error('--live requires --confirm-live (save your work in TallyPrime first)')
        if args.capture_dir and args.replay_dir:
            parser.error('--capture-dir applies to --live only')
    else:
        sources = default_sources(args.source) if args.source else [Path(f) for f in args.files]
        if any(Path(s).resolve().parent == data_dir for s in sources):
            parser.error('--data-dir must differ from the folder holding the source exports')
    try:
        if args.live or args.replay_dir:
            from engine.tally_live import import_from_tally
            report = import_from_tally(data_dir, args.company, endpoint=args.live, capture_dir=args.capture_dir,
                                       replay_dir=args.replay_dir, as_at=args.as_at)
        else:
            report = import_company(data_dir, sources, args.name)
    except TallyImportError as exc:
        print(f'Import failed: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"{'Created' if report['created'] else 'Re-synced'} {report['tally_company']} -> {report['folder']} ({report['storage_key']})")
        print('Import:', json.dumps(report['import']['counts'], ensure_ascii=False))
        for item in report['import']['skipped']:
            print(f"  skipped {item['kind']} {item['name']!r}: {item['reason']}")
        for c in report['verification']['checks']:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['check']}: expected {c['expected']}, got {c['actual']}")
    return 0 if report['verification']['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
