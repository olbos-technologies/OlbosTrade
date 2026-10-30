"""Durable quarantine for normalized history pages, NOT the execution ledger.

No network, credentials, broker submission, or authorization. Download adapters
must establish authenticity separately; caller-declared report coverage is not
proof. SQLite stores private staging metadata only, never grants verified=True.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path


class HistoryImportStaging:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS imports (
          id TEXT PRIMARY KEY, account TEXT NOT NULL, organization TEXT NOT NULL,
          start TEXT NOT NULL, end TEXT NOT NULL, pages INTEGER NOT NULL,
          state TEXT NOT NULL DEFAULT 'collecting');
        CREATE TABLE IF NOT EXISTS pages (
          import_id TEXT REFERENCES imports(id), number INTEGER, digest TEXT,
          PRIMARY KEY(import_id, number));
        CREATE TABLE IF NOT EXISTS executions (
          import_id TEXT REFERENCES imports(id), execution_id TEXT, payload TEXT,
          PRIMARY KEY(import_id, execution_id));
        ''')

    def close(self):
        self.db.close()

    def begin(self, import_id: str, *, account: str, organization: str,
              start: datetime, end: datetime, pages: int):
        if (not all(isinstance(x, str) and x.strip() for x in (import_id, account, organization))
                or not isinstance(start, datetime) or not isinstance(end, datetime)
                or start.utcoffset() is None or end.utcoffset() is None or start >= end
                or type(pages) is not int or pages < 1):
            raise ValueError('invalid_import_manifest')
        values = (import_id, account, organization, start.isoformat(), end.isoformat(), pages)
        with self.db:
            row = self.db.execute('SELECT id,account,organization,start,end,pages FROM imports WHERE id=?', (import_id,)).fetchone()
            if row is not None and row != values:
                raise ValueError('conflicting_import_manifest')
            self.db.execute('INSERT OR IGNORE INTO imports(id,account,organization,start,end,pages) VALUES(?,?,?,?,?,?)', values)

    def add_page(self, import_id: str, number: int, records: list[dict]):
        """Commit page receipt and rows together; conflicting evidence quarantines.

        Normalized schema is intentionally separate from native Flex XML/CSV.
        Every record includes execution_id/account/organization/filled_at plus
        its adapter-defined payload. Same execution ID with changed payload is
        never overwritten or automatically treated as a correction.
        """
        manifest = self.db.execute('SELECT account,organization,start,end,pages,state FROM imports WHERE id=?', (import_id,)).fetchone()
        if manifest is None:
            raise ValueError('unknown_import')
        account, organization, start, end, total, state = manifest
        if state != 'collecting':
            raise ValueError('import_not_collecting')
        try:
            if type(number) is not int or not 1 <= number <= total or type(records) is not list:
                raise ValueError('invalid_page')
            payloads = []
            for record in records:
                if (not isinstance(record, dict) or not isinstance(record.get('execution_id'), str)
                        or not record['execution_id'].strip()
                        or record.get('account') != account or record.get('organization') != organization):
                    raise ValueError('invalid_record_scope_or_identity')
                timestamp = datetime.fromisoformat(record['filled_at'])
                if timestamp.utcoffset() is None or not datetime.fromisoformat(start) <= timestamp < datetime.fromisoformat(end):
                    raise ValueError('execution_outside_report_period')
                payloads.append((record['execution_id'], json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False)))
            digest = hashlib.sha256(json.dumps(payloads, separators=(',', ':')).encode()).hexdigest()
            with self.db:
                prior = self.db.execute('SELECT digest FROM pages WHERE import_id=? AND number=?', (import_id, number)).fetchone()
                if prior is not None and prior[0] != digest:
                    raise ValueError('conflicting_page')
                for execution_id, payload in payloads:
                    previous = self.db.execute('SELECT payload FROM executions WHERE import_id=? AND execution_id=?', (import_id, execution_id)).fetchone()
                    if previous is not None and previous[0] != payload:
                        raise ValueError('execution_correction_requires_review')
                    self.db.execute('INSERT OR IGNORE INTO executions VALUES(?,?,?)', (import_id, execution_id, payload))
                self.db.execute('INSERT OR IGNORE INTO pages VALUES(?,?,?)', (import_id, number, digest))
        except (ValueError, TypeError, KeyError) as exc:
            with self.db:
                self.db.execute("UPDATE imports SET state='quarantined' WHERE id=?", (import_id,))
            raise ValueError('history_page_rejected') from exc

    def finish(self, import_id: str) -> dict:
        with self.db:
            row = self.db.execute('SELECT pages,state FROM imports WHERE id=?', (import_id,)).fetchone()
            if row is None:
                raise ValueError('unknown_import')
            count = self.db.execute('SELECT COUNT(*) FROM pages WHERE import_id=?', (import_id,)).fetchone()[0]
            if row[1] == 'quarantined':
                raise ValueError('import_quarantined')
            if count != row[0]:
                raise ValueError('history_pages_missing')
            self.db.execute("UPDATE imports SET state='staged_unverified' WHERE id=?", (import_id,))
        return {'state': 'staged_unverified', 'verified': False, 'pages': count}
