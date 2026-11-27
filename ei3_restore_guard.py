"""Offline preflight diagnostic; not an atomic authorization or credential gate."""
from contextlib import closing
from ei3_durable_journal import Denied

def reconcile_heads(trust, witness, connect):
    """Compare independent fixture heads to a fresh read-only PostgreSQL snapshot."""
    try:
        if trust.domain != "TRUST" or witness.domain != "WITNESS" or trust.path == witness.path:
            raise Denied("journal identity")
        with closing(connect()) as db:
            if db.info.transaction_status != 0:
                raise Denied("connection not idle")
            with db.transaction():
                db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                if db.execute("SELECT session_user,current_user,current_setting('transaction_read_only')").fetchone() != ("ei3_audit","ei3_audit","on"):
                    raise Denied("audit identity")
                rows = db.execute("SELECT key_epoch,checkpoint,trust_sequence,witness_sequence,mode FROM ei3.audit_state").fetchall()
                if len(rows) != 1 or rows[0][4] == "FROZEN":
                    raise Denied("database state")
                epoch, checkpoint, ts, ws, _ = rows[0]
                for journal, sequence in ((trust,ts),(witness,ws)):
                    with closing(journal._connect()) as local:
                        head = local.execute("SELECT domain,epoch,checkpoint,sequence,frozen FROM head WHERE id=1").fetchone()
                        if head != (journal.domain,epoch,checkpoint,sequence,0):
                            raise Denied("head mismatch")
        return "MATCHED_SYNTHETIC_ONLY"
    except Exception as exc:
        incomplete = False
        for journal in (trust,witness):
            try:
                journal.freeze()
            except Exception:
                incomplete = True
        raise Denied("quarantine incomplete" if incomplete else "quarantined") from exc
