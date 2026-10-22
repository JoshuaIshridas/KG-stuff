#!/usr/bin/env python3
"""Verify every supplied source event through XML -> JSON without retaining text.

This audits whole *supplied files*, not all PubMed. It intentionally does not
claim RDF coverage; use full_coverage_test.py on an adequately provisioned VM.
Temporary JSON batches bound storage use and are removed after validation.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pubmed_kg.audit import canonical, digest_event, json_inventory, reconcile, source_inventory
from pubmed_kg.corpus import atomic_json, sha256
from pubmed_kg.parser import records
from pubmed_kg.store import now


def audit(path, batch_size=100, progress=None):
    expected = source_inventory(path)
    counts, elements = Counter(), Counter()
    fingerprint = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix='pubmed-source-audit-') as temp:
        batch = Path(temp) / 'records.jsonl'
        pending = []
        def flush():
            batch.write_text(''.join(pending))
            actual = json_inventory(batch)
            counts.update(actual['counts'])
            elements.update(actual['metadata_elements'])
            with batch.open() as stream:
                for line in stream:
                    event = json.loads(line)
                    record = event['record']
                    digest_event(fingerprint, event['pmid'], record['record_kind'] if record else 'delete',
                                 record['metadata_fields'] if record else [])
            pending.clear()
            if progress:
                progress(sum(counts.get(kind, 0) for kind in ['article', 'book', 'delete']))
        for pmid, record in records(path):
            pending.append(canonical(dict(pmid=pmid, operation='delete' if record is None else 'upsert', record=record)) + '\n')
            if len(pending) >= batch_size:
                flush()
        if pending:
            flush()
    actual = dict(counts=dict(counts), metadata_elements=dict(elements), event_sha256=fingerprint.hexdigest())
    reconcile(expected, actual)
    return dict(name=path.name, sha256=sha256(path), counts=actual['counts'],
                event_sha256=actual['event_sha256'], xml_to_json_verified=True,
                record_event_retention=1.0, eligible_metadata_retention=1.0,
                rdf_verified_by_this_test=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    report = dict(checked_at=now(), status='running', full_pubmed_coverage_verified=False, sources=[])
    for path in args.sources:
        def progress(events):
            if events % 1000 == 0:
                report['progress'] = dict(file=path.name, json_verified_events=events)
                atomic_json(args.output, report)
                print(json.dumps(report['progress']), flush=True)
        try:
            result = audit(path, progress=progress)
        except Exception as error:
            report.update(status='failed', failed_source=path.name, error_type=type(error).__name__)
            atomic_json(args.output, report)
            print('Whole-source audit failed; source text and exception details redacted.', file=sys.stderr)
            return 1
        report['sources'].append(result)
        report.pop('progress', None)
        atomic_json(args.output, report)
        print(json.dumps(result), flush=True)
    report.update(status='passed', elapsed_seconds=round(time.monotonic() - started, 2),
                  scope='Every event and eligible metadata field in supplied complete files; this is not a full PubMed or full RDF test.')
    atomic_json(args.output, report)


if __name__ == '__main__':
    raise SystemExit(main())
