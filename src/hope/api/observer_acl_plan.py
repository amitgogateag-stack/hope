"""Read-only inventory and review-only reversible DATABASE TEMP proposals.

No apply endpoint, privileged connection fallback, approval or authority issuer.
Generated SQL is an operator proposal; execution requires separate authorization.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re

from sqlalchemy import Connection, text

# Fixed catalog reads. No application rows, passwords or backend SQL text.
INVENTORY_SQL = """
WITH d AS (SELECT * FROM pg_catalog.pg_database WHERE datname=current_database()),
a AS (SELECT x.grantor::bigint AS grantor,x.grantee::bigint AS grantee,x.privilege_type,x.is_grantable FROM d
 CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(d.datacl,pg_catalog.acldefault('d',d.datdba))) x),
r AS (SELECT oid::bigint AS oid,rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
 rolreplication,rolbypassrls,rolinherit FROM pg_catalog.pg_roles ORDER BY oid LIMIT 256)
SELECT pg_catalog.jsonb_build_object(
 'schema','hope.observer-temp-boundary.v1',
 'database',pg_catalog.jsonb_build_object('oid',d.oid::bigint,'name',d.datname,'owner',d.datdba::bigint),
 'cluster',pg_catalog.jsonb_build_array(s.system_identifier::text,
   pg_catalog.current_setting('server_version_num')::int/10000,pg_catalog.pg_is_in_recovery()),
 'observer',CAST(:observer AS text),
 'role_count',(SELECT count(*) FROM pg_catalog.pg_roles),
 'acl',(SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(grantor,grantee,privilege_type,is_grantable)
    ORDER BY grantor,grantee,privilege_type,is_grantable) FROM a),
 'roles',(SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
   'oid',r.oid,'name',r.rolname,'login',r.rolcanlogin,'superuser',r.rolsuper,
   'createdb',r.rolcreatedb,'createrole',r.rolcreaterole,'replication',r.rolreplication,
   'bypassrls',r.rolbypassrls,'inherit',r.rolinherit,
   'connect',pg_catalog.has_database_privilege(r.oid::oid,d.oid,'CONNECT'),
   'create',pg_catalog.has_database_privilege(r.oid::oid,d.oid,'CREATE'),
   'temp',pg_catalog.has_database_privilege(r.oid::oid,d.oid,'TEMPORARY'),
   'member_of',(SELECT COALESCE(pg_catalog.jsonb_agg(p.oid::bigint ORDER BY p.oid),'[]'::jsonb)
       FROM pg_catalog.pg_roles p WHERE pg_catalog.pg_has_role(r.oid::oid,p.oid,'MEMBER')),
   'inherited',(SELECT COALESCE(pg_catalog.jsonb_agg(p.oid::bigint ORDER BY p.oid),'[]'::jsonb)
       FROM pg_catalog.pg_roles p WHERE pg_catalog.pg_has_role(r.oid::oid,p.oid,'USAGE'))
   ) ORDER BY r.oid) FROM r),
 'memberships',(SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(
   roleid::bigint,member::bigint,grantor::bigint,admin_option,inherit_option,set_option) ORDER BY roleid,member,grantor),'[]'::jsonb)
   FROM pg_catalog.pg_auth_members))
FROM d CROSS JOIN pg_catalog.pg_control_system() s
"""


def _require(value, code):
    if not value:
        raise ValueError('OBSERVER_TEMP_'+code)


def _name(value):
    _require(type(value) is str and re.fullmatch(r'[a-z_][a-z0-9_]{0,62}',value),'IDENTIFIER_INVALID')
    return '"'+value+'"'


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def capture_temp_boundary(connection: Connection, observer: str) -> dict:
    """Fresh bounded READ ONLY transaction; no role/ACL or application writes."""
    _name(observer)
    _require(not connection.in_transaction(),'FRESH_TRANSACTION_REQUIRED')
    connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    connection.exec_driver_sql("SET LOCAL statement_timeout='5000ms'")
    connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
    value=connection.execute(text(INVENTORY_SQL),{'observer':observer}).scalar_one()
    _require(connection.exec_driver_sql('SHOW transaction_read_only').scalar_one()=='on','READ_ONLY_REQUIRED')
    return value


def _temp(role, acl):
    return role['superuser'] or any(grantee==0 or grantee in role['inherited']
        for _,grantee,privilege,_ in acl if privilege=='TEMPORARY')


@dataclass(frozen=True)
class TempAclProposal:
    before_json: str
    after_json: str
    preserved_roles: tuple[str,...]
    affected_roles: tuple[str,...]

    @property
    def before_sha256(self):
        return hashlib.sha256(self.before_json.encode()).hexdigest()

    @property
    def after_sha256(self):
        return hashlib.sha256(self.after_json.encode()).hexdigest()

    def sql(self, *, rollback=False):
        """Exact baseline/postcondition guards; no credentials, grants beyond TEMP.

        One DO statement performs every ACL change atomically. Explicit transaction
        bounds and timeouts are for psql -X -v ON_ERROR_STOP=1, not an ORM session.
        Role identity/membership drift, changed ACLs and replay fail closed.
        """
        before=json.loads(self.before_json);after=json.loads(self.after_json)
        database=_name(before['database']['name'])
        owner=next(r['name'] for r in before['roles'] if r['oid']==before['database']['owner'])
        observer=before['observer'];_name(observer);_name(owner)
        commands=['GRANT TEMPORARY ON DATABASE '+database+' TO '+_name(r) for r in self.preserved_roles]
        commands+=['REVOKE TEMPORARY ON DATABASE '+database+' FROM PUBLIC']
        if rollback:
            commands=['GRANT TEMPORARY ON DATABASE '+database+' TO PUBLIC']
            commands+=['REVOKE TEMPORARY ON DATABASE '+database+' FROM '+_name(r) for r in self.preserved_roles]
            before,after=after,before
        query=INVENTORY_SQL.replace(':observer',"'"+observer+"'").strip()
        # Normalized JSON handles ACL array order and default ACL representation.
        literals=[_canonical(v) for v in (before,after)]
        _require(all('$expected$' not in s and '$proposal$' not in s for s in literals),'SQL_LITERAL_INVALID')
        statements='\n'.join('    '+c+';' for c in commands)
        return ("-- OPERATOR REVIEW REQUIRED. No operating/financial admission is issued.\n"
          "BEGIN;\nSET LOCAL search_path=pg_catalog;\nSET LOCAL statement_timeout='5000ms';\n"
          "SET LOCAL lock_timeout='2000ms';\nSET LOCAL idle_in_transaction_session_timeout='10000ms';\n"
          "DO $proposal$\nDECLARE actual jsonb;\nBEGIN\n"
          "    IF current_user<>session_user OR current_user<>'"+owner+"' THEN\n"
          "        RAISE EXCEPTION 'OBSERVER_TEMP_OWNER_LOGIN_REQUIRED';\n    END IF;\n"
          "    SELECT ("+query+") INTO actual;\n"
          "    IF actual<>$expected$"+literals[0]+"$expected$::jsonb OR actual IS NULL THEN\n"
          "        RAISE EXCEPTION 'OBSERVER_TEMP_BASELINE_CHANGED';\n    END IF;\n"
          +statements+"\n    SELECT ("+query+") INTO actual;\n"
          "    IF actual<>$expected$"+literals[1]+"$expected$::jsonb OR actual IS NULL THEN\n"
          "        RAISE EXCEPTION 'OBSERVER_TEMP_POSTCONDITION_FAILED';\n    END IF;\n"
          "END\n$proposal$;\nCOMMIT;\n")


def propose_temp_restriction(value: dict, *, application_roles: tuple[str,...]) -> TempAclProposal:
    """Prepare a proposal and complete role impact, never automatic execution.

    Every affected LOGIN dependency must be explicitly supplied. Non-login and
    future-role default changes are reported for operator review; observed idle
    sessions and source searches cannot prove absence of future TEMP usage.
    """
    before=deepcopy(value)
    _require(type(before) is dict and before.get('schema')=='hope.observer-temp-boundary.v1','SCHEMA_INVALID')
    _require(type(application_roles) is tuple and len(set(application_roles))==len(application_roles),'APPLICATION_ROLES_INVALID')
    _name(before['database']['name']);_name(before['observer'])
    _require(before['cluster'][1] in (16,17) and before['cluster'][2] is False,'PRIMARY_DATABASE_REQUIRED')
    roles=before['roles'];acl=before['acl']
    oid=lambda x:type(x) is int and 0<x<2**32
    _require(oid(before['database']['oid']) and oid(before['database']['owner'])
        and all(oid(r['oid']) and all(oid(x) for x in (*r['inherited'],*r['member_of'])) for r in roles)
        and all(oid(a[0]) and type(a[1]) is int and 0<=a[1]<2**32 for a in acl),'OID_INVALID')
    _require(before['role_count']==len(roles) and 1<=len(roles)<=256,'ROLE_INVENTORY_INCOMPLETE')
    _require(len({r['oid'] for r in roles})==len(roles) and len({r['name'] for r in roles})==len(roles),'ROLE_IDENTITY_INVALID')
    observer=next((r for r in roles if r['name']==before['observer']),None)
    if observer is None:raise ValueError('OBSERVER_TEMP_OBSERVER_ROLE_UNSAFE')
    _require(observer['login'] and not any(observer[k]
        for k in ('superuser','createdb','createrole','replication','bypassrls','create')),'OBSERVER_ROLE_UNSAFE')
    owner=next((r for r in roles if r['oid']==before['database']['owner']),None)
    if owner is None:raise ValueError('OBSERVER_TEMP_OWNER_LOGIN_REQUIRED')
    _require(owner['login'] and owner['oid']!=observer['oid'],'OWNER_LOGIN_REQUIRED')
    _require(all(_temp(r,acl) is r['temp'] for r in roles),'UNMODELED_TEMP_SEMANTICS')
    public=[a for a in acl if a[1]==0 and a[2]=='TEMPORARY']
    _require(public==[[owner['oid'],0,'TEMPORARY',False]],'PUBLIC_TEMP_GRANT_UNSUPPORTED')
    after=deepcopy(before);after['acl']=[a for a in acl if a not in public]
    preserve=[]
    for name in application_roles:
        _name(name);role=next((r for r in roles if r['name']==name),None)
        if role is None:raise ValueError('OBSERVER_TEMP_OBSERVER_DEPENDENCY_OVERLAP')
        _require(role['oid'] not in observer['member_of'],'OBSERVER_DEPENDENCY_OVERLAP')
        _require(role['temp'],'APPLICATION_TEMP_NOT_PREEXISTING')
        if not _temp(role,after['acl']):
            after['acl'].append([owner['oid'],role['oid'],'TEMPORARY',False]);preserve.append(name)
    after['acl'].sort()
    for role in after['roles']:role['temp']=_temp(role,after['acl'])
    after_by={r['oid']:r for r in after['roles']}
    _require(not any(after_by[oid]['temp'] for oid in observer['member_of']),'OBSERVER_TEMP_STILL_REACHABLE')
    changed=[r for r in roles if r['temp']!=after_by[r['oid']]['temp']]
    _require(not any(r['login'] and r['oid']!=observer['oid'] for r in changed),'UNREVIEWED_LOGIN_DEPENDENCY')
    return TempAclProposal(_canonical(before),_canonical(after),tuple(sorted(preserve)),tuple(r['name'] for r in changed))
