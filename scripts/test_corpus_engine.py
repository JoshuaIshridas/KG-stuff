#!/usr/bin/env python3
"""Test explicit local source files through the disk-backed corpus engine.

This performs no download. Input files may contain restricted text; outputs
contain counts/provenance only. A sample cannot certify a complete corpus.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pubmed_kg.corpus import CorpusStore, atomic_json, sha256, verify_corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=25)
    args = parser.parse_args()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='pubmed-corpus-test-') as root:
        store = CorpusStore(root)
        with store.lock():
            inputs = []
            for order, path in enumerate(args.sources, 1):
                store.ingest_delta(path, order, args.batch_size)
                inputs.append(dict(name=path.name, sha256=sha256(path)))
            report = verify_corpus(store)
            report.update(input_files=inputs, elapsed_seconds=round(time.monotonic() - started, 2),
                          full_corpus_tested=False, scope='Only the explicitly supplied local files; no population device-recall claim.')
            atomic_json(args.output, report)
            print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
