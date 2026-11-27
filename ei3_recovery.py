"""Offline recovery classification; never retries or releases authority."""
from ei3_pg_audit import audit_snapshot

def recover_unknown_commit(fields, trust, witness, connect):
    """Freeze both independent fixture journals before inspecting committed state.
    A matching snapshot is diagnostic only, never permission to resume.
    """
    failures = []
    for journal in (trust, witness):
        try:
            journal.freeze()
        except Exception:
            failures.append(journal.domain)
    if failures:
        return "QUARANTINE_INCOMPLETE"
    try:
        audit_snapshot(connect, fields)
    except Exception:
        return "UNKNOWN_FROZEN"
    return "COMMITTED_OBSERVED_FROZEN"
