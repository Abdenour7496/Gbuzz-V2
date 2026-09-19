"""Read-only live verification; reports flags and image identities, never secrets."""
import argparse
import json
import subprocess
from pathlib import Path

SERVICES=('gcor-proxy','gcor-enterprise','buzz-knowledge','gcor-event-projector')


def docker(*args):
    result=subprocess.run(['docker',*args],capture_output=True,text=True)
    if result.returncode:raise RuntimeError('Docker verification command failed')
    return result.stdout


PROBE="""
import asyncio,json,os,asyncpg
async def run():
 c=await asyncpg.connect(host=os.environ['POSTGRES_HOST'],port=int(os.getenv('POSTGRES_PORT','5432')),user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD'],database=os.environ['POSTGRES_DB'])
 try:
  role=dict(await c.fetchrow('SELECT current_user AS name,rolsuper,rolbypassrls,rolcreaterole FROM pg_roles WHERE rolname=current_user'))
  role['owns_schema']=await c.fetchval("SELECT pg_has_role(current_user,nspowner,'USAGE') FROM pg_namespace WHERE nspname='gcor'")
  role['unscoped_documents']=await c.fetchval('SELECT count(*) FROM gcor.documents')
  role['unscoped_chunks']=await c.fetchval('SELECT count(*) FROM gcor.chunks')
  role['migrations']=dict((r['filename'],r['sha256']) for r in await c.fetch('SELECT filename,sha256 FROM gcor.schema_migrations'))
  print(json.dumps(role))
 finally:await c.close()
asyncio.run(run())
"""


def verify():
    import hashlib
    result={'services':{},'passed':True}
    expected={p.name:hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in (Path(__file__).resolve().parents[1]/'migrations').glob('*.sql')}
    for service in SERVICES:
        name='gbuzz-'+service+'-1';item=json.loads(docker('inspect',name))[0]
        probe=json.loads(docker('exec',name,'python','-c',PROBE))
        valid=(not any(probe[k] for k in ('rolsuper','rolbypassrls','rolcreaterole','owns_schema'))
               and probe['unscoped_documents']==0 and probe['unscoped_chunks']==0 and probe['migrations']==expected)
        result['services'][service]={'image':item['Image'],'health':item['State'].get('Health',{}).get('Status'),
            'user':item['Config']['User'],'role':probe['name'],'restricted':valid,'migration_count':len(probe['migrations'])}
        result['passed'] &= valid and item['State'].get('Health',{}).get('Status')=='healthy'
    recovery=json.loads(docker('inspect','gbuzz-recovery-controller-1'))[0]
    result['controller_socket_absent']=not any(m['Destination']=='/var/run/docker.sock' for m in recovery['Mounts'])
    result['passed'] &= result['controller_socket_absent']
    proxy=json.loads(docker('inspect','gbuzz-docker-socket-proxy-1'))[0]
    result['socket_boundary_healthy']=proxy['State'].get('Health',{}).get('Status')=='healthy'
    result['passed'] &= result['socket_boundary_healthy']
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output');args=parser.parse_args()
    report=verify()
    if args.output:Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report['passed'] else 1)
