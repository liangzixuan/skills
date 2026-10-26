# Skills

Reusable agent skills with supporting scripts and references.

## Wikipedia category census

[wikipedia-category-census](wikipedia-category-census/SKILL.md) covers recursive category discovery, article retrieval, measurable filters, canonical identity checks, and validated CSV exports.

It includes a resumable discovery script, an offline reference-list parser, canonical identity helpers, and regression tests. Article retrieval and semantic classification must be adapted to each request. The [case study](wikipedia-category-census/references/lessons.md) records lessons from a 50,988-candidate music census.

Install the `wikipedia-category-census` directory through your agent's skill installer, or place that directory in its skills location. Invoke it with `$wikipedia-category-census` and specify the category roots and criteria.

Example request:

> Use $wikipedia-category-census to crawl all subcategories of Category:21st-century singers, select singer articles with more than 100 ungrouped numbered reference-list entries, and export a validated CSV with revision links and category provenance.

The helper scripts require Python 3.10+ on macOS/Linux and use the standard library. Run offline tests from the repository root:

```sh
python3 -m unittest discover -s wikipedia-category-census/scripts -p 'test_*.py'
```
