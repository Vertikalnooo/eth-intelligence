"""Offline SQLite atomic replay claims. No external services."""
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import sqlite3
from pathlib import Path

@dataclass(frozen=True)
class ReplayKey:
    venue: str
    market_id: str
    event_namespace: str
    event_id: str

    def valid(self):
        fields=(self.venue,self.market_id,self.event_namespace,self.event_id)
        return (all(type(x) is str and 0 < len(x) <= 128 and x.isascii()
                    and all(ch.isalnum() or ch in '._-' for ch in x) for x in fields)
                and self.venue == 'Hyperliquid' and self.market_id == 'ETH-PERP')

    def canonical(self):
        if not self.valid():
            raise ValueError('invalid replay key')
        return '|'.join(f'{len(x)}:{x}' for x in
                        (self.venue,self.market_id,self.event_namespace,self.event_id))

class ClaimResult(Enum):
    CLAIMED='claimed'
    DUPLICATE='duplicate'
    CONFLICT='conflict'
    UNAVAILABLE='unavailable'

class SQLiteReplayStore:
    """Transactional durable offline claim; callers must use a persistent path."""
    def __init__(self, path):
        self.path=str(path)
        if self.path == ':memory:':
            raise ValueError('persistent database path required')
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS replay (key TEXT PRIMARY KEY, digest BLOB NOT NULL)')

    def _connect(self):
        db=sqlite3.connect(self.path,timeout=5,isolation_level=None)
        db.execute('PRAGMA busy_timeout=5000')
        db.execute('PRAGMA synchronous=FULL')
        return db

    def claim_once(self,key:ReplayKey,binding_digest:bytes)->ClaimResult:
        if type(key) is not ReplayKey or not key.valid() or type(binding_digest) is not bytes or len(binding_digest)!=32:
            return ClaimResult.UNAVAILABLE
        try:
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                canonical=key.canonical()
                existing=db.execute('SELECT digest FROM replay WHERE key=?',(canonical,)).fetchone()
                if existing is not None:
                    result=ClaimResult.DUPLICATE if existing[0]==binding_digest else ClaimResult.CONFLICT
                    db.execute('COMMIT')
                    return result
                db.execute('INSERT INTO replay(key,digest) VALUES (?,?)',(canonical,binding_digest))
                db.execute('COMMIT')
                return ClaimResult.CLAIMED
        except (sqlite3.Error,OSError,ValueError):
            return ClaimResult.UNAVAILABLE
