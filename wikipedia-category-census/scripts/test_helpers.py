"""Offline regression tests; no Wikipedia requests are made."""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch
import discover_categories as crawl
from inspect_article import inspect_html, WebsiteUnsupported


def document(pageid=1, **changes):
    config = {'wgNamespaceNumber': 0, 'wgIsArticle': True, 'wgIsRedirect': False,
              'wgAction': 'view', 'wgArticleId': pageid, 'wgRevisionId': 99,
              'wgPageName': 'Example_Artist', 'wgCategories': ['Test_category'],
              'wgWikibaseItemId': 'Q1'}
    config.update(changes)
    return ('<script>RLCONF=' + json.dumps(config) + ';RLSTATE={};</script>'
            '<ol class="references"><li id="cite_note-outside"></li></ol>'
            '<div id="mw-content-text"><div class="mw-parser-output">'
            '<ol class="references"><li id="cite_note-source"></li></ol></div></div>')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.con = crawl.connect(self.folder, 'https://en.wikipedia.org/w/api.php', ['Category:A', 'Category:B'], 5)

    def tearDown(self):
        self.con.close(); self.tmp.cleanup()

    def test_empty_continuation_page_resumes_after_restart(self):
        cursor = {'continue': '-||', 'cmcontinue': 'next'}
        crawl.apply_batch(self.con, 'Category:A', {'query': {'categorymembers': []}, 'continue': cursor})
        self.con.close()
        self.con = crawl.connect(self.folder, 'https://en.wikipedia.org/w/api.php', ['Category:B', 'Category:A'], 5)
        row = self.con.execute("SELECT * FROM categories WHERE title='Category:A'").fetchone()
        self.assertEqual(json.loads(row['cursor']), cursor)
        self.assertEqual(row['done'], 0)

    def test_cycle_dedup_and_both_root_paths(self):
        def batch(cat, members):
            crawl.apply_batch(self.con, cat, {'query': {'categorymembers': members}})
        child = {'pageid': 10, 'ns': 14, 'title': 'Category:C'}
        page = {'pageid': 1, 'ns': 0, 'title': 'Artist'}
        batch('Category:A', [child, page, page])
        batch('Category:B', [child])
        batch('Category:C', [{'pageid': 11, 'ns': 14, 'title': 'Category:A'}, page])
        self.assertEqual(self.con.execute('SELECT count(*) FROM pages').fetchone()[0], 1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM categories').fetchone()[0], 3)
        self.assertEqual(crawl.provenance(self.con)[1], {'Category:A': ['Category:A'], 'Category:B': ['Category:B', 'Category:C']})

    def test_missing_query_is_not_empty_success(self):
        with self.assertRaises(ValueError):
            crawl.apply_batch(self.con, 'Category:A', {})
        self.assertEqual(self.con.execute("SELECT done FROM categories WHERE title='Category:A'").fetchone()[0], 0)

    def test_bad_member_batch_is_atomic(self):
        data = {'query': {'categorymembers': [{'pageid': 1, 'ns': 0, 'title': 'Good'}, {'pageid': -2, 'ns': 0, 'title': 'Bad'}]}}
        with self.assertRaises(ValueError):
            crawl.apply_batch(self.con, 'Category:A', data)
        self.assertEqual(self.con.execute('SELECT count(*) FROM members').fetchone()[0], 0)

    def test_replayed_batch_does_not_duplicate_candidates(self):
        data = {'query': {'categorymembers': [{'pageid': 1, 'ns': 0, 'title': 'Artist'}]}}
        crawl.apply_batch(self.con, 'Category:A', data)
        crawl.apply_batch(self.con, 'Category:A', data)
        self.assertEqual(self.con.execute('SELECT count(*) FROM members').fetchone()[0], 1)

    def test_scope_change_rejected(self):
        with self.assertRaises(ValueError):
            crawl.connect(self.folder, 'https://fr.wikipedia.org/w/api.php', ['Category:A'], 5)

    def test_scope_is_not_capped_by_segment(self):
        status = crawl.report(self.con, self.folder, 'segment_finished')
        self.assertFalse(status['discovery_complete'])
        self.assertFalse(status['dataset_complete'])
        self.assertEqual(status['categories_total'], 2)

    def test_bad_continuation_rejected(self):
        for cursor in ({'cmcontinue': ''}, {'cmcontinue': 'x', 'action': 'edit'}, {'continue': '-||'}):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                crawl.apply_batch(self.con, 'Category:A', {'query': {'categorymembers': []}, 'continue': cursor})

    def test_repeated_continuation_stops_loop(self):
        data = {'query': {'categorymembers': []}, 'continue': {'cmcontinue': 'same'}}
        crawl.apply_batch(self.con, 'Category:A', data)
        with self.assertRaises(ValueError):
            crawl.apply_batch(self.con, 'Category:A', data)

    def test_second_worker_rejected(self):
        with crawl.worker_lock(self.folder):
            with self.assertRaises(RuntimeError):
                with crawl.worker_lock(self.folder):
                    self.fail('Second worker acquired the lock')

    def test_retry_after_seconds_and_date(self):
        self.assertEqual(crawl.retry_after('600', 0), 600)
        self.assertEqual(crawl.retry_after('Thu, 01 Jan 1970 00:10:00 GMT', 0), 600)
        self.assertEqual(crawl.retry_after('invalid'), 0)

    def test_429_deadline_survives_restart(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('url', 429, 'slow', {'Retry-After': '600'}, io.BytesIO())
        client = crawl.APIClient(self.con, 'https://en.wikipedia.org/w/api.php', 'https://example.org/contact', opener)
        with patch.object(crawl.time, 'time', return_value=1000), self.assertRaises(crawl.Deferred):
            client.get({'action': 'query'})
        self.con.close()
        self.con = crawl.connect(self.folder, 'https://en.wikipedia.org/w/api.php', ['Category:A', 'Category:B'], 1)
        self.assertGreaterEqual(crawl.getmeta(self.con, 'next_request'), 1600)
        self.assertEqual(crawl.getmeta(self.con, 'api_delay'), 10)
        blocked = Mock()
        client = crawl.APIClient(self.con, 'https://en.wikipedia.org/w/api.php', 'https://example.org/contact', blocked)
        with patch.object(crawl.time, 'time', return_value=1100), self.assertRaises(crawl.Deferred):
            client.get({'action': 'query'})
        blocked.open.assert_not_called()

    def test_maxlag_http_200_obeys_retry_after(self):
        response = Mock()
        response.headers = {'Retry-After': '900'}
        response.read.return_value = b'{"error":{"code":"maxlag","lag":10}}'
        opener = Mock(); opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        client = crawl.APIClient(self.con, 'https://en.wikipedia.org/w/api.php', 'https://example.org/contact', opener)
        with patch.object(crawl.time, 'time', return_value=1000), self.assertRaises(crawl.Deferred):
            client.get({'action': 'query'})
        self.assertGreaterEqual(crawl.getmeta(self.con, 'next_request'), 1900)

    def test_forbidden_is_not_retried_as_missing_page(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('url', 403, 'Forbidden', {}, io.BytesIO())
        client = crawl.APIClient(self.con, 'https://en.wikipedia.org/w/api.php', 'https://example.org/contact', opener)
        with self.assertRaises(RuntimeError):
            client.get({'action': 'query'})
        self.assertEqual(opener.open.call_count, 1)

    def test_redirects_do_not_make_hidden_requests(self):
        self.assertIsNone(crawl.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org'))


class ArticleTests(unittest.TestCase):
    def test_named_refs_grouped_notes_and_subreferences(self):
        html = '''<sup id="cite_ref-book">1</sup><sup id="cite_ref-book2">1</sup>
        <ol class="references"><li id="cite_note-book">Source
        <ol class="mw-subreference-list"><li id="cite_note-sub">Detail</li></ol></li>
        <li id="cite_note-other">Source</li></ol>
        <ol class="references"><li id="cite_note-book">Repeated list entry</li></ol>
        <ol class="references" data-mw-group="lower-alpha"><li id="cite_note-note">Note
        <ol class="mw-subreference-list"><li id="cite_note-note-sub">Detail</li></ol></li></ol>'''
        r = inspect_html(html, fragment=True)
        self.assertEqual(r['reference_entries'], 3)
        self.assertEqual(r['grouped_notes_excluded'], 2)
        self.assertEqual(r['identity_status'], 'requires_revision_query')

    def test_100_and_101_boundary(self):
        for n in (100, 101):
            html = '<ol class="references">'+''.join(f'<li id="cite_note-{i}"></li>' for i in range(n))+'</ol>'
            self.assertEqual(inspect_html(html, fragment=True)['reference_entries'] > 100, n == 101)

    def test_website_metadata_and_article_scope(self):
        r = inspect_html(document(), 1)
        self.assertEqual(r['reference_entries'], 1)
        self.assertEqual(r['title'], 'Example Artist')
        self.assertEqual(r['revid'], 99)
        self.assertNotIn('cite_note-outside', r['reference_ids_by_group'][''])

    def test_uncertain_identity_needs_fallback(self):
        for changes in ({'wgArticleId': 2}, {'wgIsRedirect': True}, {'wgNamespaceNumber': 14},
                        {'wgRevisionId': 0}, {'wgCategories': None}, {'wgWikibaseItemId': 'bad'}):
            with self.subTest(changes=changes), self.assertRaises(WebsiteUnsupported):
                inspect_html(document(**changes), 1)

    def test_unknown_or_truncated_markup_is_not_zero(self):
        for html in ('<sup class="reference">1</sup>', '<li id="cite_note-orphan">x</li>', '<ol class="references">'):
            with self.subTest(html=html), self.assertRaises(WebsiteUnsupported):
                inspect_html(html, fragment=True)
        with self.assertRaises(WebsiteUnsupported):
            inspect_html('<html>Access challenge</html>', 1)
        with self.assertRaises(WebsiteUnsupported):
            inspect_html(document()[:-12], 1)

    def test_json_is_decoded_not_executed(self):
        r = inspect_html(document(wgPageName='Brackets_[_and_"quotes"'), 1)
        self.assertEqual(r['title'], 'Brackets [ and "quotes"')
        with self.assertRaises(WebsiteUnsupported):
            inspect_html(document().replace('RLCONF={', 'RLCONF=evil({'), 1)


class CanonicalTests(unittest.TestCase):
    def test_alias_parse_id_is_replaced_by_revision_owner(self):
        from canonical import revision_owners
        response = {'query': {'pages': [{'pageid': 99, 'ns': 0, 'revisions': [{'revid': 123}]}]}}
        self.assertEqual(revision_owners(response, [123]), {123: 99})
        with self.assertRaises(ValueError):
            revision_owners(response, [123, 456])

    def test_latest_below_threshold_removes_older_match(self):
        from canonical import latest_canonical
        older = {'original_pageid': 1, 'canonical_pageid': 99, 'revision_id': 123,
                 'checked_at': '2026-10-20T10:00:00+00:00', 'count': 150}
        newer = {**older, 'original_pageid': 2, 'revision_id': 124,
                 'checked_at': '2026-10-21T10:00:00Z', 'count': 80}
        latest = latest_canonical([newer, older])
        self.assertEqual(latest, [newer])
        self.assertEqual([row for row in latest if row['count'] > 100], [])

    def test_identity_and_timestamp_are_required(self):
        from canonical import latest_canonical
        with self.assertRaises(ValueError):
            latest_canonical([{'original_pageid': 1, 'canonical_pageid': None, 'revision_id': 1}])
        with self.assertRaises(ValueError):
            latest_canonical([{'original_pageid': 1, 'canonical_pageid': 1, 'revision_id': 1,
                              'checked_at': '2026-10-20T10:00:00'}])


if __name__ == '__main__':
    unittest.main()
