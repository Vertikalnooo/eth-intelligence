"""Offline evidence comparison only; not a database auditor or release gate."""
import hashlib
import hmac
from contextlib import closing
from ei_cap3 import frame
from ei3_durable_journal import Denied

def verify_evidence(fields, trust, witness, trust_mac, witness_mac, committed):
    """Compare fixture evidence. committed must come from a trusted independent auditor.
    Never authorizes external action. No recovery or automatic unfreeze.
    """
    body = frame(fields)
    digest = hashlib.sha256(body).hexdigest()
    try:
        if trust.domain != "TRUST" or witness.domain != "WITNESS" or trust.path == witness.path or hmac.compare_digest(trust._key,witness._key):
            raise Denied("independence")
        for journal, mac, sequence in ((trust,trust_mac,"trust_sequence"),(witness,witness_mac,"witness_sequence")):
            expected = hmac.digest(journal._key,("EI-CAP-3/"+journal.domain+"\n").encode()+body,"sha256")
            if not hmac.compare_digest(expected,mac):
                raise Denied("MAC")
            with closing(journal._connect()) as db:
                db.execute("BEGIN")
                head = db.execute("SELECT domain,epoch,checkpoint,sequence,frozen FROM head WHERE id=1").fetchone()
                spent = db.execute("SELECT digest,old_checkpoint,new_checkpoint FROM spent WHERE authorization=?", (fields["authorization_id"],)).fetchone()
                if head != (journal.domain,fields["key_epoch"],fields["new_checkpoint"],int(fields[sequence]),0) or spent != (digest,fields["old_checkpoint"],fields["new_checkpoint"]):
                    raise Denied("journal mismatch")
        expected = {k:fields[k] for k in ("authorization_id","key_epoch","new_checkpoint","next_owner_head","op","trust_sequence","witness_sequence")}
        expected["digest"] = digest
        if type(committed) is not dict or committed != expected:
            raise Denied("committed evidence mismatch")
        return "MATCHED_SYNTHETIC_ONLY"
    except Exception as exc:
        errors = []
        for journal in (trust,witness):
            try:
                journal.freeze()
            except Exception as freeze_error:
                errors.append(type(freeze_error).__name__)
        raise Denied("evidence rejected; quarantine incomplete" if errors else "evidence rejected; quarantined") from exc
