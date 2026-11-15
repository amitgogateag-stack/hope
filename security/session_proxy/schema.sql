-- DISPOSABLE PROTOTYPE ONLY. Not a HOPE migration or production grant.
CREATE SCHEMA session_prototype;
CREATE TABLE session_prototype.control (
 id integer PRIMARY KEY CHECK(id=1), halted boolean NOT NULL,
 revoked boolean NOT NULL, generation bigint NOT NULL CHECK(generation>0)
);
INSERT INTO session_prototype.control VALUES(1,false,false,1);
CREATE TABLE session_prototype.approvals (
 nonce text PRIMARY KEY, transcript jsonb NOT NULL,
 backend_pid integer NOT NULL DEFAULT pg_backend_pid(),
 transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
 login_name text NOT NULL DEFAULT session_user,
 expires_at timestamptz NOT NULL, generation bigint NOT NULL
);
CREATE TABLE session_prototype.effects (
 nonce text PRIMARY KEY REFERENCES session_prototype.approvals(nonce),
 amount integer NOT NULL CHECK(amount=1)
);
CREATE FUNCTION session_prototype.check_commit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,session_prototype AS $$
DECLARE a session_prototype.approvals; c session_prototype.control;
BEGIN
 IF lower(btrim(current_query())) NOT IN ('commit','commit;') THEN
  RAISE EXCEPTION 'PROTOTYPE_COMMIT_CONTEXT' USING ERRCODE='23514';
 END IF;
 SELECT * INTO c FROM session_prototype.control WHERE id=1 FOR SHARE;
 SELECT * INTO a FROM session_prototype.approvals WHERE nonce=NEW.nonce;
 IF c.halted THEN RAISE EXCEPTION 'PROTOTYPE_HALTED' USING ERRCODE='23514'; END IF;
 IF c.revoked THEN RAISE EXCEPTION 'PROTOTYPE_AUTHORITY_REVOKED' USING ERRCODE='23514'; END IF;
 IF clock_timestamp()>=a.expires_at THEN RAISE EXCEPTION 'PROTOTYPE_AUTHORITY_EXPIRED' USING ERRCODE='23514'; END IF;
 IF c.generation<>a.generation OR a.backend_pid<>pg_backend_pid()
   OR a.transaction_id<>pg_current_xact_id() OR a.login_name<>session_user THEN
  RAISE EXCEPTION 'PROTOTYPE_COMMIT_DENIED' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER enforce_commit AFTER INSERT ON session_prototype.effects
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION session_prototype.check_commit();
REVOKE ALL ON SCHEMA session_prototype FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA session_prototype FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA session_prototype FROM PUBLIC;
