-- Research ONLY. Fresh disposable PG17 database, explicit admin installation.
-- NO state, keys, grants, enrollment, production bootstrap or automatic DDL.
BEGIN;
CREATE ROLE ei3_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE ei3_arm NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE ei3_issue NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE ei3_consume NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE ei3_finalize NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE ei3_advance NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE ei3_audit NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE SCHEMA ei3_crypto AUTHORIZATION ei3_owner;
CREATE EXTENSION pgcrypto WITH SCHEMA ei3_crypto;
REVOKE ALL ON SCHEMA ei3_crypto FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ei3_crypto FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ei3_crypto.hmac(bytea,bytea,text),ei3_crypto.digest(bytea,text) TO ei3_owner;
CREATE SCHEMA ei3 AUTHORIZATION ei3_owner;
REVOKE ALL ON SCHEMA ei3 FROM PUBLIC;
SET LOCAL ROLE ei3_owner;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
CREATE TABLE ei3.keys (
 key_epoch text PRIMARY KEY CHECK(key_epoch COLLATE "C" ~ '^[0-9a-f]{32}$'),
 trust_key bytea NOT NULL CHECK(octet_length(trust_key)=32 AND trust_key<>decode(repeat('00',32),'hex')),
 witness_key bytea NOT NULL CHECK(octet_length(witness_key)=32 AND witness_key<>decode(repeat('00',32),'hex')),
 CHECK(trust_key<>witness_key)
);
CREATE TABLE ei3.state (
 singleton boolean PRIMARY KEY CHECK(singleton),
 environment text NOT NULL CHECK(environment='OFFLINE'),
 ledger text NOT NULL, object_id text NOT NULL, candidate text NOT NULL,
 key_epoch text NOT NULL REFERENCES ei3.keys,
 checkpoint text NOT NULL, owner_head text NOT NULL,
 credential_revision bigint NOT NULL CHECK(credential_revision>=0),
 trust_sequence bigint NOT NULL CHECK(trust_sequence>=0),
 witness_sequence bigint NOT NULL CHECK(witness_sequence>=0),
 last_auth text NOT NULL DEFAULT repeat('0',32),
 mode text NOT NULL CHECK(mode IN ('READY','ARMED','ISSUED','PENDING','CLOSED','FROZEN','ADVANCED')),
 plan text, grant_id text, epoch text, next_at timestamptz NOT NULL DEFAULT '-infinity',
 terminal_evidence text
);
CREATE TABLE ei3.grants (
 id text PRIMARY KEY, plan text NOT NULL, epoch text NOT NULL,
 expires_at timestamptz NOT NULL, state text NOT NULL CHECK(state IN ('ISSUED','CONSUMED'))
);
CREATE TABLE ei3.events (
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 authorization_id text NOT NULL UNIQUE, checkpoint text NOT NULL UNIQUE,
 op text NOT NULL, plan text NOT NULL, grant_id text NOT NULL,
 body_digest bytea NOT NULL, evidence text NOT NULL, owner_head text NOT NULL,
 credential_revision bigint NOT NULL, trust_sequence bigint NOT NULL,witness_sequence bigint NOT NULL,
 at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ei3.spent_checkpoints (checkpoint text PRIMARY KEY);
-- Fixed iteration, no early exit for equal-length 32-byte MACs. Not a proven
-- constant-time primitive on PostgreSQL's interpreter; timing audit pending.
CREATE FUNCTION ei3.mac_equal(a bytea,b bytea) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog,pg_temp AS $$
DECLARE diff integer:=0; i integer;
BEGIN
 IF octet_length(a)<>32 OR octet_length(b)<>32 THEN RETURN false; END IF;
 FOR i IN 0..31 LOOP diff:=diff | (get_byte(a,i) # get_byte(b,i)); END LOOP;
 RETURN diff=0;
END $$;
CREATE FUNCTION ei3.frame(f text[]) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog,pg_temp AS $$
DECLARE s text:=''; i integer;
BEGIN
 IF array_ndims(f) IS DISTINCT FROM 1 OR array_lower(f,1) IS DISTINCT FROM 1 OR cardinality(f)<>24 THEN RETURN NULL; END IF;
 IF array_position(f,NULL) IS NOT NULL THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM unnest(f) AS t(v) WHERE octet_length(v)>64) THEN RETURN NULL; END IF;
 IF f[1] <> 'EI-CAP-3'
 OR f[2] <> 'OFFLINE'
 OR f[3] COLLATE "C" !~ '^[0-9a-f]{32}$'
 OR f[4] COLLATE "C" !~ '^[0-9a-f]{64}$'
 OR f[5] COLLATE "C" !~ '^[0-9a-f]{40}$'
 OR f[6] COLLATE "C" !~ '^[0-9a-f]{32}$'
 OR f[7] NOT IN ('ARM','ISSUE','CONSUME','SUCCESS','UNKNOWN','ADVANCE')
 OR f[8] COLLATE "C" !~ '^[0-9a-f]{32}$'
 OR f[9] COLLATE "C" !~ '^[0-9a-f]{32}$'
 OR f[10] COLLATE "C" !~ '^[0-9a-f]{32}$'
 OR f[11] COLLATE "C" !~ '^[0-9a-f]{32}$'
 OR f[12] COLLATE "C" !~ '^[0-9a-f]{64}$'
 OR f[13] COLLATE "C" !~ '^[0-9a-f]{64}$'
 OR f[14] COLLATE "C" !~ '^[0-9a-f]{64}$'
 OR f[15] COLLATE "C" !~ '^[0-9a-f]{64}$'
 OR f[16] COLLATE "C" !~ '^(0|[1-9][0-9]{0,15})$'
 OR f[17] COLLATE "C" !~ '^(0|[1-9][0-9]{0,15})$'
 OR f[18] COLLATE "C" !~ '^(0|[1-9][0-9]{0,15})$'
 OR f[19] COLLATE "C" !~ '^[0-9a-f]{64}$'
 OR f[20] COLLATE "C" !~ '^[0-9a-f]{32}$'
 OR f[21] <> 'SYNTHETIC_ONLY'
 OR f[22] COLLATE "C" !~ '^(0|[1-9][0-9]{0,15})$'
 OR f[23] COLLATE "C" !~ '^(0|[1-9][0-9]{0,15})$'
 OR f[24] COLLATE "C" !~ '^(0|[1-9][0-9]{0,15})$' THEN RETURN NULL; END IF;
 IF f[16]::bigint>9007199254740991 OR f[17]::bigint>9007199254740991 OR f[18]::bigint>9007199254740991
 OR f[22]::bigint>9007199254740991 OR f[23]::bigint>9007199254740991 OR f[24]::bigint>9007199254740991 THEN RETURN NULL; END IF;
 FOR i IN 1..24 LOOP s:=s||octet_length(f[i])::text||':'||f[i]; END LOOP;
 RETURN convert_to(s,'UTF8');
END $$;
CREATE FUNCTION ei3.apply_transition(expected text, login_role name, f text[], body bytea, mt bytea,mw bytea)
RETURNS text LANGUAGE plpgsql STRICT SECURITY DEFINER
SET search_path=pg_catalog,ei3,pg_temp SET synchronous_commit=on AS $$
DECLARE st ei3.state%ROWTYPE; k ei3.keys%ROWTYPE; b bytea; t boolean; w boolean;
 n timestamptz; seconds bigint; cr bigint; ts bigint; ws bigint; op text; gr ei3.grants%ROWTYPE;
BEGIN
 -- Exact session identity, not caller-controlled SET ROLE. Wrappers hardcode role.
 IF session_user<>login_role OR octet_length(body)>4096 OR octet_length(mt)<>32 OR octet_length(mw)<>32 THEN RETURN 'DENY'; END IF;
 b:=ei3.frame(f); IF b IS NULL OR b<>body OR f[7]<>expected THEN RETURN 'DENY'; END IF;
 op:=f[7]; cr:=f[16]::bigint;ts:=f[17]::bigint;ws:=f[18]::bigint;
 IF f[8]=repeat('0',32) OR f[9]=repeat('0',32) OR f[10]=repeat('0',32) OR f[11]=repeat('0',32)
 OR f[12]=f[13] OR f[13]=repeat('0',64) OR f[14]=f[15] OR f[15]=repeat('0',64) OR f[19]=repeat('0',64)
 OR f[22]::bigint>f[23]::bigint OR f[24]::bigint<=f[23]::bigint OR f[24]::bigint-f[22]::bigint>30 THEN RETURN 'DENY'; END IF;
 SELECT * INTO st FROM ei3.state WHERE singleton FOR UPDATE;
 IF NOT FOUND OR st.mode='FROZEN' OR st.environment<>f[2] OR st.ledger<>f[3] OR st.object_id<>f[4]
 OR st.candidate<>f[5] OR st.key_epoch<>f[6] OR st.checkpoint<>f[12] OR st.owner_head<>f[14]
 OR st.last_auth<>f[20] OR ts<>st.trust_sequence+1 OR ws<>st.witness_sequence+1 THEN RETURN 'DENY'; END IF;
 SELECT * INTO k FROM ei3.keys WHERE key_epoch=st.key_epoch;
 IF NOT FOUND THEN RETURN 'DENY'; END IF;
 t:=ei3.mac_equal(mt,ei3_crypto.hmac(convert_to('EI-CAP-3/TRUST'||chr(10),'UTF8')||b,k.trust_key,'sha256'));
 w:=ei3.mac_equal(mw,ei3_crypto.hmac(convert_to('EI-CAP-3/WITNESS'||chr(10),'UTF8')||b,k.witness_key,'sha256'));
 IF NOT (t AND w) THEN RETURN 'DENY'; END IF;
 -- Wall clock evaluated AFTER lock and MAC, not transaction start. No grace past expiry.
 n:=clock_timestamp();seconds:=floor(extract(epoch FROM n))::bigint;
 IF f[22]::bigint>seconds+5 OR f[23]::bigint>seconds OR f[24]::bigint<=seconds THEN RETURN 'DENY'; END IF;
 IF cr<>st.credential_revision+CASE WHEN op IN ('ARM','ISSUE','CONSUME','ADVANCE') THEN 1 ELSE 0 END THEN RETURN 'DENY'; END IF;
 IF EXISTS(SELECT 1 FROM ei3.events WHERE authorization_id=f[8] OR checkpoint=f[13])
 OR EXISTS(SELECT 1 FROM ei3.spent_checkpoints WHERE checkpoint IN (f[12],f[13])) THEN RETURN 'DENY'; END IF;
 IF op='ARM' THEN
   IF st.mode NOT IN ('READY','ADVANCED') OR n<st.next_at OR st.epoch IS NOT DISTINCT FROM f[11] OR st.plan IS NOT DISTINCT FROM f[9]
   OR EXISTS(SELECT 1 FROM ei3.grants WHERE id=f[10]) THEN RETURN 'DENY'; END IF;
 ELSE
   IF st.plan IS DISTINCT FROM f[9] OR st.grant_id IS DISTINCT FROM f[10] OR st.epoch IS DISTINCT FROM f[11] THEN RETURN 'DENY'; END IF;
   IF op='ISSUE' THEN
     IF st.mode<>'ARMED' OR EXISTS(SELECT 1 FROM ei3.grants WHERE id=f[10]) THEN RETURN 'DENY'; END IF;
   ELSIF op='CONSUME' THEN
     SELECT * INTO gr FROM ei3.grants WHERE id=f[10];
     IF NOT FOUND OR st.mode<>'ISSUED' OR gr.plan<>f[9] OR gr.epoch<>f[11] OR gr.state<>'ISSUED' OR n>=gr.expires_at THEN RETURN 'DENY'; END IF;
   ELSIF op IN ('SUCCESS','UNKNOWN') THEN
     SELECT * INTO gr FROM ei3.grants WHERE id=f[10];
     IF NOT FOUND OR st.mode<>'PENDING' OR gr.plan<>f[9] OR gr.epoch<>f[11] OR gr.state<>'CONSUMED' THEN RETURN 'DENY'; END IF;
   ELSIF op='ADVANCE' THEN
     IF st.mode<>'CLOSED' OR st.terminal_evidence IS DISTINCT FROM f[19] THEN RETURN 'DENY'; END IF;
   ELSE RETURN 'DENY'; END IF;
 END IF;
 -- No DENY return after first mutation: any failure aborts the entire transaction.
 INSERT INTO ei3.spent_checkpoints VALUES(f[12]);
 INSERT INTO ei3.events(authorization_id,checkpoint,op,plan,grant_id,body_digest,evidence,owner_head,credential_revision,trust_sequence,witness_sequence)
 VALUES(f[8],f[13],op,f[9],f[10],ei3_crypto.digest(b,'sha256'),f[19],f[15],cr,ts,ws);
 IF op='ARM' THEN
   UPDATE ei3.state SET mode='ARMED',plan=f[9],grant_id=f[10],epoch=f[11],terminal_evidence=NULL WHERE singleton;
 ELSIF op='ISSUE' THEN
   INSERT INTO ei3.grants VALUES(f[10],f[9],f[11],n+interval '120 seconds','ISSUED');
   UPDATE ei3.state SET mode='ISSUED' WHERE singleton;
 ELSIF op='CONSUME' THEN
   UPDATE ei3.grants SET state='CONSUMED' WHERE id=f[10];
   UPDATE ei3.state SET mode='PENDING' WHERE singleton;
 ELSIF op IN ('SUCCESS','UNKNOWN') THEN
   UPDATE ei3.state SET mode=CASE WHEN op='SUCCESS' THEN 'CLOSED' ELSE 'FROZEN' END,terminal_evidence=f[19],next_at=n+interval '900 seconds' WHERE singleton;
 ELSIF op='ADVANCE' THEN UPDATE ei3.state SET mode='ADVANCED' WHERE singleton;
 END IF;
 UPDATE ei3.state SET checkpoint=f[13],owner_head=f[15],credential_revision=cr,trust_sequence=ts,witness_sequence=ws,last_auth=f[8] WHERE singleton;
 RETURN 'OK';
END $$;
CREATE FUNCTION ei3.arm(f text[],body bytea,mt bytea,mw bytea) RETURNS text
LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei3,pg_temp SET synchronous_commit=on
AS $$ SELECT ei3.apply_transition('ARM','ei3_arm',f,body,mt,mw) $$;
GRANT EXECUTE ON FUNCTION ei3.arm(text[],bytea,bytea,bytea) TO ei3_arm;
CREATE FUNCTION ei3.issue(f text[],body bytea,mt bytea,mw bytea) RETURNS text
LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei3,pg_temp SET synchronous_commit=on
AS $$ SELECT ei3.apply_transition('ISSUE','ei3_issue',f,body,mt,mw) $$;
GRANT EXECUTE ON FUNCTION ei3.issue(text[],bytea,bytea,bytea) TO ei3_issue;
CREATE FUNCTION ei3.consume(f text[],body bytea,mt bytea,mw bytea) RETURNS text
LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei3,pg_temp SET synchronous_commit=on
AS $$ SELECT ei3.apply_transition('CONSUME','ei3_consume',f,body,mt,mw) $$;
GRANT EXECUTE ON FUNCTION ei3.consume(text[],bytea,bytea,bytea) TO ei3_consume;
CREATE FUNCTION ei3.success(f text[],body bytea,mt bytea,mw bytea) RETURNS text
LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei3,pg_temp SET synchronous_commit=on
AS $$ SELECT ei3.apply_transition('SUCCESS','ei3_finalize',f,body,mt,mw) $$;
GRANT EXECUTE ON FUNCTION ei3.success(text[],bytea,bytea,bytea) TO ei3_finalize;
CREATE FUNCTION ei3.unknown(f text[],body bytea,mt bytea,mw bytea) RETURNS text
LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei3,pg_temp SET synchronous_commit=on
AS $$ SELECT ei3.apply_transition('UNKNOWN','ei3_finalize',f,body,mt,mw) $$;
GRANT EXECUTE ON FUNCTION ei3.unknown(text[],bytea,bytea,bytea) TO ei3_finalize;
CREATE FUNCTION ei3.advance(f text[],body bytea,mt bytea,mw bytea) RETURNS text
LANGUAGE sql STRICT SECURITY DEFINER SET search_path=pg_catalog,ei3,pg_temp SET synchronous_commit=on
AS $$ SELECT ei3.apply_transition('ADVANCE','ei3_advance',f,body,mt,mw) $$;
GRANT EXECUTE ON FUNCTION ei3.advance(text[],bytea,bytea,bytea) TO ei3_advance;
CREATE VIEW ei3.audit_state AS SELECT environment,ledger,object_id,candidate,key_epoch,checkpoint,owner_head,credential_revision,trust_sequence,witness_sequence,last_auth,mode,plan,grant_id,epoch,next_at,terminal_evidence FROM ei3.state;
GRANT SELECT ON ei3.audit_state,ei3.events,ei3.grants,ei3.spent_checkpoints TO ei3_audit;
GRANT USAGE ON SCHEMA ei3 TO ei3_arm,ei3_issue,ei3_consume,ei3_finalize,ei3_advance,ei3_audit;
REVOKE ALL ON ALL TABLES IN SCHEMA ei3 FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ei3 FROM PUBLIC;
RESET ROLE;
COMMIT;
