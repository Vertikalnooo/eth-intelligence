-- Disposable OFFLINE fixture only. Never apply to a hosted database.
-- Execute as fixture administrator in one transaction after external quarantine.
-- No automatic unfreeze, login provisioning, or credential release.
BEGIN;
CREATE FUNCTION ei3.rotate_frozen_fixture(
    expected_epoch text, expected_checkpoint text, new_epoch text,
    trust_key bytea, witness_key bytea
) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, ei3, pg_temp
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
    INSERT INTO ei3.keys VALUES(new_epoch,trust_key,witness_key);
    UPDATE ei3.state SET key_epoch=new_epoch WHERE singleton;
    DELETE FROM ei3.keys WHERE key_epoch=expected_epoch;
END;
$$;
REVOKE ALL ON FUNCTION ei3.rotate_frozen_fixture(text,text,text,bytea,bytea) FROM PUBLIC;
COMMIT;
