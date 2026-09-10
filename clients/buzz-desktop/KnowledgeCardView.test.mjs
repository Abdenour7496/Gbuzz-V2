import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
const dom=new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window=dom.window;globalThis.document=dom.window.document;
globalThis.HTMLElement=dom.window.HTMLElement;
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
const React=await import('react');
const {render,fireEvent,cleanup}=await import('@testing-library/react');
const {KnowledgeCardView}=await import('./KnowledgeCardView.tsx');
const card={version:1,kind:'proposal',title:'Recovery policy',summary:'<img src=x onerror=evil()>',state:'proposed',document_id:'id',revision:'revision',sources:['ab'.repeat(32)]};

test('revision and linked-page controls dispatch explicit proposals',()=>{
  const actions=[];
  const view=render(React.createElement(KnowledgeCardView,{card:{...card,state:'approved',links:[{id:'linked',title:'Related procedure'}]},busy:false,canReview:false,notice:'',onAction:(...a)=>actions.push(a),onSource:()=>{}}));
  fireEvent.click(view.getByRole('button',{name:'Related procedure'}));
  fireEvent.click(view.getByRole('button',{name:'Propose revision'}));
  fireEvent.change(view.getByRole('textbox',{name:'Proposed replacement text'}),{target:{value:'Verify restoration every week.'}});
  fireEvent.click(view.getByRole('button',{name:'Submit revision proposal'}));
  assert.deepEqual(actions,[['open','linked'],['revise','Verify restoration every week.']]);
  cleanup();
});

test('review controls are hidden for non-reviewers and locked while pending',()=>{
  const base={card,busy:false,canReview:false,notice:'',onAction:()=>{},onSource:()=>{}};
  const view=render(React.createElement(KnowledgeCardView,base));
  assert.equal(view.queryByRole('button',{name:'Approve'}),null);
  assert.equal(view.container.querySelector('img'),null);
  view.rerender(React.createElement(KnowledgeCardView,{...base,canReview:true,busy:true}));
  assert.ok(view.getByRole('button',{name:'Approve'}).disabled);
  cleanup();
});

test('buttons dispatch fixed actions, source navigation and question submission',()=>{
  const actions=[],sources=[];
  const view=render(React.createElement(KnowledgeCardView,{card,busy:false,canReview:true,notice:'',onAction:(...a)=>actions.push(a),onSource:id=>sources.push(id)}));
  fireEvent.click(view.getByRole('button',{name:'Approve'}));
  fireEvent.click(view.getByRole('button',{name:'Inspect latest'}));
  fireEvent.click(view.getByRole('button',{name:/Source 1/}));
  fireEvent.click(view.getByRole('button',{name:'Ask a question'}));
  fireEvent.change(view.getByRole('textbox',{name:'Knowledge question'}),{target:{value:'How do restores work?'}});
  fireEvent.click(view.getByRole('button',{name:'Ask',exact:true}));
  assert.deepEqual(actions,[['approve'],['inspect'],['ask','How do restores work?']]);
  assert.deepEqual(sources,card.sources);
  cleanup();
});
