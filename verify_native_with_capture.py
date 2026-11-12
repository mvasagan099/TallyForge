#!/usr/bin/env python3
"""Check the native .1800 voucher reader against a captured live TallyPrime import (read-only).

    python verify_native_with_capture.py --native-root <folder holding the company folder> \\
        --key native:100124 --capture-dir <capture from import_tally_xml.py --capture-dir> --company "Name"

Compares, per voucher (matched by GUID): type, date, alter ID and per-ledger net postings, then prints the
native-only per-ledger totals against the totals derived from TallyPrime's data. Nothing is written.
Exit code 1 if any decodable voucher disagrees.
"""
import argparse
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine.native_vouchers import compare_with_bundle, ledger_nets, read_native_vouchers, select_active  # noqa: E402
from engine.tally_live import Source, build_bundle, fetch_all  # noqa: E402
from engine.tally_xml_import import NON_POSTING_KINDS, reference_figures  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--native-root', required=True)
    ap.add_argument('--key', required=True, help='e.g. native:100124')
    ap.add_argument('--capture-dir', required=True)
    ap.add_argument('--company', required=True)
    args = ap.parse_args(argv)
    native = read_native_vouchers(Path(args.native_root), args.key)
    active = select_active(native['records'])
    print(f"native records {len(native['records'])}, active {len(active)}: {dict(Counter(r['type'] for r in active))}")
    bundle, _, _ = build_bundle(fetch_all(Source(replay_dir=args.capture_dir), args.company))
    counts, mismatches = compare_with_bundle(active, bundle)
    for key, value in sorted(counts.items()):
        print(f'  {key}: {value}')
    nets, skipped = ledger_nets(active, NON_POSTING_KINDS)
    norm = lambda d: {k.replace('\r\n', '\n'): v.quantize(Decimal('0.01')) for k, v in d.items()}
    ours, ref = norm(nets), norm(reference_figures(bundle)['per_ledger'])
    differ = sorted(k for k in set(ours) | set(ref) if ours.get(k, Decimal(0)) != ref.get(k, Decimal(0)))
    print(f'native ledger totals vs TallyPrime-derived: {len(differ)} of {len(set(ours) | set(ref))} ledgers differ; skipped {skipped}')
    print('Opening balances are zero in this reference; native opening balances are NOT decoded or checked.')
    bad = counts.get('per-ledger net DIFFERS', 0) + len(differ) + sum(
        counts.get(k, 0) for k in ('truth voucher missing from native',)) + (counts.get('matched by GUID', 0) - counts.get('type equal', 0))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
