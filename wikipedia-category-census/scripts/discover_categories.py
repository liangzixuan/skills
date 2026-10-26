#!/usr/bin/env python3
"""Discover a Wikipedia category graph with durable, serial API requests.

This helper discovers candidates. It does not retrieve or classify articles.
Python 3.10+; standard library only; macOS/Linux file locking.
"""
import argparse
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import email.utils
import fcntl
import gzip
import json
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


@contextmanager
def worker_lock(folder):
    with (folder / 'worker.lock').open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another worker holds this workspace lock') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def getmeta(con, key, default=None):
    row = con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def putmeta(con, key, value):
    con.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))


def connect(folder, endpoint, roots, delay):
    con = sqlite3.connect(folder / 'discovery.sqlite3', timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript('''
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS categories(
      title TEXT PRIMARY KEY,cursor TEXT,done INTEGER NOT NULL DEFAULT 0,
      batches INTEGER NOT NULL DEFAULT 0,checked_at TEXT,error TEXT);
    CREATE TABLE IF NOT EXISTS members(
      category TEXT NOT NULL REFERENCES categories(title),pageid INTEGER NOT NULL,
      ns INTEGER NOT NULL,title TEXT NOT NULL,observed_at TEXT NOT NULL,
      PRIMARY KEY(category,pageid));
    CREATE INDEX IF NOT EXISTS members_page ON members(pageid);
    CREATE TABLE IF NOT EXISTS pages(pageid INTEGER PRIMARY KEY,title TEXT NOT NULL);
    ''')
    spec = {'schema': 1, 'endpoint': endpoint, 'roots': sorted(set(roots)), 'namespaces': [0, 14]}
    old = getmeta(con, 'scope')
    if old is not None and old != spec:
        con.close()
        raise ValueError('Workspace scope differs; use a new workspace for a new scope')
    with con:
        putmeta(con, 'scope', spec)
        if old is None:
            putmeta(con, 'started_at', utc())
            putmeta(con, 'next_request', 0)
            putmeta(con, 'api_delay', delay)
            putmeta(con, 'requests', 0)
            putmeta(con, 'failures', 0)
        # A resume may increase pacing, but must not erase existing backoff.
        putmeta(con, 'api_delay', max(delay, getmeta(con, 'api_delay', delay)))
        for title in spec['roots']:
            con.execute('INSERT OR IGNORE INTO categories(title) VALUES (?)', (title,))
    return con


def retry_after(value, now=None):
    if not value:
        return 0.0
    now = time.time() if now is None else now
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 0.0


class Deferred(Exception):
    """The current work item remains pending until the persisted deadline."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class APIClient:
    def __init__(self, con, endpoint, contact, opener=None):
        if not contact.strip() or any(c in contact for c in '\r\n'):
            raise ValueError('Supply a real operator contact without newlines')
        self.con, self.endpoint = con, endpoint
        self.user_agent = f'WikipediaCategoryCensus/1.0 ({contact}) Python-urllib'
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def defer(self, reason, after=0, throttled=False):
        failures = getmeta(self.con, 'failures', 0) + 1
        delay = getmeta(self.con, 'api_delay')
        if throttled:
            delay = max(delay, min(300, delay * 2))
        wait = max(after, delay, min(3600, 30 * 2 ** min(failures - 1, 7)))
        with self.con:
            putmeta(self.con, 'api_delay', delay)
            putmeta(self.con, 'failures', failures)
            putmeta(self.con, 'next_request', max(getmeta(self.con, 'next_request'), time.time() + wait))
        raise Deferred(reason)

    def get(self, params):
        if time.time() < getmeta(self.con, 'next_request', 0):
            raise Deferred('Waiting for the persisted request deadline')
        values = {**params, 'format': 'json', 'formatversion': 2, 'maxlag': 5}
        req = urllib.request.Request(self.endpoint + '?' + urllib.parse.urlencode(values),
              headers={'User-Agent': self.user_agent, 'Accept-Encoding': 'gzip'})
        delay = getmeta(self.con, 'api_delay')
        # Reserve a timeout interval in case the process dies during the request.
        with self.con:
            putmeta(self.con, 'next_request', time.time() + 45 + delay)
            putmeta(self.con, 'requests', getmeta(self.con, 'requests', 0) + 1)
        try:
            with self.opener.open(req, timeout=45) as response:
                after = retry_after(response.headers.get('Retry-After'))
                body = response.read()
                if response.headers.get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
                data = json.loads(body)
        except urllib.error.HTTPError as exc:
            after = retry_after(exc.headers.get('Retry-After'))
            if exc.code == 429 or 500 <= exc.code < 600:
                self.defer(f'HTTP {exc.code}', after, exc.code == 429)
            # Authentication, access and protocol errors need inspection, not a retry loop.
            raise RuntimeError(f'HTTP {exc.code}: inspect access and endpoint; checkpoint retained') from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            self.defer(f'Network or response error: {type(exc).__name__}')
        if not isinstance(data, dict):
            raise RuntimeError('Expected a JSON object; checkpoint retained')
        if 'error' in data:
            error = data['error']
            if error.get('code') in ('maxlag', 'ratelimited', 'readonly'):
                self.defer(json.dumps(error), after, error.get('code') == 'ratelimited')
            raise RuntimeError('API error: ' + json.dumps(error))
        if data.get('warnings'):
            raise RuntimeError('Inspect API warnings before resuming: ' + json.dumps(data['warnings']))
        with self.con:
            putmeta(self.con, 'next_request', time.time() + max(delay, after))
            putmeta(self.con, 'failures', 0)
        return data


def apply_batch(con, title, data):
    """Commit members and continuation together; replaying a batch is harmless."""
    members = data.get('query', {}).get('categorymembers')
    cursor = data.get('continue')
    if not isinstance(members, list):
        raise ValueError('Missing categorymembers list')
    if cursor is not None and (not isinstance(cursor, dict) or not isinstance(cursor.get('cmcontinue'), str) or not cursor.get('cmcontinue')
            or set(cursor) - {'continue', 'cmcontinue'} or not all(isinstance(v, str) for v in cursor.values())):
        raise ValueError('Invalid category continuation')
    current = con.execute('SELECT cursor FROM categories WHERE title=?', (title,)).fetchone()
    if cursor is not None and current['cursor'] and json.loads(current['cursor']) == cursor:
        raise ValueError('Repeated continuation token; inspect the response')
    for member in members:
        if (type(member.get('pageid')) is not int or member['pageid'] <= 0
                or member.get('ns') not in (0, 14) or not isinstance(member.get('title'), str)):
            raise ValueError('Unexpected category member')
    now = utc()
    with con:
        for member in members:
            con.execute('INSERT OR REPLACE INTO members VALUES (?,?,?,?,?)',
                        (title, member['pageid'], member['ns'], member['title'], now))
            if member['ns'] == 14:
                con.execute('INSERT OR IGNORE INTO categories(title) VALUES (?)', (member['title'],))
            else:
                con.execute('INSERT OR IGNORE INTO pages VALUES (?,?)', (member['pageid'], member['title']))
        con.execute('UPDATE categories SET cursor=?,done=?,batches=batches+1,checked_at=?,error=NULL WHERE title=?',
                    (json.dumps(cursor) if cursor else None, int(cursor is None), now, title))


def provenance(con):
    """Return a shortest observed category path per root and original article ID."""
    adjacency = {}
    for row in con.execute('SELECT category,pageid,ns,title FROM members ORDER BY category,title,pageid'):
        adjacency.setdefault(row['category'], []).append(dict(row))
    result = {}
    for root in getmeta(con, 'scope')['roots']:
        queue, seen = deque([(root, [root])]), {root}
        while queue:
            cat, path = queue.popleft()
            for m in adjacency.get(cat, []):
                if m['ns'] == 14 and m['title'] not in seen:
                    seen.add(m['title']); queue.append((m['title'], path + [m['title']]))
                elif m['ns'] == 0:
                    result.setdefault(m['pageid'], {}).setdefault(root, path)
    return result


def report(con, folder, state, error=None):
    total = con.execute('SELECT count(*) FROM categories').fetchone()[0]
    done = con.execute('SELECT count(*) FROM categories WHERE done=1').fetchone()[0]
    counts = {'phase': 'discovery', 'state': state, 'at': utc(), 'categories_total': total,
              'categories_done': done, 'original_pages': con.execute('SELECT count(*) FROM pages').fetchone()[0],
              'discovery_complete': done == total,
              'dataset_complete': False, 'next_request': getmeta(con, 'next_request'),
              'api_delay': getmeta(con, 'api_delay'), 'requests': getmeta(con, 'requests'), 'error': error}
    write_json(folder / 'discovery-status.json', counts)
    with (folder / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps(counts) + '\n')
    return counts


def export_candidates(con, folder):
    paths = provenance(con)
    tmp = folder / 'candidates.jsonl.tmp'
    with tmp.open('w') as stream:
        for row in con.execute('SELECT * FROM pages ORDER BY pageid'):
            stream.write(json.dumps({**dict(row), 'root_paths': paths[row['pageid']]}, ensure_ascii=False) + '\n')
    tmp.replace(folder / 'candidates.jsonl')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--root', action='append', required=True, help='Exact category title; repeat for multiple roots')
    parser.add_argument('--api', default='https://en.wikipedia.org/w/api.php')
    parser.add_argument('--contact', required=True, help='Real operator email or full contact URL')
    parser.add_argument('--delay', type=float, default=5.0, help='Minimum seconds after each response; not a policy quota')
    parser.add_argument('--run-seconds', type=float, default=300, help='Segment duration; does not limit discovery scope')
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.api)
    if (url.scheme != 'https' or not url.hostname or not url.hostname.endswith('.wikipedia.org')
            or url.path != '/w/api.php' or url.query or url.fragment or url.username or url.port):
        parser.error('--api must be an HTTPS Wikipedia /w/api.php endpoint')
    if args.delay < 1 or args.run_seconds <= 0 or any(':' not in root for root in args.root):
        parser.error('Use a delay of at least one second, a positive segment duration and full category titles')
    folder = args.workdir.resolve(); folder.mkdir(parents=True, exist_ok=True)
    with worker_lock(folder):
        con = connect(folder, args.api, args.root, args.delay)
        client = APIClient(con, args.api, args.contact)
        end = time.monotonic() + args.run_seconds
        try:
            while time.monotonic() < end:
                if (folder / 'STOP').exists():
                    report(con, folder, 'stopped'); return 0
                row = con.execute('SELECT * FROM categories WHERE done=0 ORDER BY rowid LIMIT 1').fetchone()
                if row is None:
                    export_candidates(con, folder)
                    print(json.dumps(report(con, folder, 'discovered'))); return 0
                wait = getmeta(con, 'next_request') - time.time()
                if wait > 0:
                    time.sleep(min(wait, 1, max(0, end - time.monotonic()))); continue
                params = {'action': 'query', 'list': 'categorymembers', 'cmtitle': row['title'],
                          'cmnamespace': '0|14', 'cmprop': 'ids|title', 'cmlimit': 'max'}
                if row['cursor']:
                    params.update(json.loads(row['cursor']))
                try:
                    apply_batch(con, row['title'], client.get(params))
                    report(con, folder, 'running')
                except Deferred as exc:
                    with con:
                        con.execute('UPDATE categories SET error=? WHERE title=?', (str(exc), row['title']))
                    report(con, folder, 'backoff', str(exc))
                except Exception as exc:
                    with con:
                        con.execute('UPDATE categories SET error=? WHERE title=?', (str(exc), row['title']))
                    report(con, folder, 'needs_attention', str(exc)); raise
            if con.execute('SELECT 1 FROM categories WHERE done=0 LIMIT 1').fetchone() is None:
                export_candidates(con, folder)
                print(json.dumps(report(con, folder, 'discovered')))
            else:
                print(json.dumps(report(con, folder, 'segment_finished')))
        except KeyboardInterrupt:
            report(con, folder, 'interrupted'); return 130
        finally:
            con.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
