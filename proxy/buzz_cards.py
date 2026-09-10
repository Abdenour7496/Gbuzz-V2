"""Versioned, inert card payloads. Desktop constructs commands from a fixed allowlist."""
import json


class ChatReply(str):
    def __new__(cls, text, *, kind, title, document_id=None, revision=None, state=None, sources=None, links=None, saveable=False):
        value=super().__new__(cls,text)
        value.card={'version':1,'kind':kind,'title':title[:300],
                    'summary':text[:7000],'sources':(sources or [])[:6]}
        if document_id:value.card['document_id']=str(document_id)
        if revision:value.card['revision']=revision
        if state:value.card['state']=state
        if links:value.card['links']=links[:6]
        if saveable:value.card['saveable']=True
        return value


def card_tags(reply, channel_id):
    if not isinstance(reply,ChatReply):return []
    return [['gcor-card',json.dumps(reply.card|{'channel_id':str(channel_id)},ensure_ascii=False,separators=(',',':'))]]
