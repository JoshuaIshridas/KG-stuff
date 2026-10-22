import importlib.util
from pathlib import Path

import pytest
from pubmed_kg.graph import ROOT


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_listing_inventory_and_storage_guards():
    runner = script('full_coverage_test')
    html = '<a href="pubmed26n0001.xml.gz">pubmed26n0001.xml.gz</a> 2026-01-29 14:48 19M\n'
    rows, size = runner.listing_inventory(html, 'baseline')
    assert len(rows) == 1 and size == 19 * 2 ** 20
    assert runner.capacity_status(5 * 2 ** 30, 70 * 2 ** 30, 10, None) == 'blocked_insufficient_storage'
    assert runner.capacity_status(100 * 2 ** 30, 70 * 2 ** 30, 10, None) == 'requires_operator_storage_budget'
    assert runner.capacity_status(100 * 2 ** 30, 70 * 2 ** 30, 10, 150) == 'blocked_insufficient_storage'
    assert runner.capacity_status(200 * 2 ** 30, 70 * 2 ** 30, 10, 150) == 'capacity_preflight_passed_not_a_storage_guarantee'
    with pytest.raises(ValueError):
        runner.listing_inventory('', 'baseline')


def test_complete_file_json_audit_including_deletions():
    auditor = script('audit_full_sources')
    progress = []
    baseline = auditor.audit(ROOT / 'fixtures/baseline.xml', batch_size=1, progress=progress.append)
    updates = auditor.audit(ROOT / 'fixtures/update.xml', batch_size=1)
    assert baseline['counts']['article'] == 3
    assert updates['counts']['article'] == 1 and updates['counts']['delete'] == 1
    assert baseline['xml_to_json_verified'] is True
    assert baseline['rdf_verified_by_this_test'] is False
    assert progress == [1, 2, 3]


def test_full_run_main_never_ingests_when_capacity_guard_fails(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    runner = script('full_coverage_test')
    class Response:
        def __init__(self, kind):
            number = 1 if kind == 'baseline' else 2
            self.text = f'<a href="pubmed26n{number:04}.xml.gz">file</a> 2026-01-01 01:00 40G'
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Client:
        def get(self, url): return Response('baseline' if '/baseline/' in url else 'updatefiles')
    monkeypatch.setattr(runner, 'Client', Client)
    monkeypatch.setattr(runner.shutil, 'disk_usage', lambda path: SimpleNamespace(free=5 * 2 ** 30))
    monkeypatch.setattr(runner, 'CorpusStore', lambda path: pytest.fail('storage guard bypassed'))
    report = tmp_path / 'report.json'
    monkeypatch.setattr('sys.argv', ['full_coverage_test.py', '--state', str(tmp_path / 'state'), '--report', str(report), '--run'])
    assert runner.main() == 2
    result = json.loads(report.read_text())
    assert result['status'] == 'blocked_insufficient_storage'
    assert result['full_coverage_verified'] is False
    assert not (tmp_path / 'state' / 'ledger.sqlite').exists()


def test_full_run_main_passes_only_after_complete_replay(tmp_path, monkeypatch):
    import gzip
    import hashlib
    import json
    from types import SimpleNamespace
    runner = script('full_coverage_test')
    payload = gzip.compress((ROOT / 'fixtures/baseline.xml').read_bytes(), mtime=0)
    class Response:
        def __init__(self, text): self.text = text
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Client:
        def get(self, url):
            if url.endswith('.md5'):
                return Response(hashlib.md5(payload).hexdigest())
            # A nonempty updates listing is not essential to a valid frozen baseline.
            # Use two synthetic complete files to exercise ordered replacement.
            number = 1 if '/baseline/' in url else 2
            return Response(f'<a href="pubmed26n{number:04}.xml.gz">file</a> 2026-01-01 01:00 1M')
        def download(self, url, target, md5):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    monkeypatch.setattr(runner, 'Client', Client)
    monkeypatch.setattr(runner.shutil, 'disk_usage', lambda path: SimpleNamespace(free=200 * 2 ** 30))
    report = tmp_path / 'report.json'
    monkeypatch.setattr('sys.argv', ['full_coverage_test.py', '--state', str(tmp_path / 'state'),
                                  '--report', str(report), '--required-free-gib', '150', '--run'])
    assert runner.main() == 0
    result = json.loads(report.read_text())
    assert result['full_coverage_verified'] is True
    assert result['verification']['current_articles'] == 3
    assert result['frozen_inventory_record_retention_percent'] == 100
    assert result['medical_device_recall_percent'] is None
