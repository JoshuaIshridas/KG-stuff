"""One scheduler command for metadata, FDA candidates, annotations and local RDF."""
import glob
import json
from pathlib import Path
import shutil

from .audit import VerificationError
from .corpus import CorpusStore, atomic_json, discover_plan, run_plan, sha256, verify_corpus
from .device_links import link_corpus
from .enrichment import enrich_corpus
from .fda import FdaIndex, build_index, schema_contract
from .materialize import Materialized
from .network import Client


def profile(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if set(data) - {'state', 'plan', 'fda_index', 'rdf_store', 'fda_ontology', 'fda_sources',
                    'fda_format', 'annotations_per_run', 'reserve_gib', 'batch_size', 'catalog_scope', 'trial_device_file'}:
        raise ValueError('unknown pipeline configuration field')
    for key in ['state', 'plan', 'fda_index', 'rdf_store', 'fda_ontology', 'trial_device_file']:
        if data.get(key):
            data[key] = str((path.parent / data[key]).resolve())
    inputs = []
    for pattern in data.get('fda_sources', []):
        matched = sorted(glob.glob(str(path.parent / pattern)))
        if not matched:
            raise VerificationError('FDA source pattern matched no files: ' + pattern)
        inputs.extend(matched)
    data['fda_sources'] = inputs
    data.setdefault('annotations_per_run', 10)
    data.setdefault('reserve_gib', 10)
    data.setdefault('batch_size', 50)
    if not 0 <= data['annotations_per_run'] <= 1000 or data['reserve_gib'] <= 0:
        raise ValueError('invalid annotation limit or storage reserve')
    schema_contract(data['fda_ontology'])
    from .graph import ROOT
    if sha256(data['fda_ontology']) != sha256(ROOT / 'integration/fda/ontology.ttl'):
        raise VerificationError('profile ontology differs from the supplied canonical ontology in this release')
    return data


def initialize(config):
    result = build_index(config['fda_sources'], config['fda_index'], config['fda_ontology'], config.get('fda_format'))
    index = FdaIndex(config['fda_index'])
    if not index.db.execute('SELECT 1 FROM identifiers LIMIT 1').fetchone():
        raise VerificationError('FDA ontology is present but FDA instance identifiers are missing; provide FDA data TTLs')
    return result


def status(store, index):
    result = store.status()
    expected = result['current_articles']
    linked = store.db.execute('''SELECT COUNT(*) FROM articles a JOIN link_status l ON a.pmid=l.pmid
                                WHERE l.ord=a.ord AND l.catalog=?''', (index.identity,)).fetchone()[0]
    annotations = {state: count for state, count in store.db.execute('SELECT status,COUNT(*) FROM enrichment GROUP BY status')}
    result.update(fda_catalog=index.identity, fda_ontology_sha256=index.manifest['ontology']['sha256'],
                  linked_articles=linked, linking_pending=expected - linked,
                  annotation_statuses=annotations,
                  annotation_pending_or_unavailable=expected - annotations.get('complete', 0),
                  automatic_verified_device_links=0)
    return result


def load_contexts(target, index, config):
    target.load_fda(index)
    if config.get('trial_device_file'):
        # This optional operator-provided registry crosswalk is separate from FDA.
        # Restrict it to non-textual IDs, links, types, provenance and labels.
        from .fda import parse_rdf
        from .graph import K, P
        from rdflib import RDF, RDFS
        allowed = {str(RDF.type), str(RDFS.label), str(RDFS.seeAlso), str(K.device), str(P.wasDerivedFrom)}
        # Validate the crosswalk on disk before publishing it. A local trial IRI
        # alone does not establish either registry existence or a device link.
        import re
        import sqlite3
        import tempfile
        import pyoxigraph as ox
        with tempfile.TemporaryDirectory(prefix='crosswalk-', dir=target.root) as temporary:
            db = sqlite3.connect(str(Path(temporary) / 'checks.sqlite'))
            db.execute('CREATE TABLE checks(subject TEXT PRIMARY KEY, typed INTEGER DEFAULT 0, provenance INTEGER DEFAULT 0, linked INTEGER DEFAULT 0)')
            for quad in parse_rdf(config['trial_device_file']):
                if quad.predicate.value not in allowed:
                    raise VerificationError('trial crosswalk contains unsupported/text predicates')
                subject = quad.subject.value
                if not isinstance(quad.subject, ox.NamedNode) or not re.fullmatch(re.escape(str(K)) + r'trial/NCT[0-9]{8}', subject):
                    raise VerificationError('trial crosswalk requires local normalized NCT trial subjects')
                db.execute('INSERT OR IGNORE INTO checks(subject) VALUES(?)', (subject,))
                predicate = quad.predicate.value
                if predicate == str(K.device):
                    if not isinstance(quad.object, ox.NamedNode) or not index.has_type(quad.object.value, 'MedicalDevice'):
                        raise VerificationError('trial crosswalk device must be an explicitly typed FDA MedicalDevice')
                    db.execute('UPDATE checks SET linked=1 WHERE subject=?', (subject,))
                elif predicate == str(P.wasDerivedFrom):
                    if not isinstance(quad.object, ox.NamedNode):
                        raise VerificationError('trial crosswalk provenance must be an IRI')
                    db.execute('UPDATE checks SET provenance=1 WHERE subject=?', (subject,))
                elif predicate == str(RDF.type):
                    if not isinstance(quad.object, ox.NamedNode) or quad.object.value != str(K.ClinicalTrial):
                        raise VerificationError('trial crosswalk type must be ClinicalTrial')
                    db.execute('UPDATE checks SET typed=1 WHERE subject=?', (subject,))
            if db.execute('SELECT 1 FROM checks WHERE linked=1 AND (typed=0 OR provenance=0) LIMIT 1').fetchone():
                raise VerificationError('trial device association requires ClinicalTrial type and source provenance')
            db.close()
        target.load_context(parse_rdf(config['trial_device_file']), 'urn:pmkg:trial-device-crosswalk', sha256(config['trial_device_file']))



def update(config, max_files=1, refresh_inventory=True):
    index = FdaIndex(config['fda_index'])
    if index.manifest['ontology']['sha256'] != sha256(config['fda_ontology']):
        raise VerificationError('configured FDA ontology differs from the catalog index')
    store = CorpusStore(config['state'])
    with store.lock():
        store.check_contract()
        plan_path = Path(config['plan'])
        client = Client()
        if refresh_inventory or not plan_path.exists():
            plan = discover_plan(client)
            store.bind_plan(plan)
            atomic_json(plan_path, plan)
        else:
            plan = json.loads(plan_path.read_text())
            store.bind_plan(plan)
        reserve = config['reserve_gib'] * 2 ** 30
        processed = 0
        with Materialized(config['rdf_store']) as target:
            load_contexts(target, index, config)
            while True:
                # Resume pending FDA linking before downloading another source.
                while True:
                    linked = link_corpus(store, index, max_sources=1)
                    target.apply(store)
                    if not linked['sources_scanned']:
                        break
                if (max_files is not None and processed >= max_files) or store.status()['verified_files'] == len(plan['sources']):
                    break
                if shutil.disk_usage(store.root).free < reserve or shutil.disk_usage(target.root).free < reserve:
                    raise OSError('storage reserve reached; no next source started')
                run_plan(store, plan, client, max_files=1, batch_size=config['batch_size'])
                processed += 1
                print(json.dumps(dict(metadata_sources_processed=processed, committed_sources=store.status()['verified_files'])), flush=True)
            annotation_report = None
            if config['annotations_per_run'] and store.db.execute('SELECT 1 FROM articles LIMIT 1').fetchone():
                annotation_report = enrich_corpus(store, client, limit=config['annotations_per_run'])
                target.apply(store)
            result = dict(status(store, index), metadata_sources_processed_this_run=processed,
                          annotation_run=annotation_report, local_rdf_checkpoint=target.value('checkpoint', '0'))
            atomic_json(store.root / 'pipeline-report.json', result)
            return result


def verify(config):
    store, index = CorpusStore(config['state']), FdaIndex(config['fda_index'])
    with store.lock(), Materialized(config['rdf_store']) as target:
        target.require_current(store)
        result = verify_corpus(store)
        target.require_catalog(index)
        result['materialized'] = target.verify_current(store)
        result['pipeline'] = status(store, index)
        if result['pipeline']['linking_pending']:
            raise VerificationError('FDA linking is incomplete for the configured catalog; run pipeline-link')
        atomic_json(store.root / 'pipeline-verification.json', result)
        return result
