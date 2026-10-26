# Run design and validation

## Check current source rules

Read the current rules before a large job. Quotas, access requirements, and snapshot availability can change.

- [Wikimedia robot policy](https://wikitech.wikimedia.org/wiki/Robot_policy): bulk access choices, robot rules, identification, and cached HTML access.
- [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy): client identification and operator contact.
- [API etiquette](https://www.mediawiki.org/wiki/API:Etiquette): serial requests, batching, compression, and noninteractive load control.
- [Rate limits](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits): current limits and response handling. Do not bake a quota into the skill.
- [Categorymembers](https://www.mediawiki.org/wiki/API:Categorymembers): traversal and continuation semantics.
- [Revisions](https://www.mediawiki.org/wiki/API:Revisions): revision metadata and ownership queries.
- [Wikimedia dumps](https://dumps.wikimedia.org/) and [Enterprise snapshots](https://enterprise.wikimedia.com/docs/snapshot/): check format, date, volume, access terms, and cost before use.

A cached HTML route can reduce server rendering work. It does not exempt the client from robot rules or throttling. Check identity requirements for the intended volume. If access is denied, inspect the cause; do not imitate a browser to evade it.

## Storage and stage boundaries

Use a dedicated run directory. Keep datasets, contacts, logs, and downloaded HTML out of the skill repository. An article ledger can extend the discovery database or use a separate database with stable original IDs:

| Record | Required contents |
| --- | --- |
| Run | Scope, metric version, code version, start/end dates, operator configuration |
| Category | Title, continuation object, completion state, checked time, last error |
| Edge | Parent category, member namespace, original page ID, observed title and time |
| Original page | ID, observed titles, retrieval state, attempt count, next retry, terminal reason |
| Observation | Original ID, canonical ID, revision ID, fetched URL, checked time, evidence hash/path, metrics |
| Classification | Observation key, decision, reason, evidence source/revision, classifier version |
| Request state | Shared cooldown, per-route pacing, failure count, last response status |
| Exception | Original ID, error type, evidence, whether retryable, resolution |

Make updates transactional. Commit discovered members and the next cursor together. A repeated response must not duplicate candidates. Never replace a valid observation with an error or zero placeholder. Preserve observations as history where possible.

The discovery helper uses a serial request stream, an exclusive workspace lock, and a durable deadline. It stops on access errors, API warnings, and unexpected response shapes. Its delay can increase after throttling and is not reduced automatically. Resume does not reset it.

`discovery_complete` describes category traversal only. Keep retrieval, identity, classification, and export status separate. Use a consistent database snapshot for final reconciliation.

## Full category traversal

Verify each root's exact title and category namespace before starting. Check missing pages and category redirects explicitly. A misspelled or redirected root can return an empty member list; do not treat that alone as proof of an empty scope. The helper expects already verified root titles and follows actual category membership edges.

For each root, query `list=categorymembers`, `cmnamespace=0|14`, and `cmlimit=max`. Preserve the full continuation object. An empty member list can still have a continuation token; only the absence of continuation ends that category.

Maintain a visited-category set to terminate cycles. Keep every observed edge, including edges to categories already visited. Deduplicate candidate articles by original page ID. Do not impose a depth cap or stop after a convenient number of results unless requested.

Recompute root reachability from the stored graph. Keeping only the first parent loses provenance when a category is reachable through several roots. A shortest path per root is a compact export; the database retains the full graph.

Do not replace traversal with `deepcat` search when the contract requires an exhaustive observed graph. Search can help inspect a suspected omission, but cannot prove traversal completion.

Category membership changes during collection. Record discovery dates and describe the result as the observed graph. An exact historical snapshot requires suitable dated source data; live requests over several days cannot establish it.

## Article retrieval and migration

Build the article worker around the ledger. Use canonical `/wiki/Title` requests without unnecessary query strings when the selected policy and metric allow cached HTML. URL-encode titles correctly. Prevent hidden redirect chains from making unpaced requests; handle redirects through the scheduler or use a targeted API fallback.

Before adopting another renderer or endpoint:

1. Select examples around the threshold, large and small articles, grouped notes, nested references, non-ASCII titles, redirects, and disputed classifications.
2. Save both responses and their retrieval metadata.
3. Compare at the same revision. A changed revision is an inconclusive comparison, not a parser defect or a pass.
4. Compare exact reference ID sets by group where applicable, plus every identity and classification field used downstream.
5. Test malformed, truncated, challenge, missing, and redirect responses. They must remain unresolved.
6. Switch after the required fields agree. Preserve prior rows, request deadlines, and pending retries.

Use one shared server cooldown across routes. Per-route delays may differ, but no route may run before a shared `Retry-After` deadline. Keep fallback requests in the scheduler. A 429 must not trigger an immediate request to another endpoint for the same data.

Check the JSON body for API errors even when HTTP status is 200. Handle `maxlag`, `ratelimited`, and transient service failures with persistent backoff. Parse numeric and HTTP-date `Retry-After` values. Treat permanent access failures as an issue to diagnose, not missing articles.

## Canonical identity

An alias page ID can appear beside a target title and revision ID in a parse response. Do not treat its `pageid` as conclusive canonical identity.

Batch revision ownership queries within current API limits:

```text
action=query&prop=revisions&revids=REVISION_IDS&rvprop=ids&format=json&formatversion=2
```

Validate exact requested-revision coverage, namespace, positive page IDs, and absence of suppressed or conflicting records. `canonical.revision_owners` performs these checks on returned JSON. Missing revisions remain unresolved until accounted for.

Keep aliases in the audit ledger. Select the latest successful observation per canonical ID using the recorded observation time and a deterministic tie rule. Apply the threshold to that selection. Otherwise an older above-threshold alias can survive after a newer observation falls below the threshold.

Merge provenance from all aliases into the export. Do not merge measurements or add reference counts. If the contract asks for one row per real-world entity, define a separate merge rule; canonical page deduplication alone does not establish entity equivalence.

When an original page disappears, inspect its ID and relevant logs. A recreated title or replacement redirect does not establish continuity with the original article. Record the terminal exception without inventing its missing measurement.

## Measurements and eligibility

Define the metric before writing code. For references, distinguish rendered list entries, inline citation markers, named definitions, unique publications, and external domains. Template expansion and Lua mean raw wikitext reference tags may not match rendered counts.

For the supplied parser, inspect lists within article content. Exclude grouped explanatory notes when the contract requires ungrouped numbered references. Retain nested subreference entries in their inherited group. A missing recognized list with visible citation markers is a parse problem, not proof of zero references.

Save eligibility separately from the numeric filter. A music subtree can contain albums, songs, lists, television pages, or organizations. Audit drift at several depths and inspect included and excluded cases. Category names, short descriptions, infoboxes, and Wikidata claims can disagree.

Verify Wikidata identifier labels before coding rules. Use claims as evidence, with explicit conflict handling. Save a review decision when evidence cannot resolve a candidate. Prioritize unresolved cases that could enter the output, but retain every candidate's state.

Do not silently add unsupported attributes. For a language split, track language needs recording or catalogue evidence. Nationality, article language, and spoken languages do not establish primary track language. Preserve an unknown or multilingual bucket, evidence links, and a confidence or verification field. Label estimates if the user accepts inference.

## Performance and operation

Profile request time, wait time, parsing, and database/export time separately. Use query plans and indexes for repeated joins. Compare exports before and after a local optimization using a database copy. Faster local work can improve throughput without increasing request load.

Estimate duration from successful completions over recent windows. Report remaining categories, articles, retries, and reviews separately. Include sleep, interruptions, and backoff in wall-clock estimates. A small probe gives latency evidence, not an end-to-end completion guarantee.

A supervisor must check that no worker is alive before restart. Keep supervisor and worker locks distinct. Use bounded execution segments to reload repairs safely. Never delete a database or ignore errors to make a run appear complete.

## Final audit and exports

Check against the same database snapshot:

1. Every observed reachable category is complete with no unfinished cursor.
2. Discovered original IDs equal successful original IDs plus documented terminal exceptions. No pending or retryable cases remain.
3. Every observation used for output has a verified canonical ID and revision ID.
4. All candidates that can meet the threshold have resolved eligibility.
5. Canonical selection is deterministic and precedes filtering. Exported IDs are unique.
6. Every exported row satisfies the semantic rule and threshold.
7. Classification subtotals equal the exported total; all root provenance remains available.
8. Evidence exists and hashes match for output and reviewed cases. URLs, revisions, and dates agree with the ledger.
9. CSV reload preserves original values, including long IDs, Unicode, leading zeros, and timestamp precision.

Export a main CSV and audit summary. Include source URL, revision URL/ID, checked time, metric, eligibility reason, canonical ID, and root provenance. Supply unavailable records separately when relevant. Do not label unresolved eligibility as an exclusion.

For partitioned exports, put each master row in exactly one file unless overlap is requested. Validate the union, uniqueness, and every preserved field against the master. Explain the partition rule and uncertain buckets.

State collection dates and metric definition at delivery. Distinguish a completed retrievable set with documented exceptions from an unqualified complete list. Explain that category coverage is limited to the graph observed during collection.
