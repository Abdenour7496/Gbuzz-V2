import assert from 'node:assert/strict';
import test from 'node:test';
import { parseKnowledgeCard, knowledgeCommand } from './knowledgeCardModel.ts';
const channel='11111111-1111-4111-8111-111111111111', signer='ab'.repeat(32);
const payload={version:1,kind:'proposal',channel_id:channel,title:'Recovery decision',summary:'Weekly restore drills.',sources:[],document_id:channel,revision:'2026-09-08T12:00:00.123456+00:00',state:'proposed'};
const message={kind:9,signerPubkey:signer,tags:[['h',channel],['gcor-card',JSON.stringify(payload)]]};

test('wiki actions remain bound to validated document and answer identifiers',()=>{
  const approved={...payload,state:'approved',links:[{id:channel,title:'Procedure'}]};
  assert.equal(knowledgeCommand(approved,'revise','New procedure'),`!knowledge revise ${channel} ${payload.revision} | New procedure`);
  assert.equal(knowledgeCommand(approved,'open',channel),`!knowledge show ${channel}`);
  assert.throws(()=>knowledgeCommand(approved,'open','https://example.com'));
  assert.throws(()=>knowledgeCommand(payload,'revise','Changed'));
  assert.equal(knowledgeCommand({...payload,kind:'answer',saveable:true},'save',signer),`!knowledge save ${signer}`);
  assert.throws(()=>knowledgeCommand({...payload,kind:'answer'},'save',signer));
  assert.equal(parseKnowledgeCard({...message,tags:[['h',channel],['gcor-card',JSON.stringify({...payload,links:[{id:'javascript:bad',title:'Bad'}]})]]},channel,signer),null);
});
test('accepts only pinned signer and bound channel',()=>{
  assert.ok(parseKnowledgeCard(message,channel,signer));
  assert.equal(parseKnowledgeCard({...message,signerPubkey:'cd'.repeat(32)},channel,signer),null);
  assert.equal(parseKnowledgeCard(message,channel,undefined),null);
  assert.equal(parseKnowledgeCard(message,'22222222-2222-4222-8222-222222222222',signer),null);
  assert.equal(parseKnowledgeCard({...message,edited:true},channel,signer),null);
});
test('rejects duplicate, malformed and oversized card envelopes',()=>{
  assert.equal(parseKnowledgeCard({...message,tags:[...message.tags,message.tags[1]]},channel,signer),null);
  for(const patch of [{version:2},{revision:'now\n!knowledge archive'},{document_id:'bad'},{sources:['javascript:evil']},{summary:'x'.repeat(7001)}]) {
    assert.equal(parseKnowledgeCard({...message,tags:[['h',channel],['gcor-card',JSON.stringify({...payload,...patch})]]},channel,signer),null);
  }
});
test('constructs revision-bound commands and rejects arbitrary actions',()=>{
  const card=parseKnowledgeCard(message,channel,signer);
  assert.equal(knowledgeCommand(card,'approve'),`!knowledge approve ${channel} ${payload.revision}`);
  assert.equal(knowledgeCommand(card,'ask','  restore?  '),'!knowledge ask restore?');
  assert.throws(()=>knowledgeCommand(card,'execute'));
  assert.throws(()=>knowledgeCommand({...card,state:'approved'},'approve'));
});
