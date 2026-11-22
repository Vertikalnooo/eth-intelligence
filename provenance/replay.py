"""Offline durable replay claims. No live market data or network access."""
from contextlib import closing
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import sqlite3
from datetime import timedelta
from decimal import Decimal

NAMESPACE = 'mark-update'

@dataclass(frozen=True)
class ReplayKey:
    venue: str
    market_id: str
    event_namespace: str
    event_id: str

    def valid(self):
        fields = (self.venue, self.market_id, self.event_namespace, self.event_id)
        return (all(type(x) is str and 0 < len(x) <= 128 and x.isascii()
                    and all(ch.isalnum() or ch in '._-' for ch in x) for x in fields)
                and self.venue == 'Hyperliquid'
                and self.market_id == 'ETH-PERP'
                and self.event_namespace == NAMESPACE)

    def canonical(self):
        if not self.valid():
            raise ValueError('invalid replay key')
        return '|'.join(f'{len(x)}:{x}' for x in
                        (self.venue, self.market_id, self.event_namespace, self.event_id))

def binding_digest(binding):
    """Canonical digest of a *previously verified* binding; digest is not proof."""
    from .models import VerifiedBinding
    if (type(binding) is not VerifiedBinding
            or (binding.venue, binding.symbol, binding.price_type, binding.timestamp_semantics)
            != ('Hyperliquid', 'ETH', 'mark', 'mark_update')
            or type(binding.event_id) is not str or not binding.event_id
            or type(binding.observation_id) is not str or not binding.observation_id
            or type(binding.mark_px) is not Decimal
            or not binding.mark_px.is_finite() or binding.mark_px <= 0
            or binding.exchange_timestamp.tzinfo is None
            or binding.exchange_timestamp.utcoffset() != timedelta(0)):
        raise ValueError('invalid verified binding')
    price = format(binding.mark_px.normalize(), 'f')
    ts = binding.exchange_timestamp.isoformat(timespec='microseconds')
    data = ['eth-mark-binding-v1', binding.venue, 'ETH-PERP', NAMESPACE,
            binding.event_id, binding.observation_id, binding.price_type, price,
            ts, binding.timestamp_semantics]
    return sha256(json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode('ascii')).digest()

class ClaimResult(Enum):
    CLAIMED = 'claimed'
    DUPLICATE = 'duplicate'
    CONFLICT = 'conflict'
    UNAVAILABLE = 'unavailable'

class SQLiteReplayStore:
    """SQLite BEGIN IMMEDIATE + UNIQUE key; requires durable local storage."""
    def __init__(self, path):
        self.path = str(path)
        if self.path == ':memory:' or self.path.startswith('file:'):
            raise ValueError('persistent ordinary file path required')
        with closing(self._connect()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS replay (key TEXT PRIMARY KEY, digest BLOB NOT NULL)')

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute('PRAGMA busy_timeout=5000')
            db.execute('PRAGMA synchronous=FULL')
            return db
        except Exception:
            db.close()
            raise

    def claim_once(self, key: ReplayKey, digest: bytes) -> ClaimResult:
        if (type(key) is not ReplayKey or not key.valid()
                or type(digest) is not bytes or len(digest) != 32):
            return ClaimResult.UNAVAILABLE
        try:
            with closing(self._connect()) as db:
                try:
                    db.execute('BEGIN IMMEDIATE')
                    canonical = key.canonical()
                    existing = db.execute('SELECT digest FROM replay WHERE key=?', (canonical,)).fetchone()
                    if existing is not None:
                        result = ClaimResult.DUPLICATE if existing[0] == digest else ClaimResult.CONFLICT
                    else:
                        db.execute('INSERT INTO replay(key,digest) VALUES (?,?)', (canonical, digest))
                        result = ClaimResult.CLAIMED
                    db.execute('COMMIT')
                    return result
                except Exception:
                    if db.in_transaction:
                        db.execute('ROLLBACK')
                    raise
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return ClaimResult.UNAVAILABLE
