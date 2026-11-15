"""Read-only bounded schema evidence; an observed digest is not an approval."""
import hashlib
import json
import re
from sqlalchemy import text

# Deliberately excludes table data, sequence current values, OIDs and secrets.
# Function/trigger definitions are hashed internally and never returned.
CATALOG = """
SELECT kind,identity,CASE WHEN pg_catalog.octet_length(definition)<=65536 AND sum(pg_catalog.octet_length(definition)) OVER ()<=4194304 THEN definition ELSE NULL END FROM (
 SELECT 'relation' kind,c.relname identity,c.relkind::text||':'||c.relpersistence::text definition
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')
 UNION ALL SELECT 'column',c.relname||'.'||a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod)||':'||a.attnotnull::text||':'||coalesce(pg_catalog.pg_get_expr(d.adbin,d.adrelid),'')
 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped AND c.relkind IN ('r','p','v','m')
 UNION ALL SELECT 'constraint',c.relname||'.'||k.conname,pg_catalog.pg_get_constraintdef(k.oid)||':'||k.convalidated::text
 FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
 UNION ALL SELECT 'index',c.relname,pg_catalog.pg_get_indexdef(i.indexrelid)||':'||i.indisvalid::text||':'||i.indisready::text
 FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
 UNION ALL SELECT 'trigger',c.relname||'.'||t.tgname,pg_catalog.pg_get_triggerdef(t.oid)||':'||t.tgenabled::text
 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal
 UNION ALL SELECT 'function',p.proname||'('||pg_catalog.pg_get_function_identity_arguments(p.oid)||')',pg_catalog.pg_get_functiondef(p.oid)
 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind IN ('f','p')
 UNION ALL SELECT 'sequence',c.relname,s.seqtypid::pg_catalog.regtype::text||':'||s.seqstart::text||':'||s.seqincrement::text||':'||s.seqmax::text||':'||s.seqmin::text||':'||s.seqcache::text||':'||s.seqcycle::text
 FROM pg_catalog.pg_sequence s JOIN pg_catalog.pg_class c ON c.oid=s.seqrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
 UNION ALL SELECT 'view',c.relname,pg_catalog.pg_get_viewdef(c.oid)
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('v','m')
) observed ORDER BY kind,identity LIMIT 10001
"""


def schema_observation(connection, expected_sha256=None):
    report=dict(schema='hope.schema-catalog.v1',state='UNVERIFIED',code='APPROVED_SCHEMA_DIGEST_REQUIRED',
                sha256=None,objects=None,database_writes=False,execution_authorized=False)
    try:
        if connection.dialect.name!='postgresql':raise ValueError()
        show=lambda key:connection.execute(text('SHOW '+key)).scalar_one()
        from hope.api.paper_preflight import _milliseconds
        if (int(show('server_version_num'))//10000 not in {16,17} or show('transaction_read_only')!='on'
                or show('transaction_isolation') not in {'repeatable read','serializable'}
                or not 0<_milliseconds(show('statement_timeout'))<=30000
                or not 0<_milliseconds(show('lock_timeout'))<=10000):raise ValueError()
        rows=[tuple(row) for row in connection.execute(text(CATALOG))]
        if not 0<len(rows)<=10000 or any(row[2] is None for row in rows):raise ValueError()
        # Server collation may tie distinct identities/definitions. Canonical
        # ordinal ordering includes every field and retains duplicate rows.
        rows.sort()
        payload=json.dumps(rows,separators=(',',':'),ensure_ascii=True).encode()
        if len(payload)>4*1024*1024:raise ValueError()
        digest=hashlib.sha256(payload).hexdigest()
        report.update(sha256=digest,objects=len(rows))
        if expected_sha256 is not None:
            if not isinstance(expected_sha256,str) or re.fullmatch('[0-9a-f]{64}',expected_sha256) is None:raise ValueError()
            report.update(state='VERIFIED' if expected_sha256==digest else 'MISMATCH',code='SCHEMA_MATCH' if expected_sha256==digest else 'SCHEMA_MISMATCH')
    except Exception:report.update(state='BLOCKED',code='SCHEMA_CATALOG_UNVERIFIED',sha256=None,objects=None)
    return report
