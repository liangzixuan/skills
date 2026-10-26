---
name: wikipedia-category-census
description: Crawl Wikipedia category trees, retrieve article evidence, apply measurable criteria, and export a deduplicated census with provenance. Use for complete lists from category roots, resumable Wikipedia crawls, reference-count filters, redirect identity checks, and crawl repair or validation. Do not substitute search results for exhaustive traversal or use this workflow for a simple article lookup.
---

# Wikipedia category census

Build a reproducible dataset from the full observed category graph. Keep discovery, retrieval, measurement, classification, and export as separate stages. Preserve checkpoints and evidence when changing implementation.

Read [workflow.md](references/workflow.md) before starting or repairing a run. Read [lessons.md](references/lessons.md) when choosing a retrieval method, estimating duration, or adding inferred attributes.

## Define the contract

Record these choices in a run manifest before retrieval:

- Wikipedia language edition and exact category roots.
- Recursive scope and namespaces. The discovery helper includes articles (0) and subcategories (14).
- Eligibility rule, metric definition, threshold, and boundary behavior.
- Output grain: one row per canonical article, or another explicitly requested entity.
- Evidence required for inclusion, exclusion, and unresolved cases.
- Retrieval dates, output fields, unavailable-page policy, and completion gates.

Use conversation context to resolve these choices. Ask only for missing information that changes the result. Category membership supplies candidates; it does not prove semantic eligibility.

## Choose access and pacing

Check current Wikimedia robot, identification, and API policies linked in the workflow. For large jobs, check whether a suitable dump or snapshot supplies the metric before making many live requests. Raw wikitext does not necessarily reproduce rendered references.

Use the Action API for category discovery and batched metadata. For rendered article evidence, evaluate canonical cached HTML without query parameters. Browser automation adds overhead and is not the default bulk retrieval method.

Identify the client with a real operator contact. Reuse a contact already supplied for this purpose; otherwise ask while doing local work. Do not copy contacts into a public skill or fixture. Respect robots rules, server delays, `Retry-After`, API errors returned with HTTP 200, and `maxlag` for noninteractive API work. Never rotate endpoints, identities, or workers to bypass a limit.

Before changing retrieval methods, compare representative saved articles at the same revision. Check all filter fields, reference IDs by group where relevant, identity, categories, and Wikidata IDs. Include redirects and malformed markup in failure tests. Keep a delayed API fallback for unsupported website documents.

## Discover and resume

The helpers use Python 3.10+ and the standard library on macOS/Linux. Run paths below from the repository root. Replace the workspace, roots, and contact with the actual run values:

```sh
python3 wikipedia-category-census/scripts/discover_categories.py \
  --workdir /path/to/census-run \
  --root 'Category:First root' \
  --root 'Category:Second root' \
  --contact 'https://example.org/operator-contact' \
  --delay 5 --run-seconds 300
```

The contact URL is a placeholder. The delay is a starting interval, not a guaranteed server allowance. Set `--api` for another Wikipedia language edition.

Rerun the same command to resume. `--run-seconds` sets a checkpoint segment; it does not cap categories, depth, or results. A `STOP` file in the run workspace stops the worker. Honor an intentional stop; remove it only when the user resumes work.

The helper writes:

- `discovery.sqlite3`: full observed edges, original article IDs, category cursors, and persistent pacing.
- `discovery-status.json` and `events.jsonl`: stage status and counts.
- `candidates.jsonl`: candidates with a shortest observed category path from each reachable root, written after discovery completes.

This helper only discovers candidates. Its `dataset_complete` value stays false. Adapt the article worker and classifier to the contract below; do not deliver discovery output as the finished census.

## Retrieve, measure, and classify

1. Retrieve pending original page IDs through one coordinated scheduler. Save compressed HTML, timestamps, revision IDs, source URLs, and hashes.
2. Verify identity before using measurements. Keep original and canonical IDs separately. For parsed fragments and redirects, resolve revision ownership with the API. Use `revision_owners` in [canonical.py](scripts/canonical.py) to reject partial or conflicting responses.
3. Implement the requested metric from rendered evidence or another validated source. Treat unsupported markup as unresolved, never as zero.
4. Apply semantic eligibility separately. Save the reason and evidence for each decision. Resolve conflicts through saved HTML and targeted Wikipedia/Wikidata reads.
5. Select the latest successful, identity-verified observation per canonical article **before** applying the threshold. `latest_canonical` in [canonical.py](scripts/canonical.py) supplies this selection. Merge provenance across aliases separately.

For “distinct ungrouped numbered reference-list entries,” use the offline parser:

```sh
python3 wikipedia-category-census/scripts/inspect_article.py saved.html.gz --expected-pageid 123
python3 wikipedia-category-census/scripts/inspect_article.py saved-parse-fragment.html --fragment
```

The parser supports the tested Cite list structure and website `RLCONF` metadata. It counts distinct `cite_note-*` entry IDs in ungrouped reference lists. Reused named references count once. Grouped notes are excluded. Nested subreferences inherit their parent list group. Separate entries for the same publication remain separate. Fragment mode requires a separate revision-owner check; it cannot certify identity. New renderers require validation and possibly an adapter.

## Operate and finish

Use one worker lock and, if needed, one supervisor lock. A supervisor must resume checkpoints, respect `STOP`, and surface permanent access or protocol failures. Do not launch a second worker because progress is slow. Schedule chat follow-ups only when requested. Preserve the user's notification preference and stop the follow-up after delivery.

Estimate duration from recent successful throughput, remaining work by stage, and observed backoff. State a range and its assumptions. Profile local parsing and export before changing request pacing.

Before delivery, verify:

- All observed reachable categories are exhausted, including every continuation page.
- Every discovered original ID has a successful observation or a documented terminal exception.
- No retryable failures, unknown identities, or unresolved classifications can change the result.
- Canonical IDs are unique; latest-observation selection precedes filtering.
- Counts reconcile with the ledger; source and revision links, timestamps, and root provenance are present.
- CSV round trips preserve IDs, Unicode, original fields, and timestamp precision.

Deliver the CSV, counts, date range, metric definition, and material scope caveats. If pages became unavailable, distinguish completion of the retrievable observed set from an unqualified census of every candidate. Never label a partial export complete.

Run the offline regression suite after changing helpers:

```sh
python3 -m unittest discover -s wikipedia-category-census/scripts -p 'test_*.py'
```
