"""Narrow Docker boundary: sanitized inventory and labeled, restart-only actions."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from main import DockerClient, LABEL_PREFIX


def authorized(container, stack, restart=False):
    labels=container.get('Config',{}).get('Labels') or {}
    return (labels.get(LABEL_PREFIX+'enabled')=='true' and labels.get(LABEL_PREFIX+'stack')==stack
            and (not restart or labels.get(LABEL_PREFIX+'action')=='restart'))


def sanitized(container):
    return {key:container.get(key) for key in ('Id','Name','State')} | {
        'Config':{'Labels':container.get('Config',{}).get('Labels') or {}}}


class Boundary:
    def __init__(self, docker, stack):
        self.docker,self.stack=docker,stack

    def dispatch(self, method, path):
        parsed=urlsplit(path)
        if method=='GET' and parsed.path=='/_ping':return 200,{'status':'ok'}
        if method=='GET' and parsed.path=='/containers/json':
            rows=self.docker.managed_containers(self.stack)
            return 200,[{'Id':r['Id']} for r in rows if authorized(r,self.stack)]
        match=re.fullmatch(r'/containers/([a-f0-9]{64})/(json|restart)',parsed.path)
        if not match or (method,match[2]) not in {('GET','json'),('POST','restart')}:
            return 403,{'error':'Operation denied'}
        container=self.docker.request('GET',f'/containers/{match[1]}/json')
        if not authorized(container,self.stack,restart=method=='POST'):
            return 403,{'error':'Container is outside the permitted recovery scope'}
        if method=='GET':return 200,sanitized(container)
        self.docker.restart(match[1],20)
        return 200,{'status':'restarted'}


def run():
    boundary=Boundary(DockerClient('/var/run/docker.sock'),os.getenv('RECOVERY_STACK_ID','gbuzz'))
    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            try:
                if int(self.headers.get('Content-Length','0')) or self.headers.get('Transfer-Encoding'):
                    status,payload=403,{'error':'Request bodies are not allowed'}
                else:status,payload=boundary.dispatch(self.command,self.path)
            except Exception:
                status,payload=502,{'error':'Docker operation failed'}
            data=json.dumps(payload).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        do_GET=do_POST=do_PUT=do_DELETE=do_PATCH=handle_request
        def log_message(self,*args):pass
    ThreadingHTTPServer(('0.0.0.0',2375),Handler).serve_forever()


if __name__=='__main__':run()
