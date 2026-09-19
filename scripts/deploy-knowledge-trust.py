"""Deploy a locally tested trust release without recreating authoritative stores.

Requires an already verified encrypted backup and prepared local configuration.
Only explicit application/control services are recreated. Existing schema/data
are retained; the ledger records migrations only after their DDL succeeds.
"""
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def run(args, *, stdin=None, capture=False):
    result=subprocess.run(args,input=stdin,text=True,capture_output=True,cwd=ROOT)
    if result.returncode:
        # Commands may contain credentials on stdin; do not include stdout/stderr
        # or the full arguments in an exception rendered by an unattended caller.
        raise RuntimeError('Deployment step failed: '+args[0]+' '+args[1])
    return result.stdout if capture else None


def deploy():
    values=dict(line.split('=',1) for line in (ROOT/'.env').read_text().splitlines() if '=' in line and not line.lstrip().startswith('#'))
    plan=json.loads((ROOT/'config/windows-startup.compose-files.json').read_text())
    if plan['project']!='gbuzz' or 'docker-compose.knowledge-trust.yml' not in plan['files']:
        raise RuntimeError('Prepare the explicit knowledge trust plan first')
    args=['docker','compose','--project-name','gbuzz']
    for file in plan['files']:args+=['-f',file]
    for profile in plan['profiles']:args+=['--profile',profile]
    run(args+['config','--quiet'])
    services=['gcor-proxy','gcor-enterprise','buzz-knowledge','gcor-event-projector','mcp-postgres-gcor','recovery-controller']
    before={s:json.loads(run(['docker','inspect','gbuzz-'+s+'-1'],capture=True))[0]['Image'] for s in services}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence=ROOT/'backups'/('knowledge-trust-'+stamp);evidence.mkdir()
    # Tag every old image before any mutation so image pruning cannot lose rollback.
    for service,image in before.items():
        tag='gbuzz-before-trust-'+service+':'+stamp
        available=subprocess.run(['docker','image','inspect',image],capture_output=True).returncode==0
        if available:run(['docker','image','tag',image,tag])
        else:
            # Docker's image store can lose a replaced manifest while a container
            # still runs from its snapshot. Retain that snapshot, clearing runtime
            # environment values so the rollback image does not embed credentials.
            container=json.loads(run(['docker','inspect','gbuzz-'+service+'-1'],capture=True))[0]
            commit=['docker','commit']
            for entry in container['Config']['Env']:
                name=entry.split('=',1)[0]
                if name!='PATH':commit+=['--change','ENV '+name+'=']
            try:run(commit+['gbuzz-'+service+'-1',tag])
            except RuntimeError:
                # A missing parent blob can also prevent commit. Exporting the
                # running root filesystem needs no parent manifest and excludes
                # mounted volumes and runtime environment metadata.
                archive=evidence/(service+'-rootfs.tar')
                run(['docker','export','--output',str(archive),'gbuzz-'+service+'-1'])
                restore=['docker','import','--change','WORKDIR '+(container['Config'].get('WorkingDir') or '/')]
                for setting in ('Entrypoint','Cmd'):
                    if container['Config'].get(setting):restore+=['--change',setting.upper()+' '+json.dumps(container['Config'][setting])]
                restore+=['--change','ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin',str(archive),tag]
                run(restore)
                archive.unlink()
            before[service]=json.loads(run(['docker','image','inspect',tag],capture=True))[0]['Id']
    (evidence/'before-images.json').write_text(json.dumps(before,indent=2)+'\n')
    pg='gbuzz-postgres-1';user=values.get('POSTGRES_USER','buzz');database=values.get('POSTGRES_DB','buzz')
    sql=['docker','exec','-i',pg,'psql','-X','-v','ON_ERROR_STOP=1','-U',user,'-d',database,'-At']
    dims=run(sql,stdin="SELECT atttypmod FROM pg_attribute WHERE attrelid='gcor.chunks'::regclass AND attname='embedding';",capture=True).strip()
    if not re.fullmatch('[0-9]+',dims):raise RuntimeError('Unable to verify embedding dimensions')
    # Each copy gets a fresh path; no recursive removal or overwrite of existing data.
    target='/tmp/knowledge-trust-'+stamp
    run(['docker','cp',str(ROOT/'migrations'),pg+':'+target])
    migration_log=run(['docker','exec','-e','MIGRATIONS_DIR='+target,'-e','EMBEDDING_DIMS='+dims,pg,
        'sh','-c','PGUSER="$POSTGRES_USER" PGDATABASE="$POSTGRES_DB" sh "$MIGRATIONS_DIR/apply.sh"'],capture=True)
    (evidence/'migrations.log').write_text(migration_log)
    runtime=values['GCOR_DB_USER'];password=values['GCOR_DB_PASSWORD']
    if not re.fullmatch('[a-z_][a-z0-9_]{0,62}',runtime) or runtime==user:raise RuntimeError('Invalid runtime role')
    literal=lambda v:"'"+v.replace("'","''")+"'"
    role_sql=f"SELECT format('CREATE ROLE %I LOGIN',{literal(runtime)}) WHERE NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname={literal(runtime)}) \\gexec\n"
    role_sql+=f"ALTER ROLE {runtime} LOGIN INHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {literal(password)};\nGRANT gcor_app TO {runtime};\n"
    run(sql,stdin=role_sql)
    print('Schema migration history recorded; restricted runtime role prepared.',flush=True)
    run(args+['up','-d','--no-deps','--no-build','--pull','never','docker-socket-proxy'])
    run(args+['up','-d','--no-deps','--no-build','--pull','never',*services])
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        states=json.loads(run(['docker','inspect',*['gbuzz-'+s+'-1' for s in services]],capture=True))
        if all(x['State']['Running'] and x['State'].get('Health',{}).get('Status','healthy')=='healthy' for x in states):break
        time.sleep(3)
    else:raise RuntimeError('Application health did not converge; retained rollback images in '+str(evidence))
    after={s:json.loads(run(['docker','inspect','gbuzz-'+s+'-1'],capture=True))[0]['Image'] for s in services}
    (evidence/'after-images.json').write_text(json.dumps(after,indent=2)+'\n')
    print('Application rollout healthy. Release evidence: '+str(evidence))


if __name__=='__main__':deploy()
