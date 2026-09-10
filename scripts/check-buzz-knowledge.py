"""Authenticate the configured Knowledge agent without posting any chat messages."""
import asyncio
import json
import os
from urllib.parse import urlsplit
from coincurve import PrivateKey
from websockets.asyncio.client import connect
from buzz_chat import sign_event


async def run():
    key=PrivateKey(bytes.fromhex(os.environ['BUZZ_KNOWLEDGE_PRIVATE_KEY']))
    url=os.getenv('BUZZ_KNOWLEDGE_RELAY_URL','ws://relay:3000')
    authority=os.getenv('BUZZ_KNOWLEDGE_AUTH_URL',url);target=urlsplit(url)
    async with asyncio.timeout(15):
        async with connect(authority,host=target.hostname,port=target.port or 80,proxy=None) as socket:
            async for frame in socket:
                message=json.loads(frame)
                if message[0]=='AUTH':
                    await socket.send(json.dumps(['AUTH',sign_event(key,22242,[['relay',authority],['challenge',message[1]]],'')]))
                elif message[0]=='OK':
                    if not message[2]:raise RuntimeError('Agent authentication rejected')
                    print('Knowledge agent authenticated with the live Buzz relay. No chat message posted.')
                    return
    raise RuntimeError('Authentication did not complete')


if __name__=='__main__':asyncio.run(run())
