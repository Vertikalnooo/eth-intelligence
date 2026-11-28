-- Disposable OFFLINE fixture only. Never apply to a hosted database.
-- Execute as fixture administrator in one transaction after external quarantine.
-- No automatic unfreeze, login provisioning, or credential release.
BEGIN;
-- Local retirement history survives process restart, not privileged database rollback.
CREATE TABLE ei3.retired_rotation_epochs (
    key_epoch text PRIMARY KEY,
    trust_digest bytea NOT NULL,
    witness_digest bytea NOT NULL
);
REVOKE ALL ON ei3.retired_rotation_epochs FROM PUBLIC;
CREATE FUNCTION ei3.rotate_frozen_fixture(
    expected_epoch text, expected_checkpoint text, new_epoch text,
    trust_key bytea, witness_key bytea
) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, ei3, pg_temp SET synchronous_commit = on
AS $$
BEGIN
    IF expected_epoch IS NULL OR expected_checkpoint IS NULL OR new_epoch IS NULL
       OR new_epoch !~ '^[0-9a-f]{32}$' OR new_epoch = expected_epoch
       OR trust_key IS NULL OR witness_key IS NULL
       OR octet_length(trust_key) <> 32 OR octet_length(witness_key) <> 32
       OR trust_key = witness_key THEN
        RAISE EXCEPTION 'invalid rotation';
    END IF;
    PERFORM 1 FROM ei3.state WHERE singleton AND environment='OFFLINE'
        AND mode='FROZEN' AND key_epoch=expected_epoch AND checkpoint=expected_checkpoint
        FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'rotation state mismatch'; END IF;
    IF EXISTS (SELECT 1 FROM ei3.keys AS k WHERE k.trust_key IN (rotate_frozen_fixture.trust_key,rotate_frozen_fixture.witness_key)
        OR k.witness_key IN (rotate_frozen_fixture.trust_key,rotate_frozen_fixture.witness_key)) THEN
        RAISE EXCEPTION 'key reuse';
    END IF;
    IF EXISTS (SELECT 1 FROM ei3.retired_rotation_epochs AS h
        WHERE h.key_epoch=new_epoch
        OR h.trust_digest IN (ei3_crypto.digest(trust_key,'sha256'),ei3_crypto.digest(witness_key,'sha256'))
        OR h.witness_digest IN (ei3_crypto.digest(trust_key,'sha256'),ei3_crypto.digest(witness_key,'sha256'))) THEN
        RAISE EXCEPTION 'retired epoch or key reuse';
    END IF;
    INSERT INTO ei3.retired_rotation_epochs
        SELECT k.key_epoch,ei3_crypto.digest(k.trust_key,'sha256'),ei3_crypto.digest(k.witness_key,'sha256')
        FROM ei3.keys AS k WHERE k.key_epoch=expected_epoch;
    INSERT INTO ei3.keys VALUES(new_epoch,trust_key,witness_key);
    UPDATE ei3.state SET key_epoch=new_epoch WHERE singleton;
    DELETE FROM ei3.keys WHERE key_epoch=expected_epoch;
END;
$$;
REVOKE ALL ON FUNCTION ei3.rotate_frozen_fixture(text,text,text,bytea,bytea) FROM PUBLIC;
COMMIT;
