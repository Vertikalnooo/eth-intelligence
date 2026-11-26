"""Research-only supervised transport and signed, durable grant lifecycle.

No live entrypoint. This module never connects to the exchange. Private
loopback worker fixtures exercise the same bounded HTTP decoder.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
from uuid import uuid4

from rest_one_shot import OneShotError

SCOPE = "OFFLINE:ETH-PERP:one-shot:v2"
MAX_TTL = 300
DEADLINE_NS = 8_000_000_000
HEX = re.compile(r"[0-9a-f]{32}")
SHA = re.compile(r"[0-9a-f]{64}")
CANDIDATE = re.compile(r"[0-9a-f]{40}")
SCHEMA = """
CREATE TABLE identity (id TEXT PRIMARY KEY NOT NULL);
CREATE TABLE grants (
 id TEXT PRIMARY KEY NOT NULL,
 signature TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('ISSUED','REVOKED','CONSUMED')),
 changed_at INTEGER NOT NULL
);
CREATE TRIGGER no_delete BEFORE DELETE ON grants
BEGIN SELECT RAISE(ABORT,'grant deletion forbidden'); END;
CREATE TRIGGER terminal BEFORE UPDATE ON grants
WHEN OLD.state != 'ISSUED' OR NEW.state NOT IN ('REVOKED','CONSUMED')
 OR OLD.id != NEW.id OR OLD.signature != NEW.signature
BEGIN SELECT RAISE(ABORT,'illegal transition'); END;
"""


@dataclass(frozen=True)
class Grant:
    store_id: str
    grant_id: str
    candidate: str
    scope: str
    issued_at: int
    expires_at: int
    signature: str


def _key(key):
    if type(key) is not bytes or len(key) != 32:
        raise OneShotError("invalid_operator_key")
    return key


def _message(g):
    if (type(g) is not Grant or type(g.store_id) is not str
            or HEX.fullmatch(g.store_id) is None
            or type(g.grant_id) is not str or HEX.fullmatch(g.grant_id) is None
            or type(g.candidate) is not str or CANDIDATE.fullmatch(g.candidate) is None
            or type(g.scope) is not str or g.scope != SCOPE
            or type(g.issued_at) is not int or type(g.expires_at) is not int
            or not 0 < g.expires_at - g.issued_at <= MAX_TTL):
        raise OneShotError("invalid_grant")
    return json.dumps([g.store_id,g.grant_id,g.candidate,g.scope,
                       g.issued_at,g.expires_at], separators=(",",":")).encode()


def _signature(g, key):
    return hmac.new(_key(key), _message(g), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Store:
    path: Path
    store_id: str
    device: int
    inode: int
    candidate: str

    def _check_path(self):
        if (not isinstance(self.path, Path) or not self.path.is_absolute()
                or self.path.resolve(strict=True) != self.path):
            raise OneShotError("noncanonical_store")
        parent = self.path.parent.stat()
        file = self.path.lstat()
        uid = os.geteuid()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid
                or parent.st_mode & 0o077
                or not stat.S_ISREG(file.st_mode) or file.st_uid != uid
                or file.st_mode & 0o077 or file.st_nlink != 1
                or (file.st_dev,file.st_ino) != (self.device,self.inode)):
            raise OneShotError("untrusted_store_path")
        for suffix in ("-journal","-wal","-shm"):
            side = Path(str(self.path) + suffix)
            if side.is_symlink():
                raise OneShotError("untrusted_store_sidecar")
            if side.exists():
                info = side.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                        or info.st_mode & 0o077 or info.st_nlink != 1):
                    raise OneShotError("untrusted_store_sidecar")

    def _connect(self):
        db = None
        try:
            self._check_path()
            db = sqlite3.connect(self.path.as_uri()+"?mode=rw", uri=True,
                                 timeout=1, isolation_level=None)
            self._check_path()
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise OneShotError("unexpected_journal_mode")
            if db.execute("SELECT id FROM identity").fetchall() != [(self.store_id,)]:
                raise OneShotError("store_identity_mismatch")
            return db
        except BaseException:
            if db is not None:
                db.close()
            raise

    def _transaction(self, action):
        db = None
        try:
            db = self._connect()
            db.execute("BEGIN IMMEDIATE")
            result = action(db)
            self._check_path()
            db.commit()
            return result
        except (OSError,sqlite3.Error,ValueError,RuntimeError) as exc:
            raise OneShotError("store_unavailable") from exc
        finally:
            if db is not None:
                db.close()

    def _validate(self, g, key, *, current=True):
        expected = _signature(g,key)
        if (type(g.signature) is not str or SHA.fullmatch(g.signature) is None
                or not hmac.compare_digest(g.signature, expected)
                or g.store_id != self.store_id or g.candidate != self.candidate):
            raise OneShotError("grant_signature_or_binding")
        now = int(time.time())
        if current and not g.issued_at <= now < g.expires_at:
            raise OneShotError("grant_not_current")
        return now

    def issue_offline(self, key, ttl=60):
        """Explicit trusted operator action, OFFLINE scope only. Never auto-issue."""
        if type(ttl) is not int or not 0 < ttl <= MAX_TTL:
            raise OneShotError("invalid_ttl")
        now = int(time.time())
        unsigned = Grant(self.store_id,uuid4().hex,self.candidate,SCOPE,now,now+ttl,"")
        g = Grant(unsigned.store_id,unsigned.grant_id,unsigned.candidate,unsigned.scope,
                  unsigned.issued_at,unsigned.expires_at,_signature(unsigned,key))
        def insert(db):
            db.execute("INSERT INTO grants VALUES(?,?,?,?)",
                       (g.grant_id,g.signature,"ISSUED",now))
        self._transaction(insert)
        return g

    def _transition(self,g,key,state):
        def transition(db):
            now = self._validate(g,key,current=state=="CONSUMED")
            changed = db.execute(
                "UPDATE grants SET state=?,changed_at=? WHERE id=? AND signature=? AND state='ISSUED'",
                (state,now,g.grant_id,g.signature)).rowcount
            if changed != 1:
                raise OneShotError("grant_not_issued")
        self._transaction(transition)

    def consume(self,g,key):
        self._transition(g,key,"CONSUMED")

    def revoke(self,g,key):
        self._transition(g,key,"REVOKED")

    def state(self,g,key):
        def read(db):
            self._validate(g,key,current=False)
            row = db.execute("SELECT state FROM grants WHERE id=? AND signature=?",
                             (g.grant_id,g.signature)).fetchone()
            if row is None or row[0] not in ("ISSUED","REVOKED","CONSUMED"):
                raise OneShotError("unknown_grant")
            return row[0]
        return self._transaction(read)


def initialize_offline_store(path, candidate):
    """Explicit offline fixture/operator provisioning; never replaces a store."""
    if (not isinstance(path,Path) or not path.is_absolute()
            or path.parent.resolve(strict=True) != path.parent
            or type(candidate) is not str or CANDIDATE.fullmatch(candidate) is None):
        raise OneShotError("invalid_store_configuration")
    parent = path.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise OneShotError("private_store_directory_required")
    fd = os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
    os.close(fd)
    identity = uuid4().hex
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.executescript(SCHEMA)
        db.execute("INSERT INTO identity VALUES(?)",(identity,))
        db.commit()
    finally:
        db.close()
    dfd = os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    info=path.stat()
    return Store(path,identity,info.st_dev,info.st_ino,candidate)


def _supervise_offline(port, deadline_ns):
    """Fixed loopback worker. Deadline covers startup, I/O, decode and exit.

    Kill on timeout, never retry. Linux child also arms a kernel timer and
    parent-death signal before any I/O. OS scheduling/unkillable kernel
    tasks remain outside the guarantee.
    """
    if sys.platform != "linux" or type(port) is not int or not 0 < port <= 65535:
        raise OneShotError("offline_worker_configuration")
    if type(deadline_ns) is not int or not 0 < deadline_ns-time.monotonic_ns() <= DEADLINE_NS:
        raise OneShotError("invalid_deadline")
    worker=Path(__file__).with_name("rest_one_shot_worker.py")
    args=[sys.executable,str(worker),str(os.getpid()),str(deadline_ns),str(port)]
    process=None
    try:
        process=subprocess.Popen(args,stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                 close_fds=True,env={"PATH":"/usr/bin:/bin","LANG":"C",
                                     "PYTHONPATH":str(Path(__file__).resolve().parent/"scripts/offline_guard")})
        remaining=(deadline_ns-time.monotonic_ns())/1e9
        if remaining <= 0:
            raise OneShotError("hard_deadline")
        try:
            out,_=process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise OneShotError("hard_deadline") from exc
        if time.monotonic_ns() >= deadline_ns:
            raise OneShotError("hard_deadline")
        if process.returncode != 0 or len(out)>512:
            raise OneShotError("worker_failed")
        try:
            result=json.loads(out)
        except (ValueError,UnicodeError) as exc:
            raise OneShotError("invalid_worker_result") from exc
        if (type(result) is not dict or set(result)!={"mark_price","received_ns"}
                or type(result["mark_price"]) is not str
                or type(result["received_ns"]) is not int
                or not 0 <= result["received_ns"] <= time.monotonic_ns()):
            raise OneShotError("invalid_worker_result")
        return result
    except OSError as exc:
        raise OneShotError("worker_unavailable") from exc
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired as exc:
                    raise OneShotError("worker_reap_unconfirmed") from exc
            if process.stdout is not None:
                process.stdout.close()


def run_offline(store,grant,key,port,*,budget_ns=DEADLINE_NS):
    """One signed offline grant, one local worker, no live capability."""
    if (type(store) is not Store or type(budget_ns) is not int
            or not 0 < budget_ns <= DEADLINE_NS or type(port) is not int
            or not 0 < port <= 65535 or sys.platform != "linux"):
        raise OneShotError("invalid_run_configuration")
    store.consume(grant,key)
    deadline=time.monotonic_ns()+budget_ns
    result=_supervise_offline(port,deadline)
    return {"status":"PASS","scope":"OFFLINE_ONLY","mark_price":result["mark_price"],
            "received_ns":result["received_ns"],"attempt_reservations":1,
            "exchange_freshness_verified":False,"recommendation_eligible":False,
            "stage2_complete":False}
