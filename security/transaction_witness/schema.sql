-- DISPOSABLE EXPERIMENT ONLY. Not a production migration or approval issuer.
-- A separately committed owner reservation models an EXTERNAL authority root.
CREATE SCHEMA financial_witness;
CREATE TABLE financial_witness.principals(principal_name name PRIMARY KEY);
CREATE TABLE financial_witness.policy(id int PRIMARY KEY CHECK(id=1),generation bigint NOT NULL CHECK(generation>0),revoked boolean NOT NULL);
INSERT INTO financial_witness.policy VALUES(1,1,false);
CREATE TABLE financial_witness.claims(
 nonce uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 authorization_sha256 text NOT NULL UNIQUE REFERENCES public.paper_startup_executions(authorization_sha256),
 job_run_id uuid NOT NULL UNIQUE, principal_name name NOT NULL,
 transaction_id xid8 NOT NULL UNIQUE,backend_pid int NOT NULL,backend_start timestamptz NOT NULL,
 database_oid oid NOT NULL,cluster_id text NOT NULL,generation bigint NOT NULL,
 expires_at bigint NOT NULL,outcome text NOT NULL CHECK(outcome IN ('FILLED','NO_TRADE','RISK_BLOCKED'))
);
CREATE TABLE financial_witness.envelopes(
 nonce uuid PRIMARY KEY REFERENCES financial_witness.claims(nonce),
 transaction_id xid8 NOT NULL UNIQUE,principal_name name NOT NULL
);
CREATE TABLE financial_witness.events(
 event_no bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 nonce uuid NOT NULL REFERENCES financial_witness.envelopes(nonce),
 relation_name text NOT NULL,operation text NOT NULL CHECK(operation IN ('INSERT','UPDATE','DELETE')),
 old_row jsonb,new_row jsonb,transaction_id xid8 NOT NULL
);
CREATE TABLE financial_witness.approvals(
 nonce uuid PRIMARY KEY REFERENCES financial_witness.claims(nonce),
 expected_events jsonb NOT NULL CHECK(jsonb_typeof(expected_events)='array' AND octet_length(expected_events::text)<=1048576),
 signed_response_sha256 text NOT NULL CHECK(signed_response_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE TABLE financial_witness.seals(
 nonce uuid PRIMARY KEY REFERENCES financial_witness.envelopes(nonce),
 transaction_id xid8 NOT NULL,event_count int NOT NULL CHECK(event_count BETWEEN 1 AND 128),
 events_sha256 text NOT NULL CHECK(events_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE FUNCTION financial_witness.immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'WRITE_SET_AUDIT_IMMUTABLE' USING ERRCODE='23514'; END $$;
CREATE FUNCTION financial_witness.owner_only() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF current_user IS DISTINCT FROM session_user OR current_user IS DISTINCT FROM
  (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='financial_witness') THEN
  RAISE EXCEPTION 'WRITE_SET_EXTERNAL_OWNER_REQUIRED' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER policy_owner BEFORE INSERT OR UPDATE ON financial_witness.policy FOR EACH ROW EXECUTE FUNCTION financial_witness.owner_only();
CREATE TRIGGER policy_delete BEFORE DELETE ON financial_witness.policy FOR EACH ROW EXECUTE FUNCTION financial_witness.immutable();
CREATE TRIGGER policy_truncate BEFORE TRUNCATE ON financial_witness.policy FOR EACH STATEMENT EXECUTE FUNCTION financial_witness.immutable();
CREATE FUNCTION financial_witness.internal_only() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF current_user IS DISTINCT FROM (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='financial_witness')
  OR NEW.transaction_id IS DISTINCT FROM pg_current_xact_id() THEN
  RAISE EXCEPTION 'WRITE_SET_INTERNAL_WRITER_REQUIRED' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['principals','claims','envelopes','events','approvals','seals'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON financial_witness.%I FOR EACH ROW EXECUTE FUNCTION financial_witness.immutable()',t);
  EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON financial_witness.%I FOR EACH STATEMENT EXECUTE FUNCTION financial_witness.immutable()',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['principals','claims','approvals'] LOOP
  EXECUTE format('CREATE TRIGGER owner_insert BEFORE INSERT ON financial_witness.%I FOR EACH ROW EXECUTE FUNCTION financial_witness.owner_only()',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['envelopes','events','seals'] LOOP
  EXECUTE format('CREATE TRIGGER internal_insert BEFORE INSERT ON financial_witness.%I FOR EACH ROW EXECUTE FUNCTION financial_witness.internal_only()',t);
 END LOOP;
END $$;
CREATE FUNCTION financial_witness.claim_binding() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE actual record; authority record;
BEGIN
 SELECT usename,backend_start,backend_xid::text AS xid,datid INTO actual
 FROM pg_stat_activity WHERE pid=NEW.backend_pid;
 SELECT * INTO authority FROM public.paper_startup_executions WHERE authorization_sha256=NEW.authorization_sha256;
 IF actual IS NULL OR authority IS NULL OR actual.xid IS NULL OR actual.usename<>NEW.principal_name
  OR actual.backend_start<>NEW.backend_start OR actual.datid<>NEW.database_oid
  OR actual.xid::numeric<>mod(NEW.transaction_id::text::numeric,4294967296)
  OR NEW.cluster_id<>(SELECT system_identifier::text FROM pg_control_system())
  OR NEW.database_oid<>(SELECT oid FROM pg_database WHERE datname=current_database())
  OR authority.database_principal<>NEW.principal_name OR authority.job_run_id<>NEW.job_run_id
  -- This reservation has its own explicitly modeled authority deadline. The
  -- initial execution row's short observation is not a renewable authority:
  -- existing public COMMIT guards still require the fresh closing witness.
  OR NEW.expires_at>authority.expires_at
  OR NEW.expires_at>extract(epoch FROM clock_timestamp())+15
  OR NEW.expires_at<=extract(epoch FROM clock_timestamp())
  OR NEW.generation<>authority.observed_policy_generation THEN
  RAISE EXCEPTION 'WRITE_SET_CLAIM_BINDING_INVALID' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER validate_claim BEFORE INSERT ON financial_witness.claims FOR EACH ROW EXECUTE FUNCTION financial_witness.claim_binding();
CREATE FUNCTION financial_witness.bound_claim(_nonce uuid) RETURNS financial_witness.claims
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,financial_witness AS $$
DECLARE c financial_witness.claims; started timestamptz;
BEGIN
 SELECT * INTO c FROM financial_witness.claims WHERE nonce=_nonce;
 SELECT backend_start INTO started FROM pg_stat_activity WHERE pid=pg_backend_pid();
 IF c IS NULL OR current_setting('role')<>'none' OR c.principal_name<>session_user
  OR c.transaction_id<>pg_current_xact_id() OR c.backend_pid<>pg_backend_pid() OR c.backend_start<>started
  OR c.database_oid<>(SELECT oid FROM pg_database WHERE datname=current_database())
  OR c.cluster_id<>(SELECT system_identifier::text FROM pg_control_system()) THEN
  RAISE EXCEPTION 'WRITE_SET_SESSION_MISMATCH' USING ERRCODE='23514';
 END IF;
 IF extract(epoch FROM clock_timestamp())>=c.expires_at THEN
  RAISE EXCEPTION 'WRITE_SET_AUTHORITY_EXPIRED' USING ERRCODE='23514';
 END IF;
 RETURN c;
END $$;
CREATE FUNCTION financial_witness.open_envelope(_nonce uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,financial_witness AS $$
DECLARE c financial_witness.claims;
BEGIN
 c:=financial_witness.bound_claim(_nonce);
 IF NOT EXISTS(SELECT 1 FROM financial_witness.principals WHERE principal_name=session_user) THEN
  RAISE EXCEPTION 'WRITE_SET_PRINCIPAL_UNREGISTERED' USING ERRCODE='23514';
 END IF;
 INSERT INTO financial_witness.envelopes VALUES(_nonce,pg_current_xact_id(),session_user);
END $$;
CREATE FUNCTION financial_witness.snapshot(_nonce uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,financial_witness AS $$
DECLARE result jsonb;
BEGIN
 PERFORM financial_witness.bound_claim(_nonce);
 SELECT coalesce(jsonb_agg(jsonb_build_object('relation',relation_name,'operation',operation,
  'before',old_row,'after',new_row) ORDER BY event_no),'[]'::jsonb) INTO result
 FROM financial_witness.events WHERE nonce=_nonce;
 IF jsonb_array_length(result)>128 OR octet_length(result::text)>1048576 THEN
  RAISE EXCEPTION 'WRITE_SET_BOUNDS_EXCEEDED' USING ERRCODE='23514';
 END IF;
 RETURN result;
END $$;
CREATE FUNCTION financial_witness.digest(_events jsonb) RETURNS text LANGUAGE sql IMMUTABLE
 SET search_path=pg_catalog AS $$ SELECT encode(sha256(convert_to(_events::text,'UTF8')),'hex') $$;
CREATE FUNCTION financial_witness.challenge(_nonce uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,financial_witness AS $$
DECLARE c financial_witness.claims;
BEGIN
 c:=financial_witness.bound_claim(_nonce);
 RETURN to_jsonb(c);
END $$;
CREATE FUNCTION financial_witness.capture() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,financial_witness AS $$
DECLARE target uuid; old_value jsonb; new_value jsonb;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM financial_witness.principals WHERE principal_name=session_user) THEN
  IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
 END IF;
 SELECT nonce INTO target FROM financial_witness.envelopes WHERE transaction_id=pg_current_xact_id() AND principal_name=session_user;
 IF target IS NULL THEN
  IF TG_TABLE_NAME IN ('job_runs','audit_events') THEN
   IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
  END IF;
  RAISE EXCEPTION 'WRITE_SET_ENVELOPE_REQUIRED' USING ERRCODE='23514';
 END IF;
 IF TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'WRITE_SET_TRUNCATE_FORBIDDEN' USING ERRCODE='23514'; END IF;
 PERFORM financial_witness.bound_claim(target);
 IF TG_OP<>'INSERT' THEN old_value:=to_jsonb(OLD); END IF;
 IF TG_OP<>'DELETE' THEN new_value:=to_jsonb(NEW); END IF;
 IF coalesce(octet_length(old_value::text),0)+coalesce(octet_length(new_value::text),0)>131072
  OR (SELECT count(*) FROM financial_witness.events WHERE nonce=target)>=128 THEN
  RAISE EXCEPTION 'WRITE_SET_BOUNDS_EXCEEDED' USING ERRCODE='23514';
 END IF;
 INSERT INTO financial_witness.events(nonce,relation_name,operation,old_row,new_row,transaction_id)
 VALUES(target,TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,TG_OP,old_value,new_value,pg_current_xact_id());
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
CREATE FUNCTION financial_witness.seal(_nonce uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,financial_witness AS $$
DECLARE actual jsonb; expected jsonb;
BEGIN
 actual:=financial_witness.snapshot(_nonce);
 SELECT expected_events INTO expected FROM financial_witness.approvals WHERE nonce=_nonce;
 IF expected IS NULL THEN RAISE EXCEPTION 'WRITE_SET_INDEPENDENT_APPROVAL_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF actual IS DISTINCT FROM expected OR jsonb_array_length(actual)=0 THEN
  RAISE EXCEPTION 'WRITE_SET_MANIFEST_MISMATCH' USING ERRCODE='23514';
 END IF;
 INSERT INTO financial_witness.seals VALUES(_nonce,pg_current_xact_id(),jsonb_array_length(actual),financial_witness.digest(actual));
END $$;
CREATE FUNCTION financial_witness.verify_commit() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,financial_witness AS $$
DECLARE c financial_witness.claims; s financial_witness.seals; p financial_witness.policy;
 actual jsonb; expected jsonb; state_now text;
BEGIN
 IF lower(btrim(current_query())) NOT IN ('commit','commit;') THEN
  RAISE EXCEPTION 'WRITE_SET_ACTUAL_COMMIT_REQUIRED' USING ERRCODE='23514';
 END IF;
 c:=financial_witness.bound_claim(NEW.nonce);
 SELECT * INTO p FROM financial_witness.policy WHERE id=1 FOR SHARE;
 IF p IS NULL OR p.revoked OR p.generation<>c.generation THEN
  RAISE EXCEPTION 'WRITE_SET_POLICY_REVOKED' USING ERRCODE='23514';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtext('hope:paper:environment-control')::bigint);
 SELECT state INTO state_now FROM public.paper_environment_control_events ORDER BY control_sequence DESC LIMIT 1 FOR SHARE;
 IF state_now IS DISTINCT FROM 'RUNNING' THEN RAISE EXCEPTION 'WRITE_SET_HALTED' USING ERRCODE='23514'; END IF;
 SELECT * INTO s FROM financial_witness.seals WHERE nonce=NEW.nonce;
 IF s IS NULL THEN RAISE EXCEPTION 'WRITE_SET_SEAL_MISSING' USING ERRCODE='23514'; END IF;
 actual:=financial_witness.snapshot(NEW.nonce);
 SELECT expected_events INTO expected FROM financial_witness.approvals WHERE nonce=NEW.nonce;
 IF s.transaction_id<>pg_current_xact_id() OR s.event_count<>jsonb_array_length(actual)
  OR s.events_sha256<>financial_witness.digest(actual) OR actual IS DISTINCT FROM expected THEN
  RAISE EXCEPTION 'WRITE_SET_SEAL_MISMATCH' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.job_runs j JOIN public.paper_certified_outcomes o USING(job_run_id)
  JOIN public.paper_startup_financial_transactions t ON t.authorization_sha256=c.authorization_sha256
  JOIN public.paper_startup_commit_witnesses w ON w.authorization_sha256=t.authorization_sha256
  WHERE j.job_run_id=c.job_run_id AND j.status='SUCCEEDED' AND o.outcome=c.outcome
   AND t.transaction_id=pg_current_xact_id() AND w.financial_transaction_id=t.transaction_id) THEN
  RAISE EXCEPTION 'WRITE_SET_FINANCIAL_LIFECYCLE_INCOMPLETE' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER actual_commit AFTER INSERT ON financial_witness.envelopes
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION financial_witness.verify_commit();
CREATE CONSTRAINT TRIGGER actual_commit AFTER INSERT ON financial_witness.events
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION financial_witness.verify_commit();
CREATE CONSTRAINT TRIGGER actual_commit AFTER INSERT ON financial_witness.seals
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION financial_witness.verify_commit();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['signals','orders','fills','paper_effects','paper_portfolios','paper_portfolio_positions',
  'paper_portfolio_fill_applications','paper_portfolio_pnl_events','paper_risk_assessments','paper_order_terminal_events',
  'paper_no_trade_decisions','paper_strategy_authorizations','paper_certified_outcomes','job_runs','audit_events',
  'paper_startup_financial_transactions','paper_startup_commit_witnesses'] LOOP
  EXECUTE format('CREATE TRIGGER witness_capture AFTER INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION financial_witness.capture()',t);
  EXECUTE format('CREATE TRIGGER witness_no_truncate BEFORE TRUNCATE ON public.%I FOR EACH STATEMENT EXECUTE FUNCTION financial_witness.capture()',t);
 END LOOP;
END $$;
REVOKE ALL ON SCHEMA financial_witness FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA financial_witness FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA financial_witness FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA financial_witness FROM PUBLIC;
