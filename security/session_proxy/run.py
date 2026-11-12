"""Root TEST orchestrator; broker/attackers run as three distinct non-root UIDs.

Only fresh disposable Linux/PostgreSQL infrastructure. No HOPE approval issuer.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from broker import canonical, observation

HERE = Path(__file__).resolve().parent
BROKER_UID, WORKER_UID, OTHER_UID = 21001, 21002, 21003
CASES = ('authorized_prototype_commit','borrowed_session','reconnected_worker',
         'replaced_process','wrong_kernel_uid','wrong_namespace','delegated_socket',
         'approval_replay','revoked_authority','halt_during_execution',
         'expiry_at_commit','direct_sql_outside_boundary','same_interpreter_exec','new_thread_creation',
         'commit_peer_kernel_stopped','wrong_signing_key','wrong_signed_scope','non_boolean_commit',
         'untrusted_identity_fields','policy_generation_changed','lost_commit_acknowledgment',
         'broker_crash_before_commit','broker_commit_transport_loss_reconciled','concurrent_same_approval')


class Blocked(Exception):
    pass


def require(value, message):
    if not value:
        raise AssertionError(message)


def main():
    output=Path(os.environ.get('PROXY_EVIDENCE_DIR','proxy-evidence'))
    output.mkdir(parents=True,exist_ok=True)
    results={name:{'status':'BLOCKED','reason':'not executed'} for name in CASES}
    report={'schema':'hope.session-prototype-evidence.v1','production_issuance':False,
            'scope':'isolated prototype economic ledger, NOT HOPE PAPER admission',
            'cases':results, 'production_guarantee':'BLOCKED'}
    clients=[];server=None
    try:
        if sys.platform!='linux' or os.geteuid()!=0:
            raise Blocked('Linux disposable root orchestrator required; applications stay non-root')
        url=make_url(os.environ['PROXY_DATABASE_URL'])
        require(url.host in ('localhost','127.0.0.1') and url.database=='hope','disposable localhost hope database required')
        admin=psycopg.connect(url.set(drivername='postgresql').render_as_string(hide_password=False),autocommit=True)
        major=admin.info.server_version//10000
        require(major in (16,17) and str(major)==os.environ['PROXY_POSTGRES_MAJOR'],'wrong PostgreSQL')
        report['postgres_major']=major
        require(not admin.execute("SELECT 1 FROM pg_namespace WHERE nspname='session_prototype'").fetchone(),'fresh database required')
        require(admin.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]==0,'fresh public schema required')
        suffix=os.urandom(8).hex();urls={}
        for kind in ('owner','broker','worker'):
            role='proxy_'+kind+'_'+suffix;password=os.urandom(32).hex()
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT').format(sql.Identifier(role),sql.Literal(password)))
            urls[kind]=url.set(username=role,password=password)
        admin.execute(sql.SQL('GRANT CREATE ON DATABASE {} TO {}').format(sql.Identifier(url.database),sql.Identifier(urls['owner'].username)))
        with psycopg.connect(urls['owner'].set(drivername='postgresql').render_as_string(hide_password=False),autocommit=True) as owner:
            owner.execute((HERE/'schema.sql').read_text())
            role=sql.Identifier(urls['broker'].username)
            owner.execute(sql.SQL('GRANT USAGE ON SCHEMA session_prototype TO {}').format(role))
            owner.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA session_prototype TO {}').format(role))
            owner.execute(sql.SQL('GRANT INSERT ON session_prototype.approvals,session_prototype.effects TO {}').format(role))
            owner.execute(sql.SQL('GRANT EXECUTE ON FUNCTION session_prototype.check_commit() TO {}').format(role))
        admin.execute(sql.SQL('GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO {}').format(sql.Identifier(urls['broker'].username)))
        # This independent SQL boundary is executable even if host attestation
        # is BLOCKED. Do not abandon unaffected tests on a missing capability.
        sql_denials=[]
        with psycopg.connect(urls['worker'].set(drivername='postgresql').render_as_string(hide_password=False),autocommit=True) as worker:
            for attack in ("INSERT INTO session_prototype.effects VALUES ('forged',1)",
                           "UPDATE session_prototype.control SET halted=false,revoked=false",
                           "INSERT INTO session_prototype.approvals(nonce) VALUES ('forged')",
                           'SET ROLE '+urls['broker'].username):
                try:worker.execute(attack)
                except psycopg.errors.InsufficientPrivilege as exc:sql_denials.append(exc.sqlstate)
                else:raise AssertionError('direct SQL boundary bypass')
        require(admin.execute('SELECT count(*) FROM session_prototype.effects').fetchone()[0]==0,'unauthorized SQL effect')
        results['direct_sql_outside_boundary']={'status':'VERIFIED','scope':'disposable prototype only',
                                               'sqlstates':sql_denials,'committed_effects':0}
        root=Path(tempfile.mkdtemp(prefix='hope-proxy-'));root.chmod(0o755)
        # The runner checkout may not be traversable by dedicated UIDs. Copy
        # reviewed executable sources to a root-owned, read-only runtime path.
        runtime=root/'runtime';runtime.mkdir();runtime.chmod(0o755)
        for name in ('broker.py','worker.py','execution_fence.py'):
            target=runtime/name;target.write_bytes((HERE/name).read_bytes());target.chmod(0o644)
        socketdir=root/'socket';socketdir.mkdir();os.chown(socketdir,BROKER_UID,BROKER_UID);socketdir.chmod(0o755)
        audit=socketdir/'audit.jsonl'
        private=Ed25519PrivateKey.generate()
        executable=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        config={'socket':str(socketdir/'broker.sock'),'audit':str(audit),
            'worker_uid':WORKER_UID,'socket_gid':WORKER_UID,'public_key':private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex(),
            'dsn':urls['broker'].set(drivername='postgresql').render_as_string(hide_password=False),
            'executable_sha256':executable,'namespaces':observation(os.getpid())['namespaces'],
            'release_tree':hashlib.sha256(b''.join((HERE/n).read_bytes() for n in ('broker.py','worker.py','execution_fence.py','schema.sql'))).hexdigest()}
        config_path=root/'broker.json';config_path.write_text(json.dumps(config));os.chown(config_path,BROKER_UID,BROKER_UID);config_path.chmod(0o600)
        clean_env={'PATH':'/usr/bin:/bin','PYTHONUNBUFFERED':'1','LANG':'C.UTF-8'}
        serverlog=(root/'broker.log').open('w+')
        def launch_server():
            return subprocess.Popen(['/usr/bin/setpriv','--reuid='+str(BROKER_UID),'--regid='+str(BROKER_UID),'--groups='+str(WORKER_UID),
            '--inh-caps=+sys_ptrace','--ambient-caps=+sys_ptrace',sys.executable,str(runtime/'broker.py'),str(config_path)],
                env=clean_env,stdout=serverlog,stderr=serverlog)
        server=launch_server()

        report['host_capabilities']={line.split(':',1)[0]:line.split(':',1)[1].strip()
            for line in Path('/proc/self/status').read_text().splitlines() if line.startswith(('CapBnd:','CapEff:'))}
        until=time.monotonic()+10
        while not Path(config['socket']).exists():
            if server.poll() is not None or time.monotonic()>until:
                serverlog.flush();serverlog.seek(0)
                diagnostics=serverlog.read()
                # Configuration contents/credentials are never logged. Keep only
                # the launcher's own diagnostics and exception type lines.
                report['broker_startup']={'exit_code':server.poll(),'diagnostics':[line for line in diagnostics.splitlines()
                    if line.startswith('setpriv:') or "can't open file" in line or line.startswith(('PermissionError:','ModuleNotFoundError:','ImportError:'))]}
                bounded=int(report['host_capabilities']['CapBnd'],16) & (1 << 19)
                reason=('CAP_SYS_PTRACE absent from root capability bounding set' if not bounded
                    else 'Dedicated broker startup failed; see sanitized broker_startup evidence')
                raise Blocked(reason)
            time.sleep(.05)

        class Client:
            def __init__(self, uid=WORKER_UID, namespace=False, delegation=False):
                command=[sys.executable,str(runtime/'worker.py'),config['socket']]
                if delegation:command.append('delegate')
                kwargs=dict(user=uid,group=WORKER_UID,extra_groups=[])
                if namespace:
                    command=['/usr/bin/unshare','--mount','--','/usr/bin/setpriv','--reuid='+str(uid),
                             '--regid='+str(uid),'--clear-groups',*command]
                    kwargs={}
                self.p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    text=True,env=clean_env,**kwargs)
                clients.append(self)
                self.first=self.read()
                if self.first.get('reason') in ('PermissionError','ProcessLookupError'):
                    raise Blocked('Dedicated observer cannot read genuine peer /proc identity; no synthetic fallback')
            def read(self):
                if not select.select([self.p.stdout],[],[],15)[0]:raise RuntimeError('client response timeout')
                line=self.p.stdout.readline()
                if not line:
                    raise Blocked('worker startup/namespace/host access unavailable')
                return json.loads(line)
            def send(self,value,reply=True):
                self.p.stdin.write(json.dumps(value)+'\n');self.p.stdin.flush()
                return self.read() if reply else None
            def close(self):
                if self.p.poll() is None:self.p.kill()
                self.p.wait(timeout=3)

        def state(halted=False,revoked=False):
            admin.execute('UPDATE session_prototype.control SET halted=%s,revoked=%s WHERE id=1',(halted,revoked))
        def count(nonce):
            return admin.execute('SELECT count(*) FROM session_prototype.effects WHERE nonce=%s',(nonce,)).fetchone()[0]
        def sign(client):
            t=client.first['challenge']
            native={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc',str(client.p.pid),'status').read_text().splitlines() if ':' in line}
            lease=t['execution_lease'];require(lease['kind']=='ptrace-single-thread-since-attach-v1','missing kernel fence')
            require(int(native['TracerPid'])==lease['tracer_tid'],'signer tracer mismatch')
            tracer={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc',str(lease['tracer_tid']),'status').read_text().splitlines() if ':' in line}
            require(int(tracer['Tgid'])==server.pid and int(tracer['Uid'].split()[0])==BROKER_UID,'untrusted tracer identity')
            # Authority independently observes the target kernel process and
            # pg_stat_activity of the broker-owned upstream transaction.
            require(t['peer']==observation(client.p.pid),'signer peer mismatch')
            row=admin.execute('SELECT usename,datid,backend_start::text,backend_xid::text FROM pg_stat_activity WHERE pid=%s',
                              (t['backend_pid'],)).fetchone()
            require(row and row[0]==urls['broker'].username and row[1]==t['database_oid']
                    and row[2]==t['backend_start'] and int(row[3])==int(t['xid']) % (2**32),'signer actual backend mismatch')
            require(t['cluster']==str(admin.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0]),'signer cluster mismatch')
            require(t['release_tree']==config['release_tree'] and t['scope']=='ONE_PROTOTYPE_EFFECT','signer scope mismatch')
            return {'signature':private.sign(canonical(t)).hex()}
        def prepared(client):
            require(client.send(sign(client))=={'prepared':client.first['challenge']['nonce']},'not prepared')
        def denied(client,packet):
            response=client.send(packet)
            require(response.get('result')=='DENIED','attack admitted')
            require(count(client.first['challenge']['nonce'])==0,'unauthorized economic effect')
            return response
        def audit_event(nonce,result,reason=None):
            until=time.monotonic()+5
            while time.monotonic()<until:
                rows=[json.loads(l) for l in audit.read_text().splitlines()]
                matches=[r for r in rows if r.get('nonce')==nonce and r.get('result')==result and (reason is None or r.get('reason')==reason)]
                if matches:return matches[-1]
                time.sleep(.05)
            raise AssertionError('missing kernel audit: '+str(result)+'/'+str(reason))

        def wait_backend_gone(pid):
            until=time.monotonic()+6
            while time.monotonic()<until:
                if not admin.execute('SELECT 1 FROM pg_stat_activity WHERE pid=%s',(pid,)).fetchone():return
                time.sleep(.02)
            raise AssertionError('upstream backend survived broker death')
        def restart():
            nonlocal server
            require(server.poll() is not None,'old broker is still running')
            Path(config['socket']).unlink()  # Fresh disposable orchestrator-owned directory only.
            server=launch_server()
            until=time.monotonic()+5
            while not Path(config['socket']).exists():
                require(server.poll() is None and time.monotonic()<until,'broker restart failed')
                time.sleep(.01)
        def commit_blocked(client):
            # Exact actual upstream session is observed executing COMMIT while
            # its mandatory control-row fence is blocked by the test authority.
            client.send({'commit':True},reply=False)
            t=client.first['challenge'];audit_event(t['nonce'],'COMMIT_BARRIER_HELD')
            until=time.monotonic()+1
            while time.monotonic()<until:
                admin.execute('SELECT pg_stat_clear_snapshot()')
                row=admin.execute('SELECT state,wait_event_type,query FROM pg_stat_activity WHERE pid=%s',(t['backend_pid'],)).fetchone()
                if row and row[0]=='active' and row[1]=='Lock' and row[2]=='COMMIT':
                    status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc',str(client.p.pid),'status').read_text().splitlines() if ':' in line}
                    require(status['State'].startswith('t') and int(status['TracerPid'])==t['execution_lease']['tracer_tid'],'COMMIT peer was not kernel stopped')
                    return dict(state=status['State'],tracer_tid=int(status['TracerPid']),backend_pid=t['backend_pid'])
                time.sleep(.005)
            raise AssertionError('actual COMMIT lock wait not observed')

        for name in CASES:
            if results[name]['status']=='VERIFIED':continue
            state()
            economic_before=admin.execute('SELECT count(*) FROM session_prototype.effects').fetchone()[0]
            try:
                if name=='authorized_prototype_commit':
                    a=Client();prepared(a);response=a.send({'commit':True})
                    require(response.get('result')=='COMMITTED' and count(response['nonce'])==1,'positive commit missing')
                    t=a.first['challenge']
                    row=admin.execute('SELECT backend_pid,transaction_id::text,login_name FROM session_prototype.approvals WHERE nonce=%s',(t['nonce'],)).fetchone()
                    require(tuple(row)==(t['backend_pid'],t['xid'],t['login']),'actual SQL session binding mismatch')
                elif name in ('borrowed_session','reconnected_worker'):
                    a=Client();signature=sign(a);a.close() if name=='reconnected_worker' else None
                    b=Client();denied(b,signature);a.close()
                elif name=='replaced_process':
                    a=Client();prepared(a);nonce=a.first['challenge']['nonce'];a.send({'replace':True},reply=False)
                    audit_event(nonce,'DENIED','EXEC_FENCE_EXEC_EVENT');require(count(nonce)==0,'replacement committed')
                elif name=='wrong_kernel_uid':
                    a=Client(OTHER_UID);require(a.first.get('reason')=='WRONG_KERNEL_UID','wrong UID accepted')
                elif name=='wrong_namespace':
                    a=Client(namespace=True);require(a.first.get('reason')=='WRONG_NAMESPACE','wrong namespace accepted')
                elif name=='delegated_socket':
                    a=Client(delegation=True);prepared(a);denied(a,{'delegate':True})
                elif name=='approval_replay':
                    a=Client();signature=sign(a);require(a.send(signature).get('prepared'),'not prepared')
                    require(a.send({'commit':True}).get('result')=='COMMITTED','initial commit failed')
                    b=Client();denied(b,signature)
                    require(count(a.first['challenge']['nonce'])==1,'approval repeated')
                elif name in ('same_interpreter_exec','new_thread_creation'):
                    a=Client();prepared(a);nonce=a.first['challenge']['nonce']
                    a.send({'same_exec':True} if name=='same_interpreter_exec' else {'new_thread':True},reply=False)
                    reason='EXEC_FENCE_EXEC_EVENT' if name=='same_interpreter_exec' else 'EXEC_FENCE_TASK_CREATION'
                    event=audit_event(nonce,'DENIED',reason)
                    require(any(row['event']==(4 if name=='same_interpreter_exec' else 3) for row in event['kernel_events']),'missing actual kernel event')
                    require(count(nonce)==0,'invalid execution committed')
                elif name in ('wrong_signing_key','wrong_signed_scope','untrusted_identity_fields'):
                    a=Client();t=a.first['challenge']
                    if name=='wrong_signing_key':packet={'signature':Ed25519PrivateKey.generate().sign(canonical(t)).hex()}
                    elif name=='wrong_signed_scope':packet={'signature':private.sign(canonical(dict(t,scope='BOOTSTRAP_ONLY'))).hex()}
                    else:packet=dict(sign(a),pid=a.p.pid,backend_pid=t['backend_pid'])
                    denied(a,packet)
                elif name=='non_boolean_commit':
                    a=Client();prepared(a);response=denied(a,{'commit':1})
                    require(response['reason']=='COMMIT_PROTOCOL','numeric commit was accepted')
                elif name=='policy_generation_changed':
                    a=Client();prepared(a)
                    admin.execute('UPDATE session_prototype.control SET generation=generation+1 WHERE id=1')
                    response=denied(a,{'commit':True})
                    require(response['reason']=='PROTOTYPE_COMMIT_DENIED','policy fence was not checked')
                elif name=='commit_peer_kernel_stopped':
                    a=Client();prepared(a)
                    with admin.transaction():
                        admin.execute('SELECT 1 FROM session_prototype.control WHERE id=1 FOR UPDATE')
                        report['commit_barrier_observation']=commit_blocked(a)
                    response=a.read();require(response.get('result')=='COMMITTED' and count(response['nonce'])==1,'fenced COMMIT did not finish')
                elif name=='lost_commit_acknowledgment':
                    a=Client();old_signature=sign(a);require(a.send(old_signature).get('prepared'),'not prepared')
                    require(a.send({'drop_ack':True})=='ACK_UNAVAILABLE','transport shutdown not exercised')
                    nonce=a.first['challenge']['nonce'];audit_event(nonce,'COMMITTED_ACK_LOST')
                    require(count(nonce)==1,'owner could not resolve actual committed effect')
                    b=Client();denied(b,old_signature)
                    require(count(nonce)==1,'lost ACK triggered replay')
                elif name in ('broker_crash_before_commit','broker_commit_transport_loss_reconciled'):
                    a=Client();old_signature=sign(a);require(a.send(old_signature).get('prepared'),'not prepared')
                    t=a.first['challenge']
                    if name=='broker_commit_transport_loss_reconciled':
                        with admin.transaction():
                            admin.execute('SELECT 1 FROM session_prototype.control WHERE id=1 FOR UPDATE')
                            report['unknown_commit_observation']=commit_blocked(a)
                            server.kill();server.wait(timeout=3)
                    else:server.kill();server.wait(timeout=3)
                    a.p.wait(timeout=3)
                    require(a.p.returncode==-signal.SIGKILL,'EXITKILL did not terminate the leased worker')
                    wait_backend_gone(t['backend_pid']);resolved=count(t['nonce'])
                    require(resolved in (0,1) and (name!='broker_crash_before_commit' or resolved==0),'unexpected crash outcome')
                    report.setdefault('recovery_observations',{})[name]=dict(read_only_resolved_effects=resolved,automatic_replays=0)
                    restart();b=Client();denied(b,old_signature)
                    require(count(t['nonce'])==resolved,'old execution replayed after restart')
                elif name=='concurrent_same_approval':
                    from concurrent.futures import ThreadPoolExecutor
                    a=Client();b=Client();old_signature=sign(a)
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        responses=list(pool.map(lambda c:c.send(old_signature),(a,b)))
                    require(responses[0].get('prepared') and responses[1].get('result')=='DENIED','session signature borrowed concurrently')
                    require(a.send({'commit':True}).get('result')=='COMMITTED','authorized concurrent winner failed')
                    require(count(b.first['challenge']['nonce'])==0,'borrowed concurrent effect')
                elif name in ('revoked_authority','halt_during_execution','expiry_at_commit'):
                    a=Client();prepared(a)
                    if name=='revoked_authority':state(revoked=True)
                    elif name=='halt_during_execution':state(halted=True)
                    else:time.sleep(4)  # signed three-second lease; actual PG COMMIT check
                    response=denied(a,{'commit':True})
                    expected={'revoked_authority':'PROTOTYPE_AUTHORITY_REVOKED',
                              'halt_during_execution':'PROTOTYPE_HALTED',
                              'expiry_at_commit':'PROTOTYPE_AUTHORITY_EXPIRED'}[name]
                    require(response['reason']==expected,'wrong COMMIT rejection boundary: '+response['reason'])
                economic_after=admin.execute('SELECT count(*) FROM session_prototype.effects').fetchone()[0]
                allowed=1 if name in ('authorized_prototype_commit','approval_replay','commit_peer_kernel_stopped','lost_commit_acknowledgment','concurrent_same_approval') else report.get('recovery_observations',{}).get(name,{}).get('read_only_resolved_effects',0)
                require(economic_after-economic_before==allowed,'unexpected committed economic effects')
                results[name]={'status':'VERIFIED','scope':'disposable prototype only',
                               'authorized_effects':allowed,'unauthorized_committed_effects':0}
            except Blocked as exc:results[name]={'status':'BLOCKED','reason':str(exc)}
            except Exception as exc:results[name]={'status':'FAILED','reason':type(exc).__name__+': '+str(exc)[:180]}
            finally:
                for client in clients:client.close()
                clients.clear()
        report['kernel_audit']=[json.loads(l) for l in audit.read_text().splitlines()]
        report['execution_fence_scope']='Kernel exec/task events after successful SEIZE only; single-threaded targets; no loaded-code or pre-attachment proof'
        report['remaining_proofs']={name:'BLOCKED' for name in (
            'same_executable_exec_generation','loaded_python_code_integrity','production_OCI_image_attestation',
            'remote_signer_isolation_from_observer_capability','HOPE_financial_path_integration',
            'independently_controlled_CI_workflow')}
    except Exception as exc:
        report['host_error']=str(exc) if isinstance(exc,Blocked) else type(exc).__name__
    finally:
        for client in clients:client.close()
        if server is not None:
            server.terminate();server.wait(timeout=5)
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        lines=['# Trusted session prototype evidence','',report['scope'],'','Production issuance: disabled','']
        lines += [f"- {name}: {value['status']} {value.get('reason','')}" for name,value in results.items()]
        if report.get('host_error'):lines.append('Host BLOCKED: '+report['host_error'])
        (output/'report.md').write_text('\n'.join(lines)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k!='kernel_audit'},sort_keys=True))
    return 0 if all(value['status']=='VERIFIED' for value in results.values()) else 1


if __name__=='__main__':
    raise SystemExit(main())
