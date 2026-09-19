"""Prepare local credentials and the canonical plan without printing secret values.

Run after taking an encrypted backup. Existing credentials and unrelated settings
are preserved. This does not deploy containers or apply migrations.
"""
import hashlib
import json
import re
import secrets
from pathlib import Path


def prepare(root):
    env_path=root/'.env';lines=env_path.read_text().splitlines()
    values=dict(line.split('=',1) for line in lines if '=' in line and not line.lstrip().startswith('#'))
    changes={}
    runtime=values.get('GCOR_DB_USER') or 'gcor_runtime'
    if not re.fullmatch('[a-z_][a-z0-9_]{0,62}',runtime) or runtime in {'buzz','postgres',values.get('POSTGRES_USER')}:
        raise ValueError('Choose a distinct non-owner runtime role')
    changes['GCOR_DB_USER']=runtime
    password=values.get('GCOR_DB_PASSWORD','')
    if not password or password.startswith('change-me'):password=secrets.token_hex(32)
    changes['GCOR_DB_PASSWORD']=password
    token=values.get('PROJECTOR_WORKLOAD_TOKEN','')
    if not token or token.startswith('change-me'):token=secrets.token_hex(32)
    changes['PROJECTOR_WORKLOAD_TOKEN']=token
    configured=values.get('GCOR_WORKLOAD_CREDENTIALS','[]').strip().strip("'")
    workloads=json.loads(configured)
    digest=hashlib.sha256(token.encode()).hexdigest()
    if not any(w.get('sha256')==digest for w in workloads):
        workloads.append({'sha256':digest,'subject':'buzz-event-projector','operations':['ingest']})
    elif not any(w.get('sha256')==digest and 'ingest' in w.get('operations',[]) for w in workloads):
        raise ValueError('Existing projector workload identity does not permit ingestion')
    changes['GCOR_WORKLOAD_CREDENTIALS']=json.dumps(workloads,separators=(',',':'))
    # Replace every occurrence to avoid ambiguous duplicate environment settings.
    for key,value in changes.items():
        indexes=[i for i,line in enumerate(lines) if line.startswith(key+'=')]
        if indexes:
            for i in indexes:lines[i]=key+'='+value
        else:lines.append(key+'='+value)
    env_path.write_text('\n'.join(lines)+'\n')
    plan_path=root/'config/windows-startup.compose-files.json'
    plan=json.loads(plan_path.read_text())
    for filename in ('docker-compose.knowledge-trust.yml','docker-compose.endpoint-observability.yml'):
        if filename not in plan['files']:plan['files'].append(filename)
    for service in ('gcor-enterprise','buzz-knowledge','docker-socket-proxy'):
        if service not in plan['required_services']:plan['required_services'].append(service)
    plan_path.write_text(json.dumps(plan,indent=2)+'\n')
    print('Prepared runtime credentials, projector identity and the canonical knowledge trust plan. No secrets displayed.')


if __name__=='__main__':prepare(Path(__file__).resolve().parents[1])
