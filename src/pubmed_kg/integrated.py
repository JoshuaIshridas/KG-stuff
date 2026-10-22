"""Offline end-to-end acceptance using the supplied FDA ontology and fake data."""
import json
from pathlib import Path
import tempfile

from rdflib import Graph

from .corpus import CorpusStore, atomic_json, sha256, verify_corpus
from .device_links import link_corpus, review_links
from .enrichment import enrich_corpus
from .fda import FdaIndex, build_index, schema_contract
from .graph import ROOT, check, write
from .materialize import Materialized
from .pipeline import load_contexts


def approvals(store):
    result = []
    for candidate, payload in store.db.execute('SELECT id,payload FROM link_candidates ORDER BY id'):
        value = json.loads(payload)
        if value['evidence']['scheme'] in ('510k', 'pma'):
            result.append(dict(candidate_id=candidate, status='verified', reviewer='SYNTHETIC-reviewer',
                               reviewed_at='2026-10-22T00:00:00Z', evidence_method='independent-identifier-confirmation'))
    return result


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pubmed-integrated-') as temporary:
        root = Path(temporary)
        manifest = build_index([ROOT / 'fixtures/fda-canonical-synthetic.ttl'], root / 'fda.sqlite')
        index, store = FdaIndex(root / 'fda.sqlite'), CorpusStore(root / 'corpus')
        paths = {name: ROOT / 'fixtures' / name for name in ['baseline.xml', 'update.xml']}
        config = dict(trial_device_file=str(ROOT / 'fixtures/trial-devices-synthetic.ttl'))
        with store.lock(), Materialized(root / 'rdf') as target:
            load_contexts(target, index, config)
            store.ingest_delta(paths['baseline.xml'], 1)
            link_corpus(store, index, source_paths=paths)
            enrich_corpus(store, None, limit=3, fixture=ROOT / 'fixtures/pubtator.json')
            review_links(store, index, approvals(store))
            target.apply(store)
            before = target.verify_current(store)
            store.ingest_delta(paths['update.xml'], 2)
            # Previous approvals/annotations must not survive replacement.
            assert not store.db.execute('SELECT 1 FROM link_candidates WHERE pmid=?', ('900000001',)).fetchone()
            assert not store.db.execute('SELECT 1 FROM enrichment WHERE pmid=?', ('900000001',)).fetchone()
            target.apply(store)
            stale = target.query('PREFIX k: <https://example.org/pubmed-kg/v1/> SELECT ?p WHERE { <https://example.org/pubmed-kg/v1/article/900000001> k:link ?p }')
            assert not stale['results']['bindings']
            link_corpus(store, index, source_paths=paths)
            enrich_corpus(store, None, limit=3, fixture=ROOT / 'fixtures/pubtator.json')
            decisions = approvals(store)
            review_links(store, index, decisions)
            target.apply(store)
            verification = verify_corpus(store)
            materialized = target.verify_current(store)
            results = []
            for query in json.loads((ROOT / 'queries/fda/catalog.json').read_text()):
                result = target.query((ROOT / 'queries/fda' / query['file']).read_text())
                count = len(result['results']['bindings'])
                if count != query['expected_fixture_rows']:
                    raise ValueError('canonical FDA query expectation failed: ' + query['file'] + ': ' + str(count))
                results.append(dict(file=query['file'], rows=count, result=result))
            target.export(output / 'sample.nq')
            graph = Graph()
            for quad in target.rdf:
                from .fda import rdf_term
                graph.add((rdf_term(quad.subject), rdf_term(quad.predicate), rdf_term(quad.object)))
            check(graph)
            write(graph, output / 'sample.ttl')
            atomic_json(output / 'query-results.json', results)
            atomic_json(output / 'verification.json', verification)
            atomic_json(output / 'synthetic-review-decisions.json', decisions)
            summary = dict(status='passed', synthetic=True, canonical_fda_ontology=schema_contract(ROOT / 'integration/fda/ontology.ttl'),
                           fda_catalog=manifest['identity'], current_articles=materialized['current_articles'],
                           before_update_articles=before['current_articles'], deleted_pmids=verification['deleted_pmids'],
                           queries_passed=len(results), rdf_triples=len(graph),
                           stale_links_and_annotations_cleared=True, source_metadata_retention=verification['eligible_metadata_retention'],
                           full_pubmed_tested=False, overall_device_recall='not_measured')
            atomic_json(output / 'summary.json', summary)
            return summary
