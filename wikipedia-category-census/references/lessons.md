# Lessons from the music census

This case study records a completed run on October 20–25, 2026. Its timings describe that run; they are not service guarantees. The skill contains reusable code and synthetic tests, not the original dataset or operator configuration.

## Scope and result

The roots were [21st-century singers](https://en.wikipedia.org/wiki/Category:21st-century_singers) and [musical groups established in the 21st century](https://en.wikipedia.org/wiki/Category:Musical_groups_established_in_the_21st_century). The filter required eligible singers or groups with more than 100 distinct entries in ungrouped numbered reference lists.

| Measure | Result |
| --- | ---: |
| Observed categories | 743 |
| Discovered original page IDs | 50,988 |
| Retrievable original page IDs | 50,984 |
| Distinct canonical articles | 50,370 |
| Unavailable original pages, documented separately | 4 |
| Matching singers | 1,530 |
| Matching groups | 358 |
| Final matching rows | 1,888 |

Discovery took about two hours. Article collection, retries, identity correction, and final checks continued over several days. The result covered the retrievable observed set, with four unavailable pages accounted for. It was not a single-time snapshot of Wikipedia.

## What improved retrieval

Per-article API parsing became the main bottleneck. Early estimates extrapolated a high backoff interval over the whole queue and were too crude to support a precise finish date.

A two-page probe showed that cached HTML might work. A broader comparison then checked 20 articles at matching revisions. Reference IDs by group, categories, and Wikidata IDs agreed with API results. Median HTTP time was 0.460 seconds in that comparison. The worker switched methods while preserving 8,206 completed rows and the existing request deadline.

The run used separate normal pacing for HTML and API requests, with a shared cooldown. Unsupported HTML went to the API fallback. This reduced expensive parsing requests without bypassing limits. A descriptive client header with a real operator contact was added. The earlier missing contact was not proven to cause throttling.

Browser automation was unnecessary for ordinary cached HTML. Dumps remained a possible alternative, but raw wikitext could not directly reproduce the rendered metric. Snapshot format, volume, freshness, and access terms needed separate checks.

## Correctness repairs

| Problem | Reusable repair |
| --- | --- |
| Empty category batches can still continue | Stop only when continuation is absent. Commit members and cursor together. |
| Cycles and shared subcategories | Keep visited categories and all edges; compute provenance per root. |
| API parse IDs can identify an alias | Resolve the owner of each revision and retain original IDs separately. |
| An older alias passed the threshold | Select the latest canonical observation before filtering. |
| Nested reference entries | Inherit the containing list group; deduplicate by entry ID. |
| Category drift | Classify the article subject separately from traversal membership. |
| Conflicting Wikidata claims | Verify labels and review Wikipedia evidence. |
| Deleted pages or reused titles | Keep original-ID exceptions; do not assign replacement content to them. |

A mistyped Wikidata identifier illustrated the need for label checks: `Q2252262` is rapper; `Q225226` is a war medal. Human and group claims also needed review in disputed cases. A category path or single claim could not settle every classification.

## Local work also affected speed

An export query repeatedly joined on canonical identity without a matching index. Adding an index for `coalesce(canonical_pageid,pageid)` reduced measured export time from 6.409 seconds to 0.140 seconds. The comparison used a database copy and verified byte-identical output. Profiling local work avoided an unnecessary increase in request rate.

## Enrichment needs separate evidence

A later request split the 1,888 rows by primary track language. The original census had no language field. The split was a best-effort classification, not a verified census of every artist's catalogue. Future runs should record evidence, confidence, and unknown or multilingual cases explicitly. Nationality and spoken-language claims are insufficient substitutes for track language.

The split also exposed a serialization issue: a spreadsheet engine changed timestamp formatting and precision. Restoring original field values and comparing every row against the master preserved the source data. Matching partition counts alone would not have caught it.

## Practices to repeat

Keep a durable ledger, one coordinated worker, and evidence for decisions that can affect the result. Validate a new retrieval route before migrating. Measure throughput over actual operating windows. Finish with identity, exception, classification, and export reconciliation, then report collection dates and scope limits alongside the file.
