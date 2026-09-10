"""NIP-98 proof of key ownership with live Buzz channel authorization."""
import base64
import hashlib
import json
import time
from urllib.parse import urlsplit
from uuid import UUID

from coincurve import PublicKeyXOnly

from access_policy import Principal


def verify_event(token: bytes, url: str, method: str, body: bytes, now=None) -> str:
    event = json.loads(base64.b64decode(token, validate=True))
    if not isinstance(event, dict):
        raise ValueError("Invalid event")
    if type(event.get('created_at')) is not int or type(event.get('kind')) is not int:
        raise ValueError("Invalid event types")
    if event['kind'] != 27235 or abs((time.time() if now is None else now)-event['created_at']) > 60:
        raise ValueError("Invalid kind or expired event")
    if event.get('content') != '':
        raise ValueError("HTTP auth content must be empty")
    for field, size in [('id',64),('pubkey',64),('sig',128)]:
        value=event.get(field)
        if not isinstance(value,str) or len(value)!=size or any(c not in '0123456789abcdef' for c in value):
            raise ValueError("Invalid event encoding")
    tags=event.get('tags')
    if not isinstance(tags,list) or not all(isinstance(t,list) and t and all(isinstance(v,str) for v in t) for t in tags):
        raise ValueError("Invalid tags")
    for name, expected in [('u',url),('method',method),('payload',hashlib.sha256(body).hexdigest())]:
        matches=[t for t in tags if t[0]==name]
        if matches != [[name,expected]]:
            raise ValueError("Missing, duplicate or mismatched request binding")
    canonical=json.dumps([0,event['pubkey'],event['created_at'],event['kind'],tags,event['content']],
                         ensure_ascii=False,separators=(',',':')).encode('utf-8')
    digest=hashlib.sha256(canonical).digest()
    if digest.hex()!=event['id'] or not PublicKeyXOnly(bytes.fromhex(event['pubkey'])).verify(bytes.fromhex(event['sig']),digest):
        raise ValueError("Invalid event signature")
    return event['pubkey']


class BuzzIdentity:
    def __init__(self, app, origin: str):
        parsed=urlsplit(origin)
        if parsed.scheme not in {'http','https'} or not parsed.netloc or parsed.path not in {'','/'} or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("GCOR_PUBLIC_ORIGIN must be an absolute HTTP(S) origin")
        self.app=app
        self.origin=origin.rstrip('/')

    async def authenticate(self, token: bytes, scope, body: bytes) -> Principal:
        path=scope.get('raw_path',scope['path'].encode()).decode('ascii')
        query=scope.get('query_string',b'').decode('ascii')
        url=self.origin+path+('?' + query if query else '')
        pubkey=verify_event(token,url,scope['method'],body)
        payload=json.loads(body)
        if scope['path']=='/api/workspace/channels':
            return Principal(pubkey,'','public')
        channel=UUID(payload['channel_id'])
        # Membership is rechecked on every request; no authorization cache.
        # Explicit membership is required even for public channels in this pilot.
        row=await self.app.state.pool.fetchrow('''
            SELECT c.visibility::text AS visibility,m.role::text AS role
            FROM public.channels c
            JOIN public.channel_members m ON m.channel_id=c.id AND m.community_id=c.community_id
            JOIN public.users u ON u.community_id=c.community_id AND u.pubkey=m.pubkey
            WHERE c.id=$1 AND m.pubkey=$2 AND m.removed_at IS NULL
              AND u.deactivated_at IS NULL AND c.deleted_at IS NULL
              AND c.archived_at IS NULL
        ''',channel,bytes.fromhex(pubkey),timeout=5)
        if row is None:
            raise PermissionError("Active Buzz membership required")
        level='public' if row['visibility']=='public' else 'private'
        return Principal(pubkey,str(channel),level,role=row.get('role','reader'))
