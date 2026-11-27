-- Offline research schema; apply ONLY to disposable local PostgreSQL.
-- Explicit administrator provisioning; no application startup migration.
CREATE ROLE ei_runtime NOLOGIN;
CREATE SCHEMA ei;
REVOKE ALL ON SCHEMA ei FROM PUBLIC;
CREATE TABLE ei.system_state (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 store_id text NOT NULL CHECK(store_id ~ '^[0-9a-f]{32}$'),
 active text, frozen boolean NOT NULL DEFAULT false,
 next_at timestamptz NOT NULL DEFAULT '-infinity'
);
CREATE TABLE ei.grants (
 id text PRIMARY KEY CHECK(id ~ '^[0-9a-f]{32}$'),
 epoch text NOT NULL CHECK(epoch ~ '^[0-9a-f]{32}$'),
 candidate text NOT NULL CHECK(candidate ~ '^[0-9a-f]{40}$'),
 expires timestamptz NOT NULL,
 state text NOT NULL CHECK(state IN ('ISSUED','CONSUMED','REVOKED'))
);
CREATE TABLE ei.events (
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 id text NOT NULL REFERENCES ei.grants(id),
 kind text NOT NULL CHECK(kind IN ('ISSUED','CONSUMED','REVOKED','SUCCESS','UNKNOWN')),
 at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,kind)
);
CREATE FUNCTION ei.apply(s text, op text, g text, e text, c text)
RETURNS text LANGUAGE plpgsql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei AS $$
DECLARE st ei.system_state%ROWTYPE; n timestamptz; changed integer;
BEGIN
 IF s !~ '^[0-9a-f]{32}$' OR g !~ '^[0-9a-f]{32}$'
 OR e !~ '^[0-9a-f]{32}$' OR c !~ '^[0-9a-f]{40}$' THEN RETURN 'DENY'; END IF;
 SELECT * INTO st FROM ei.system_state WHERE singleton FOR UPDATE;
 IF NOT FOUND OR st.store_id <> s THEN RETURN 'DENY'; END IF;
 n := clock_timestamp();
 IF op='ISSUE' THEN
  IF st.frozen OR st.active IS NOT NULL OR n < st.next_at THEN RETURN 'DENY'; END IF;
  INSERT INTO ei.grants VALUES(g,e,c,n+interval '120 seconds','ISSUED')
   ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS changed=ROW_COUNT;
  IF changed<>1 THEN RETURN 'DENY'; END IF;
  INSERT INTO ei.events(id,kind) VALUES(g,'ISSUED');
 ELSIF op='CONSUME' THEN
  IF st.frozen OR st.active IS NOT NULL OR n < st.next_at THEN RETURN 'DENY'; END IF;
  UPDATE ei.grants SET state='CONSUMED' WHERE id=g AND epoch=e AND candidate=c
   AND state='ISSUED' AND n < expires;
  GET DIAGNOSTICS changed=ROW_COUNT;
  IF changed<>1 THEN RETURN 'DENY'; END IF;
  UPDATE ei.system_state SET active=g WHERE singleton;
  INSERT INTO ei.events(id,kind) VALUES(g,'CONSUMED');
 ELSIF op='REVOKE' THEN
  UPDATE ei.grants SET state='REVOKED' WHERE id=g AND epoch=e AND candidate=c AND state='ISSUED';
  GET DIAGNOSTICS changed=ROW_COUNT;
  IF changed<>1 THEN RETURN 'DENY'; END IF;
  INSERT INTO ei.events(id,kind) VALUES(g,'REVOKED');
 ELSIF op IN ('SUCCESS','UNKNOWN') THEN
  IF st.active IS DISTINCT FROM g OR NOT EXISTS
   (SELECT 1 FROM ei.grants WHERE id=g AND epoch=e AND candidate=c AND state='CONSUMED')
   THEN RETURN 'DENY'; END IF;
  INSERT INTO ei.events(id,kind) VALUES(g,op);
  UPDATE ei.system_state SET active=NULL, frozen=(op='UNKNOWN'),
   next_at=n+interval '35 seconds' WHERE singleton;
 ELSE RETURN 'DENY';
 END IF;
 RETURN 'OK';
END $$;
REVOKE ALL ON FUNCTION ei.apply(text,text,text,text,text) FROM PUBLIC;
GRANT USAGE ON SCHEMA ei TO ei_runtime;
GRANT EXECUTE ON FUNCTION ei.apply(text,text,text,text,text) TO ei_runtime;
-- No direct table access, no unfreeze/recovery function, no public issuer.
