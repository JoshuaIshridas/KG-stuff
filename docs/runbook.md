> **v0.6 integrated operation:** use [production-v06.md](production-v06.md) for FDA indexing, reviewed links and local graph loading. Commands below describe the lower-level/legacy engines; keep separate states. Replacement now clears metadata, annotation and device-link graphs.

> Version 0.5: use the new [full-corpus delta workflow](full-corpus.md) for a baseline/backfill. The snapshot workflow below is retained for small, separate states. The new engine removes per-source full-graph rebuilding; full-corpus execution and semantic device recall remain unproven. Use a fresh state when upgrading.

# Operational runbook

## Schema-v2 verification upgrade

Read [coverage.md](coverage.md). A fresh state directory is required for existing pre-v2 data. Every source is now counted independently, serialized as audited JSONL, verified, then mapped to Turtle and verified again from disk before checkpointing. `pmkg verify` replays all JSON source events and reconciles the ledger, snapshot and deletion manifest. `verify_complete_inventory: true` reconciles current official directory listings automatically at the end of each scheduled update.

## Complete reconstruction and ordering

Create a fresh state directory per annual baseline. Record **every** file in that year's baseline listing, then every applicable daily update in numerical filename order; more than one update may occur per day. Do not mix annual vintages or process updates before baseline completion. Retain the inventory as an audit artifact. Official guidance recommends replacing the local database with each annual baseline.

Build a JSON manifest using `manifest.example.json`. Each `sources` element requires `name`, `url`, `order` (unique contiguous integer), and preferably `md5` from the official `.md5` sidecar. Set `baseline_complete` only after an operator reconciles the complete baseline listing and required updates. The flag is an operator attestation, not independently certified by this tool. Set `discover_updates: true` after establishing the complete baseline inventory. Each update run discovers and atomically appends new same-vintage sequential daily files and their official MD5 checksums, refusing filename gaps and annual rollovers. Alternatively append reviewed entries manually without renumbering old entries. Discovery prints filenames/URLs and does not run a large job. Review year rollover and gaps before extending the manifest. `update` processes entries sequentially and stops on any failure. Do not omit a failed earlier file to force progress.

Filename order is assigned explicitly rather than guessed from article dates. An article's PMID Version is not a reliable replacement ordering key. Source order controls replacement, and tombstones prevent older sources resurrecting deleted articles. Use a globally later order for targeted top-ups, which are current retrieval snapshots. This means later scheduled sources must use still later orders; do not reserve a huge order for top-ups and then process older-numbered updates. Separate targeted state is safest when managing a complete mirror. Re-fetching at the same source name/order with changed bytes is rejected; use a new order/name.

## Ledger and recovery

`ledger.sqlite` stores source SHA-256, source ordering, processing status, completion UTC time, current metadata, tombstones, and enrichment states. Raw-private files are downloaded with `.part` suffix, bounded memory, request timeouts, retry/backoff, and atomic rename after checksum validation. Partial downloads restart from byte zero: processing resumes at source level, not HTTP byte offsets. Local ingestion computes SHA-256, but only a supplied official MD5 establishes upstream checksum agreement. Download failures retain `.part`; no completion is recorded. Download status enters the ledger once a complete local file is available; in-progress downloads are represented by `.part`, not a separate ledger row.

The advisory flock covers the entire CLI operation (macOS/Linux). A second run fails promptly. Locks release automatically on process death. SQLite applies an entire source in one transaction. `downloaded → parsed → validated → exported` advances only after parsing and successful SHACL validation of every output partition. Parsed/validated are transaction-local states; failures leave `failed`, no partial record replacements. Replaying a failed/downloaded source is safe. Completed source identity is immutable; duplicate sources are skipped.

Export writes a unique immutable generation under `snapshots/`, including a provenance manifest and deletion list. `CURRENT` switches by atomic rename only after all files validate. It is written before the DB completion commit; a crash in this narrow interval can leave the pointer ahead of committed ledger state. **Consumers must take the same run.lock and confirm every manifest source is exported in the ledger before loading.** If not, rerun the failed/downloaded source; it regenerates output. Old generations remain usable. Atomic rename provides process-crash safety, not an fsync guarantee against power loss; use filesystem/database backups. The CLI never marks `loaded` because no graph store writer is implemented.

## Loading and deletions

Turtle is a complete current snapshot, partitioned at 500 articles. Stage all `part-*.ttl` into a new dedicated PubMed named graph, validate/count it, then switch the graph application's active graph selection. Remove the previous graph only after verification. Never append successive full snapshots: that would retain obsolete triples. `deletions.json` lists deleted PMIDs; external loaders may use it to clear article graphs, but must also remove replaced article-owned assertions. Snapshot graph replacement handles both correctly. Do not erase FDA graphs or keep synthetic adapter data in a production graph. Enrichment and derived links must be refreshed after article replacement; this pipeline clears prior enrichment on replacement.

## Scheduling

After establishing a reviewed inventory, cron can invoke one command:

```cron
15 3 * * * cd /srv/pubmed-kg && /srv/pubmed-kg/.venv/bin/pmkg --state /srv/pubmed-state update --manifest /srv/pubmed-kg/manifest.json >> /srv/pubmed-state/operations.log 2>&1
```

The complete baseline inventory is operator-approved; with `discover_updates` enabled the cron command discovers, checksums and ingests new sequential daily files itself. Otherwise it processes manually appended entries. Use UTC explicitly at the scheduler if required by deployment. Provision secret environment variables outside version control. Check exit codes and ledger failures; CLI errors are deliberately redacted. A failed provider request must never be treated as zero results. `enrich` retries pending/failed/unavailable records; complete records are skipped unless `--refresh`. Limits prevent accidental mass annotation. Metadata remains usable when enrichment fails.

## Raw text and rights

`raw-private/` may contain copyrighted abstracts in downloaded XML. Restrict access, set retention according to institutional policy, and never distribute these caches. Abstracts are transiently scanned for explicit identifiers; neither abstract content, body text, mention snippets nor PubTator passage text is persisted in metadata/RDF/logs. Titles and structured bibliographic terms are retained as requested. Live verification raw responses stay in the parent work area, outside deliverables. PubTator bulk exports may contain text and require the same handling. Consider deleting private raw files after audit/retention requirements are met.

PMC full text remains disabled. PMCID is only an identifier. Any future full-text module must first verify membership in the OA subset, inspect each article's actual license/reuse restrictions, use current authorized retrieval methods, and keep text out of RDF. NCBI disclaimer: https://www.ncbi.nlm.nih.gov/About/disclaimer.html .

## Actual MDKG integration requirements

Supply read-only SPARQL endpoint URL, authentication method if any, named graph IRIs, namespace declarations, device resource class/IRI conventions, exact 510(k)/PMA/product-code identifier predicates and literal normalization, trial-device, device-recall and category predicates, plus example resources. Inspect schema and bounded examples before adapting the queries. `inspect-mdkg` has a client timeout; its aggregate predicate inventory may still be expensive on a billion-triple endpoint—prefer endpoint-provided schema or a small configured graph. It performs no writes. Authentication is not yet implemented.

The local `match` command compares explicit detected identifier literals with configured predicates in an operator-supplied graph extract. Results are candidates with a qualitative classification. Product codes identify categories, not unique devices; do not promote a product-code match to identity. Name-only candidates require independent evidence and are not generated automatically. Verification is an external human review action: preserve reviewer, source resource, date, evidence method and actual matching identifier; no automatic `owl:sameAs`. Match output is a separate review artifact, not silently included in later metadata exports. A reviewed assertion registry and production loader are future integration work.
