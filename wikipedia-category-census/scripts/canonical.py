"""Validate revision ownership and choose a canonical observation before filtering."""
from datetime import datetime


def revision_owners(response, requested):
    """Read action=query&prop=revisions&revids=...; reject missing identities."""
    requested = set(requested)
    if not requested or any(type(r) is not int or r <= 0 for r in requested):
        raise ValueError('Supply positive revision IDs')
    if response.get('error') or response.get('warnings'):
        raise ValueError('Resolve API errors and warnings before accepting identity')
    pages = response.get('query', {}).get('pages')
    if not isinstance(pages, list) or response.get('continue'):
        raise ValueError('Incomplete revision response')
    owners = {}
    for page in pages:
        pid = page.get('pageid')
        if type(pid) is not int or pid <= 0 or page.get('ns') != 0 or 'missing' in page:
            raise ValueError('Revision does not resolve to an available mainspace article')
        for revision in page.get('revisions', []):
            rid = revision.get('revid')
            if rid not in requested or ('texthidden' in revision) or ('suppressed' in revision):
                raise ValueError('Unexpected or suppressed revision')
            if rid in owners and owners[rid] != pid:
                raise ValueError('Conflicting revision owners')
            owners[rid] = pid
    if set(owners) != requested:
        raise ValueError('Unresolved revision IDs: ' + str(sorted(requested - set(owners))))
    return owners


def latest_canonical(rows):
    """Select among successfully retrieved, identity-verified observations.

Call before applying any content threshold. This function does not certify that
all discovered pages were retrieved or that manual reviews were resolved.
    """
    selected, keys = {}, {}
    for row in rows:
        for key in ('original_pageid', 'canonical_pageid', 'revision_id'):
            if type(row.get(key)) is not int or row[key] <= 0:
                raise ValueError('Missing positive identity field: ' + key)
        checked = datetime.fromisoformat(row['checked_at'].replace('Z', '+00:00'))
        if checked.tzinfo is None:
            raise ValueError('Retrieval time must include a timezone')
        pid = row['canonical_pageid']
        key = (checked, row['original_pageid'])
        if pid not in keys or key > keys[pid]:
            selected[pid], keys[pid] = row, key
        elif key == keys[pid] and row != selected[pid]:
            raise ValueError('Conflicting observations with the same selection key')
    return [selected[pid] for pid in sorted(selected)]
