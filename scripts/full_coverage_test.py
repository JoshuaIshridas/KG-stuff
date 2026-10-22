#!/usr/bin/env python3
"""Guarded, resumable full-inventory acceptance test. No source download by default.

--run explicitly starts the complete reconstruction after storage checks. The
operator's --required-free-gib budget must include RDF/JSON/database/scratch space;
the directory-size estimate alone is NOT a sufficient storage budget.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pubmed_kg.corpus import BASE, CorpusStore, atomic_json, make_plan, run_plan, validate_plan, verify_corpus
from pubmed_kg.network import Client
from pubmed_kg.store import now


def listing_inventory(html, kind):
    sources, estimated_bytes = [], 0
    for line in html.splitlines():
        match = re.search(r'href="(pubmed\d+n\d+\.xml\.gz)"[^>]*>.*?</a>(.*)', line)
        if not match:
            continue
        name, tail = match.groups()
        size = re.sub('<[^>]*>', '', tail).split()[-1]
        value = re.fullmatch(r'(\d+(?:\.\d+)?)([KMGT]?)', size)
        if not value:
            raise ValueError('unrecognized directory size; inspect official listing')
        power = 'KMGT'.index(value[2]) + 1 if value[2] else 0
        estimated_bytes += float(value[1]) * 1024 ** power
        sources.append((name, BASE + kind + '/' + name))
    if not sources:
        raise ValueError('empty source listing')
    return sources, int(estimated_bytes)


def capacity_status(free_bytes, compressed_bytes, reserve_gib, required_free_gib):
    if reserve_gib < 1 or (required_free_gib is not None and required_free_gib <= 0):
        raise ValueError('positive capacity settings required; reserve must be at least 1 GiB')
    # Necessary lower bound only. This does not claim to size full RDF output.
    source_floor = compressed_bytes + int(reserve_gib * 2 ** 30)
    if free_bytes < source_floor:
        return 'blocked_insufficient_storage'
    if required_free_gib is None:
        return 'requires_operator_storage_budget'
    if free_bytes < max(source_floor, int(required_free_gib * 2 ** 30)):
        return 'blocked_insufficient_storage'
    return 'capacity_preflight_passed_not_a_storage_guarantee'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--required-free-gib', type=float)
    parser.add_argument('--reserve-gib', type=float, default=10)
    parser.add_argument('--batch-size', type=int, default=50)
    args = parser.parse_args()
    args.state.parent.mkdir(parents=True, exist_ok=True)
    client = Client()
    listings, sizes = {}, {}
    for kind in ['baseline', 'updatefiles']:
        with client.get(BASE + kind + '/') as response:
            listings[kind], sizes[kind] = listing_inventory(response.text, kind)
    observed_plan = make_plan(listings['baseline'], listings['updatefiles'])
    plan_path = args.report.with_suffix('.plan.json')
    # Resume the same frozen inventory. Updates arriving mid-test are a separate
    # subsequent run, not a moving denominator for this acceptance test.
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        validate_plan(plan)
        if observed_plan['sources'][:len(plan['sources'])] != plan['sources']:
            raise ValueError('published inventory changed incompatibly; inspect annual rollover')
    else:
        plan = observed_plan
        atomic_json(plan_path, plan)
    free = shutil.disk_usage(args.state.parent).free
    capacity = capacity_status(free, sum(sizes.values()), args.reserve_gib, args.required_free_gib)
    report = dict(checked_at=now(), status=capacity, full_coverage_verified=False,
                  planned_files=len(plan['sources']), live_observed_files=len(observed_plan['sources']),
                  baseline_files=len(listings['baseline']), update_files=len(listings['updatefiles']),
                  free_gib=free / 2 ** 30, estimated_compressed_gib=sum(sizes.values()) / 2 ** 30,
                  required_free_gib=args.required_free_gib, reserve_gib=args.reserve_gib,
                  plan=str(plan_path), plan_sha256=plan['plan_sha256'],
                  source_files_downloaded_during_preflight=0,
                  limitation='Rounded directory-size estimate excludes JSON, SQLite, RDF, audit history, scratch space and backups. Operator budget and ongoing monitoring are required.')
    atomic_json(args.report, report)
    print(json.dumps(report, indent=2), flush=True)
    if not args.run:
        return 0
    if capacity != 'capacity_preflight_passed_not_a_storage_guarantee':
        return 2
    store = CorpusStore(args.state)
    with store.lock():
        store.bind_plan(plan)
        try:
            while store.status()['verified_files'] < len(plan['sources']):
                if shutil.disk_usage(args.state).free < args.reserve_gib * 2 ** 30:
                    raise OSError('storage reserve reached before next source')
                progress = run_plan(store, plan, client, max_files=1, batch_size=args.batch_size)
                report['progress'] = progress
                atomic_json(args.report, report)
                print(json.dumps(dict(verified_files=progress['verified_files'], planned_files=len(plan['sources']))), flush=True)
            verification = verify_corpus(store)
            complete = verification['status'] == 'complete_for_frozen_inventory' and verification['verification'] == 'passed'
            report.update(status='passed' if complete else 'incomplete', completed_at=now(),
                          verification=verification, full_coverage_verified=complete,
                          frozen_inventory_record_retention_percent=100.0 if complete else None,
                          medical_device_recall_percent=None)
            atomic_json(args.report, report)
            print(json.dumps(report, indent=2), flush=True)
            return 0 if complete else 2
        except Exception as error:
            report.update(status='interrupted_or_failed', error_type=type(error).__name__,
                          progress=store.status(), full_coverage_verified=False)
            atomic_json(args.report, report)
            print('Full test stopped; inspect safe report and resume the same state/plan.', file=sys.stderr)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
