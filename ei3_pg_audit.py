"""Offline PG17 audit primitive. Connection factory is a trusted fixture boundary."""
import hashlib
from contextlib import closing
from ei_cap3 import frame
from ei3_durable_journal import Denied

def audit_snapshot(connect, fields):
    """Require a fresh idle connection with exact audit identity; never release credentials."""
    body = frame(fields)
    digest = hashlib.sha256(body).hexdigest()
    with closing(connect()) as db:
        if db.info.transaction_status != 0:
            raise Denied("connection not idle")
        with db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if db.execute("SELECT session_user,current_user,current_setting('transaction_read_only')").fetchone() != ("ei3_audit","ei3_audit","on"):
                raise Denied("audit identity")
            state = db.execute("SELECT environment,ledger,object_id,candidate,key_epoch,checkpoint,owner_head,credential_revision,trust_sequence,witness_sequence,last_auth FROM ei3.audit_state").fetchall()
            expected = (fields["environment"],fields["ledger"],fields["object_id"],fields["candidate"],fields["key_epoch"],fields["new_checkpoint"],fields["next_owner_head"],int(fields["credential_revision"]),int(fields["trust_sequence"]),int(fields["witness_sequence"]),fields["authorization_id"])
            if state != [expected]:
                raise Denied("state mismatch")
            event = db.execute("SELECT checkpoint,op,plan,grant_id,encode(body_digest,'hex'),evidence,owner_head,credential_revision,trust_sequence,witness_sequence FROM ei3.events WHERE authorization_id=%s",(fields["authorization_id"],)).fetchall()
            if event != [(fields["new_checkpoint"],fields["op"],fields["plan"],fields["grant"],digest,fields["evidence"],fields["next_owner_head"],int(fields["credential_revision"]),int(fields["trust_sequence"]),int(fields["witness_sequence"]))]:
                raise Denied("event mismatch")
            if db.execute("SELECT checkpoint FROM ei3.spent_checkpoints WHERE checkpoint=%s",(fields["old_checkpoint"],)).fetchall() != [(fields["old_checkpoint"],)]:
                raise Denied("spent checkpoint missing")
            if fields["op"] in ("ISSUE","CONSUME","SUCCESS","UNKNOWN"):
                grant = db.execute("SELECT plan,epoch,state FROM ei3.grants WHERE id=%s",(fields["grant"],)).fetchall()
                if grant != [(fields["plan"],fields["epoch"],"ISSUED" if fields["op"]=="ISSUE" else "CONSUMED")]:
                    raise Denied("grant mismatch")
    result = {k:fields[k] for k in ("authorization_id","key_epoch","new_checkpoint","next_owner_head","op","trust_sequence","witness_sequence")}
    result["digest"] = digest
    return result
