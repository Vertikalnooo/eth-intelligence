"""Offline journal primitive; not a production signer or commit auditor."""
import hashlib
import hmac
import sqlite3
import re
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

    def _fingerprint(self):
        return hashlib.sha256(b"EI3-JOURNAL-KEY\x00"+self._key).hexdigest()

    def _check_binding(self, db, epoch):
        row = db.execute("SELECT fingerprint,retired FROM key_history WHERE epoch=?", (epoch,)).fetchone()
        if row != (self._fingerprint(),0):
            raise Denied("key binding")

    def initialize_fixture(self, epoch, checkpoint, sequence):
        """Explicit test fixture only; never resets an existing journal."""
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE head (id INTEGER PRIMARY KEY CHECK(id=1), domain TEXT NOT NULL, epoch TEXT NOT NULL, checkpoint TEXT NOT NULL, sequence INTEGER NOT NULL, frozen INTEGER NOT NULL)")
            db.execute("CREATE TABLE spent (authorization TEXT PRIMARY KEY, digest TEXT NOT NULL, old_checkpoint TEXT UNIQUE NOT NULL, new_checkpoint TEXT UNIQUE NOT NULL)")
            db.execute("CREATE TABLE key_history (epoch TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, retired INTEGER NOT NULL CHECK(retired IN (0,1)))")
            db.execute("INSERT INTO key_history VALUES (?,?,0)", (epoch,self._fingerprint()))
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
            self._check_binding(db, fields["key_epoch"])
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

    def rotate_frozen_fixture(self, expected_epoch, expected_checkpoint, new_epoch, new_key):
        """Offline only. Preserve quarantine and all spent/key history."""
        replacement = Journal(self.path, self.domain, new_key)
        if not isinstance(new_epoch,str) or not re.fullmatch(r"[0-9a-f]{32}",new_epoch):
            raise Denied("invalid epoch")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            head = db.execute("SELECT domain,epoch,checkpoint,frozen FROM head WHERE id=1").fetchone()
            if head != (self.domain,expected_epoch,expected_checkpoint,1):
                raise Denied("rotation state mismatch")
            self._check_binding(db,expected_epoch)
            try:
                db.execute("INSERT INTO key_history VALUES (?,?,0)",(new_epoch,replacement._fingerprint()))
            except sqlite3.IntegrityError as exc:
                raise Denied("epoch or key reuse") from exc
            db.execute("UPDATE key_history SET retired=1 WHERE epoch=?",(expected_epoch,))
            db.execute("UPDATE head SET epoch=? WHERE id=1",(new_epoch,))
            db.commit()
        return replacement
