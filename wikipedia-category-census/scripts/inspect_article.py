#!/usr/bin/env python3
"""Inspect saved MediaWiki HTML without network access or JavaScript execution.

Supports the tested legacy Cite list markup and RLCONF website metadata.
Unrecognized structure requires inspection; it is not a zero reference count.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
from html.parser import HTMLParser


class WebsiteUnsupported(ValueError):
    pass


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lists = []
        self.groups = {}
        self.all_ids = set()
        self.citation_markers = 0
        self.short_description = []
        self.short_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'sup' and ('reference' in attrs.get('class', '').split() or attrs.get('id', '').startswith('cite_ref')):
            self.citation_markers += 1
        if tag == 'ol':
            classes = attrs.get('class', '').split()
            if 'references' in classes:
                group = attrs.get('data-mw-group', '')
            elif 'mw-subreference-list' in classes and self.lists:
                group = self.lists[-1]
            else:
                group = None
            self.lists.append(group)
        if tag == 'li' and attrs.get('id', '').startswith('cite_note'):
            self.all_ids.add(attrs['id'])
            if self.lists and self.lists[-1] is not None:
                self.groups.setdefault(self.lists[-1], set()).add(attrs['id'])
        if tag == 'div':
            if self.short_depth:
                self.short_depth += 1
            elif 'shortdescription' in attrs.get('class', '').split():
                self.short_depth = 1

    def handle_endtag(self, tag):
        if tag == 'ol' and self.lists:
            self.lists.pop()
        if tag == 'div' and self.short_depth:
            self.short_depth -= 1

    def handle_data(self, data):
        if self.short_depth:
            self.short_description.append(data)

    @property
    def count(self):
        return len(self.groups.get('', set()))

    @property
    def grouped(self):
        return sum(len(ids) for name, ids in self.groups.items() if name)


class WebsiteDocument(References):
    """Read article metadata as JSON and count only the article content."""
    def __init__(self):
        super().__init__()
        self.config = None
        self.script = None
        self.content_depth = 0
        self.found_content = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'script':
            self.script = []
        if tag == 'div':
            if self.content_depth:
                self.content_depth += 1
            elif values.get('id') == 'mw-content-text':
                self.content_depth = 1
                self.found_content = True
        if self.content_depth:
            super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            text = ''.join(self.script)
            marker = re.search(r'\bRLCONF\s*=\s*(?=\{)', text)
            if marker:
                try:
                    self.config, _ = json.JSONDecoder().raw_decode(text[marker.end():])
                except ValueError as exc:
                    raise WebsiteUnsupported('Invalid website metadata JSON') from exc
            self.script = None
        if self.content_depth:
            super().handle_endtag(tag)
            if tag == 'div':
                self.content_depth -= 1

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if self.content_depth:
            super().handle_data(data)

    def result(self, html, expected_pageid):
        self.feed(html)
        self.close()
        c = self.config
        if not isinstance(c, dict) or not self.found_content or self.content_depth:
            raise WebsiteUnsupported('Missing or incomplete article content or metadata')
        if (c.get('wgNamespaceNumber') != 0 or c.get('wgIsArticle') is not True
                or c.get('wgIsRedirect') is not False or c.get('wgAction') != 'view'):
            raise WebsiteUnsupported('Website response is not a mainspace article view')
        if c.get('wgArticleId') != expected_pageid:
            raise WebsiteUnsupported('Page identity changed; resolve by original page ID')
        if type(c.get('wgRevisionId')) is not int or c['wgRevisionId'] <= 0:
            raise WebsiteUnsupported('Missing article revision ID')
        if not isinstance(c.get('wgPageName'), str) or not c['wgPageName']:
            raise WebsiteUnsupported('Missing canonical page name')
        if not isinstance(c.get('wgCategories'), list) or not all(isinstance(x, str) for x in c['wgCategories']):
            raise WebsiteUnsupported('Missing article categories')
        listed = set().union(*self.groups.values()) if self.groups else set()
        if listed != self.all_ids or (self.citation_markers and not self.all_ids):
            raise WebsiteUnsupported('Reference IDs outside recognized lists')
        qid = c.get('wgWikibaseItemId')
        if qid is not None and (not isinstance(qid, str) or not re.fullmatch(r'Q[1-9][0-9]*', qid)):
            raise WebsiteUnsupported('Invalid Wikidata ID')
        return {'text': html, 'pageid': c['wgArticleId'], 'title': c['wgPageName'].replace('_', ' '),
                'revid': c['wgRevisionId'], 'categories': [{'category': x} for x in c['wgCategories']],
                'properties': {'wikibase_item': qid}}


def inspect_html(html, expected_pageid=None, fragment=False):
    if fragment:
        parser = References()
        parser.feed(html)
        parser.close()
        listed = set().union(*parser.groups.values()) if parser.groups else set()
        if parser.lists or listed != parser.all_ids or (parser.citation_markers and not listed):
            raise WebsiteUnsupported('Unrecognized or incomplete reference list structure')
        identity = {}
    else:
        if expected_pageid is None:
            raise ValueError('Website mode requires the discovered original page ID')
        parser = WebsiteDocument()
        data = parser.result(html, expected_pageid)
        if parser.lists:
            raise WebsiteUnsupported('Unclosed reference lists')
        identity = {key: data[key] for key in ('pageid', 'title', 'revid', 'categories', 'properties')}
    return {
        **identity,
        'reference_entries': parser.count,
        'grouped_notes_excluded': parser.grouped,
        'reference_ids_by_group': {k: sorted(v) for k, v in parser.groups.items()},
        'html_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest(),
        'identity_status': 'requires_revision_query' if fragment else 'website_metadata_checked',
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('html', type=Path, help='Saved UTF-8 HTML or .html.gz file')
    ap.add_argument('--expected-pageid', type=int)
    ap.add_argument('--fragment', action='store_true', help='Explicitly parse an API HTML fragment; supplies no identity')
    args = ap.parse_args()
    data = args.html.read_bytes()
    if args.html.suffix == '.gz':
        data = gzip.decompress(data)
    print(json.dumps(inspect_html(data.decode('utf-8'), args.expected_pageid, args.fragment), ensure_ascii=False))


if __name__ == '__main__':
    main()
