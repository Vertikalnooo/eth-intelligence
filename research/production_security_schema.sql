-- Offline disposable PostgreSQL schema, never run on startup.
CREATE ROLE ei2_recovery NOLOGIN;
CREATE ROLE ei2_issuer NOLOGIN;
CREATE ROLE ei2_executor NOLOGIN;
CREATE ROLE ei2_finalizer NOLOGIN;
CREATE SCHEMA ei2;
REVOKE ALL ON SCHEMA ei2 FROM PUBLIC;
CREATE TABLE ei2.state (
 singleton boolean PRIMARY KEY CHECK(singleton),
 store_id text NOT NULL, checkpoint text NOT NULL, epoch text,
 candidate text, active text, mode text NOT NULL CHECK(mode IN ('DISARMED','ARMED','PENDING','CLOSED','FROZEN')),
 next_at timestamptz NOT NULL DEFAULT '-infinity'
);
CREATE TABLE ei2.grants (
 id text PRIMARY KEY, epoch text NOT NULL, candidate text NOT NULL,
 expires timestamptz NOT NULL, state text NOT NULL CHECK(state IN ('ISSUED','CONSUMED'))
);
CREATE TABLE ei2.events (
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, kind text NOT NULL,
 grant_id text, epoch text NOT NULL, checkpoint text NOT NULL UNIQUE,
 at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- Caller must maintain a separately held checkpoint, never silently adopt DB state.
-- One arm authorizes at most one grant/attempt. No recovery from PENDING/FROZEN
-- through application roles. Dedicated operator reconciliation is out of scope.
CREATE FUNCTION ei2.transition(op text,s text,oldcp text,newcp text,e text,c text,g text)
RETURNS text LANGUAGE plpgsql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei2 AS $$
DECLARE st ei2.state%ROWTYPE; n timestamptz; changed integer;
BEGIN
 IF s !~ '^[0-9a-f]{32}$' OR oldcp !~ '^[0-9a-f]{64}$' OR newcp !~ '^[0-9a-f]{64}$'
 OR oldcp=newcp OR e !~ '^[0-9a-f]{32}$' OR c !~ '^[0-9a-f]{40}$' OR g !~ '^[0-9a-f]{32}$'
 THEN RETURN 'DENY'; END IF;
 SELECT * INTO st FROM ei2.state WHERE singleton FOR UPDATE;
 IF NOT FOUND OR st.store_id<>s OR st.checkpoint<>oldcp THEN RETURN 'DENY'; END IF;
 n:=clock_timestamp();
 IF op='ARM' THEN
   IF st.mode NOT IN ('DISARMED','CLOSED') OR st.active IS NOT NULL OR n<st.next_at
       OR st.epoch IS NOT DISTINCT FROM e THEN RETURN 'DENY'; END IF;
   UPDATE ei2.state SET mode='ARMED',epoch=e,candidate=c WHERE singleton;
 ELSIF st.epoch IS DISTINCT FROM e OR st.candidate IS DISTINCT FROM c THEN RETURN 'DENY';
 ELSIF op='ISSUE' THEN
   IF st.mode<>'ARMED' OR st.active IS NOT NULL THEN RETURN 'DENY'; END IF;
   INSERT INTO ei2.grants VALUES(g,e,c,n+interval '120 seconds','ISSUED') ON CONFLICT DO NOTHING;
   GET DIAGNOSTICS changed=ROW_COUNT;
   IF changed<>1 THEN RETURN 'DENY'; END IF;
   UPDATE ei2.state SET active=g WHERE singleton;
 ELSIF op='CONSUME' THEN
   IF st.mode<>'ARMED' OR st.active IS DISTINCT FROM g THEN RETURN 'DENY'; END IF;
   UPDATE ei2.grants SET state='CONSUMED' WHERE id=g AND epoch=e AND candidate=c AND state='ISSUED' AND n<expires;
   GET DIAGNOSTICS changed=ROW_COUNT;
   IF changed<>1 THEN RETURN 'DENY'; END IF;
   UPDATE ei2.state SET mode='PENDING' WHERE singleton;
 ELSIF op IN ('SUCCESS','UNKNOWN') THEN
   IF st.mode<>'PENDING' OR st.active IS DISTINCT FROM g THEN RETURN 'DENY'; END IF;
   UPDATE ei2.state SET mode=CASE WHEN op='SUCCESS' THEN 'CLOSED' ELSE 'FROZEN' END,
    active=NULL,next_at=n+interval '900 seconds' WHERE singleton;
 ELSE RETURN 'DENY';
 END IF;
 UPDATE ei2.state SET checkpoint=newcp WHERE singleton;
 INSERT INTO ei2.events(kind,grant_id,epoch,checkpoint) VALUES(op,g,e,newcp);
 RETURN 'OK';
END $$;
REVOKE ALL ON FUNCTION ei2.transition(text,text,text,text,text,text,text) FROM PUBLIC;
CREATE FUNCTION ei2.arm(s text,p text,q text,e text,c text,g text)
RETURNS text LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei2
AS $$ SELECT ei2.transition('ARM',s,p,q,e,c,g) $$;
REVOKE ALL ON FUNCTION ei2.arm(text,text,text,text,text,text) FROM PUBLIC;
GRANT USAGE ON SCHEMA ei2 TO ei2_recovery;
GRANT EXECUTE ON FUNCTION ei2.arm(text,text,text,text,text,text) TO ei2_recovery;
CREATE FUNCTION ei2.issue(s text,p text,q text,e text,c text,g text)
RETURNS text LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei2
AS $$ SELECT ei2.transition('ISSUE',s,p,q,e,c,g) $$;
REVOKE ALL ON FUNCTION ei2.issue(text,text,text,text,text,text) FROM PUBLIC;
GRANT USAGE ON SCHEMA ei2 TO ei2_issuer;
GRANT EXECUTE ON FUNCTION ei2.issue(text,text,text,text,text,text) TO ei2_issuer;
CREATE FUNCTION ei2.consume(s text,p text,q text,e text,c text,g text)
RETURNS text LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei2
AS $$ SELECT ei2.transition('CONSUME',s,p,q,e,c,g) $$;
REVOKE ALL ON FUNCTION ei2.consume(text,text,text,text,text,text) FROM PUBLIC;
GRANT USAGE ON SCHEMA ei2 TO ei2_executor;
GRANT EXECUTE ON FUNCTION ei2.consume(text,text,text,text,text,text) TO ei2_executor;
CREATE FUNCTION ei2.success(s text,p text,q text,e text,c text,g text)
RETURNS text LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei2
AS $$ SELECT ei2.transition('SUCCESS',s,p,q,e,c,g) $$;
REVOKE ALL ON FUNCTION ei2.success(text,text,text,text,text,text) FROM PUBLIC;
GRANT USAGE ON SCHEMA ei2 TO ei2_finalizer;
GRANT EXECUTE ON FUNCTION ei2.success(text,text,text,text,text,text) TO ei2_finalizer;
CREATE FUNCTION ei2.unknown(s text,p text,q text,e text,c text,g text)
RETURNS text LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei2
AS $$ SELECT ei2.transition('UNKNOWN',s,p,q,e,c,g) $$;
REVOKE ALL ON FUNCTION ei2.unknown(text,text,text,text,text,text) FROM PUBLIC;
GRANT USAGE ON SCHEMA ei2 TO ei2_finalizer;
GRANT EXECUTE ON FUNCTION ei2.unknown(text,text,text,text,text,text) TO ei2_finalizer;
-- No grants on state/events/grants, no runtime reset, no general transition access.
