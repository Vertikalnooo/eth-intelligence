"""Offline journal primitive; not a production signer or commit auditor."""
import hashlib
import hmac
import sqlite3
from contextlib import closing
from ei_cap3 import frame

class Denied(RuntimeError):
    pass

class Journal:
    """One independent fixture store. Caller supplies independently validated policy."""
    def __init__(self, path, domain, key):
        if domain not in ("TRUST", "WITNESS") or type(key) is not bytes or len(key) != 32:
            raise ValueError("signer configuration")
        self.path, self.domain, self._key = path, domain, key

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def initialize_fixture(self, epoch, checkpoint, sequence):
        """Explicit test fixture only; never resets an existing journal."""
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE head (id INTEGER PRIMARY KEY CHECK(id=1), domain TEXT NOT NULL, epoch TEXT NOT NULL, checkpoint TEXT NOT NULL, sequence INTEGER NOT NULL, frozen INTEGER NOT NULL)")
            db.execute("CREATE TABLE spent (authorization TEXT PRIMARY KEY, digest TEXT NOT NULL, old_checkpoint TEXT UNIQUE NOT NULL, new_checkpoint TEXT UNIQUE NOT NULL)")
            db.execute("INSERT INTO head VALUES (1,?,?,?,?,0)", (self.domain, epoch, checkpoint, sequence))
            db.commit()

    def spend(self, fields, *, validate, now):
        """Commit before returning MAC. No retry/re-release. Policy callback is trusted."""
        fields = dict(fields)
        body = frame(fields)
        if not validate(dict(fields)):
            raise Denied("policy")
        if not int(fields["not_before"]) <= now < int(fields["expires_at"]) or not 0 < int(fields["expires_at"])-int(fields["issued_at"]) <= 30 or int(fields["issued_at"]) > now:
            raise Denied("time")
        digest = hashlib.sha256(body).hexdigest()
        seq = int(fields["trust_sequence" if self.domain == "TRUST" else "witness_sequence"])
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            head = db.execute("SELECT domain,epoch,checkpoint,sequence,frozen FROM head WHERE id=1").fetchone()
            if head != (self.domain, fields["key_epoch"], fields["old_checkpoint"], seq-1, 0) or fields["new_checkpoint"] == fields["old_checkpoint"]:
                raise Denied("head mismatch")
            try:
                db.execute("INSERT INTO spent VALUES (?,?,?,?)", (fields["authorization_id"], digest, fields["old_checkpoint"], fields["new_checkpoint"]))
            except sqlite3.IntegrityError as exc:
                raise Denied("replay") from exc
            db.execute("UPDATE head SET checkpoint=?,sequence=? WHERE id=1", (fields["new_checkpoint"],seq))
            db.commit()
        return hmac.digest(self._key, ("EI-CAP-3/"+self.domain+"\n").encode()+body, "sha256")

    def freeze(self):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("UPDATE head SET frozen=1 WHERE id=1").rowcount != 1:
                raise Denied("missing head")
            db.commit()
