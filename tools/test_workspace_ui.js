#!/usr/bin/env node
/* Run the shipped workspace functions against a small synthetic DOM; no server or real data. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const source = fs.readFileSync(path.join(__dirname, '../static/workspace/workspace.js'), 'utf8');

function harness(overrides = {}) {
  const nodes = new Map(), calls = [], rendered = [];
  class Element {
    constructor(id = '', tagName = 'div') { this.id=id; this.tagName=tagName; this.handlers={}; this.dataset={}; this.value=''; this.files=[]; this.disabled=false; this.hidden=false; this.open=false; this.elements={}; this.children=[]; this.childIds=[]; }
    set innerHTML(html) {
      this.html=html;
      for (const id of this.childIds) nodes.delete('#'+id);
      this.childIds=[];
      for (const match of html.matchAll(/<([a-z]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
        const element = new Element(match[2],match[1]); nodes.set('#'+match[2],element); this.childIds.push(match[2]);
        if (/\bdisabled\b/.test(match[0])) element.disabled=true;
      }
    }
    get innerHTML() { return this.html || ''; }
    addEventListener(type, fn) { this.handlers[type]=fn; }
    querySelector(selector) { return document.querySelector(selector); }
    querySelectorAll(selector) { return document.querySelectorAll(selector); }
    setAttribute(name,value) { this[name]=value; }
    focus() {} select() {} reportValidity() { return true; }
    showModal() { this.open=true; } close() { this.open=false; }
    append(...items) { this.children.push(...items); for (const item of items) if (item.id) nodes.set('#'+item.id,item); }
    after(item) { if (item.id) nodes.set('#'+item.id,item); }
    remove() { nodes.delete('#'+this.id); }
  }
  const document = {
    body:new Element('body'), cookie:'', activeElement:null,
    getElementById(id) { return nodes.get('#'+id) || null; },
    querySelector(selector) { return nodes.get(selector) || null; },
    querySelectorAll(selector) {
      if (selector === '#page-content button') return [...nodes.values()].filter(n=>n.tagName==='button');
      return [];
    },
    createElement(tag) { return new Element('',tag); }, addEventListener() {},
  };
  document.body.dataset={};
  const initial = {page:'record',actor:{role:'owner',id:1},classroom:{id:1,code:'1',name:'合成甲班',revision:1},students:[],today:'2026-10-05',term:{key:'2026-autumn',label:'2026秋'},...overrides};
  for (const id of ['workspace-data','page-content','app-toast','reauth-form','auth-dialog','action-dialog','credential-dialog','copy-dialog','class-switch','term-switch','public-report-panel','bulk-message','bulk-panel','roster-results']) nodes.set('#'+id,new Element(id));
  nodes.get('#workspace-data').textContent=JSON.stringify(initial);
  const location = {href:'https://audit.invalid/classes/1/records/new/',assign(url){this.href=new URL(url,this.href).href;calls.push(['assign',url]);}};
  const context = {
    document,location,window:{addEventListener(){}},history:{replaceState(_,__,url){location.href=new URL(url,location.href).href;calls.push(['replace',url]);}},
    Intl,URL,Map,Set,console,Blob,FormData,AbortController,navigator:{clipboard:{writeText:async()=>{}}},
    crypto:require('node:crypto').webcrypto,setTimeout:()=>0,clearTimeout(){},confirm:()=>true,
  };
  const exports = `
    globalThis.audit={
      switchRecordClass,recordContextUrl,refreshRosterContext,renderPublicReport,changePublicReport,refreshPublicReport,
      activityCount,lastActivity,displayTime,recordsTable,copyText,classDangerZone,setupClassDangerZone,eventsPage,
      renderPolicyPreview,itemTermQuery,hasUnsavedChanges,classPage,
      getData:()=>data,setData:value=>{data=value;},
      getShare:()=>({busy:publicReportBusy,pending:publicReportPending,needsRefresh:publicReportNeedsRefresh}),
      getRoster:()=>({busy:rosterRefreshing,needsRefresh:rosterRefreshRequired}),
      setRequest:fn=>{request=fn;},setModal:fn=>{modalForm=fn;},
      setRenders:fn=>{recordPage=fn;rosterPage=fn;updateClassNavigation=()=>{};},ApiError
    };
  })();`;
  vm.createContext(context);vm.runInContext(source.slice(0,source.indexOf('  hydrateIcons(); updateExpiry();'))+exports,context);
  context.audit.setRenders(()=>rendered.push(context.audit.getData()));
  return {api:context.audit,context,nodes,calls,rendered,Element};
}
function deferred() { let resolve,reject;const promise=new Promise((ok,no)=>{resolve=ok;reject=no;});return {promise,resolve,reject}; }
const cases=[];
function test(name,run) { cases.push([name,run]); }

if (process.argv.includes('--time-probe')) {
  const {api}=harness();
  console.log(JSON.stringify(['2026-10-05T10:00:00','2026-10-05T10:00:00+08:00','2026-10-05T02:00:00Z'].map(value=>api.displayTime(value))));
  process.exit(0);
}

test('switching class synchronizes the refresh target, while a failed switch preserves it',async()=>{
  const {api,context}=harness();
  api.setRequest(async()=>({actor:{role:'owner',id:1},classroom:{id:2,code:'2',name:'乙'},students:[],term:{key:'2026-autumn'}}));
  await api.switchRecordClass('2');
  assert.equal(api.getData().classroom.code,'2');
  assert.equal(new URL(context.location.href).pathname,'/classes/2/records/new/');
  assert.equal(api.recordContextUrl(),'/classes/2/records/new/?term=2026-autumn');
  api.setRequest(async()=>{throw new api.ApiError('unavailable',503);});
  await api.switchRecordClass('3');
  assert.equal(api.getData().classroom.code,'2');assert.equal(new URL(context.location.href).pathname,'/classes/2/records/new/');
});

test('sharing permits one in-flight mutation and hides stale credentials',async()=>{
  const {api,nodes}=harness({public_report:{active:true,status:'active',revision:4,url:'synthetic-old',can_rotate:true,can_disable:true}});
  const wait=deferred(),submitted=[];api.setRequest(async(_url,body)=>{submitted.push(body);return wait.promise;});
  const first=api.changePublicReport('rotate');await api.changePublicReport('rotate');await api.changePublicReport('disable');
  assert.equal(submitted.length,1);assert.equal(submitted[0].revision,4);assert(submitted[0].submission_id);
  assert.equal(api.getShare().busy,true);assert(!nodes.get('#public-report-panel').innerHTML.includes('copy-public-report'));
  assert(!nodes.get('#public-report-panel').innerHTML.includes('synthetic-old'));assert(api.hasUnsavedChanges());
  wait.resolve({active:true,status:'active',revision:5,url:'synthetic-new',can_rotate:true,can_disable:true});await first;
  assert.equal(api.getData().public_report.url,'synthetic-new');assert.equal(api.getShare().pending,null);
});

test('an ended class retains the teacher current-report revocation controls',()=>{
  const {api,nodes}=harness({page:'class',historical:true,readonly:true,classroom:{id:1,code:'1',name:'已结束合成班',archived:true},public_report:{active:true,status:'active',revision:1,url:'synthetic-report',can_disable:true,can_rotate:true}});
  api.classPage();assert(nodes.get('#page-content').innerHTML.includes('public-report-panel'));
  const sharing=nodes.get('#public-report-panel').innerHTML;assert(sharing.includes('当前学期公开报告'));assert(sharing.includes('id="disable-public-report"'));
});

test('uncertain sharing retries exactly the original submission and displays the current receipt',async()=>{
  const {api}=harness({public_report:{active:true,status:'active',revision:8,url:'old',can_rotate:true}});
  const submitted=[];let count=0;
  api.setRequest(async(_url,body)=>{submitted.push(JSON.stringify(body));if(!count++)throw new api.ApiError('network');return {active:false,status:'owner_disabled',revision:10,replayed:true,can_enable:true};});
  await api.changePublicReport('rotate');assert(api.getShare().pending);assert(!api.getShare().busy);
  await api.changePublicReport();assert.equal(submitted[0],submitted[1]);assert.equal(api.getData().public_report.status,'owner_disabled');assert.equal(api.getShare().pending,null);
});

test('sharing conflict fetches current state without automatically resubmitting',async()=>{
  const {api,nodes}=harness({actor:{role:'committee',id:3},public_report:{active:false,status:'never_enabled',revision:0,can_enable:true}});
  const methods=[];api.setRequest(async(_url,_body,method='POST')=>{methods.push(method);if(method==='POST')throw new api.ApiError('changed',409);return {active:false,status:'owner_disabled',revision:3,can_enable:false,can_rotate:false,can_disable:false};});
  await api.changePublicReport('enable');
  assert.deepEqual(methods,['POST','GET']);assert.equal(api.getShare().pending,null);
  assert(nodes.get('#public-report-panel').innerHTML.includes('班委不能重新启用'));assert(!nodes.get('#public-report-panel').innerHTML.includes('id="enable-public-report"'));
});

test('post-import refresh replaces the entire context; failure keeps old revision and blocks stale actions',async()=>{
  const {api,nodes,Element,rendered}=harness();nodes.set('#fake-danger',new Element('fake-danger','button'));
  const previous=api.getData();api.setRequest(async()=>{throw new api.ApiError('offline');});await api.refreshRosterContext('保存成功。');
  assert.equal(api.getData(),previous);assert.equal(api.getRoster().needsRefresh,true);assert.equal(nodes.get('#fake-danger').disabled,true);assert(nodes.has('#bulk-state-refresh'));
  const fresh={...previous,classroom:{...previous.classroom,revision:2},students:[{id:101,name:'合成学生',number:'001'}],class_management:{student_count:1,record_count:0,class_revision:2,report_revision:4}};
  api.setRequest(async()=>fresh);await api.refreshRosterContext('保存成功。');assert.equal(api.getData(),fresh);assert.equal(rendered.length,1);assert.equal(api.getRoster().needsRefresh,false);
});

test('dangerous confirmation binds the fetched scope and never a later live revision',async()=>{
  const {api,nodes,Element}=harness();nodes.set('#archive-class',new Element('archive-class','button'));nodes.set('#class-management-message',new Element('class-management-message'));
  const preview={class_id:1,term_key:'2026-autumn',class_revision:3,report_revision:7,record_count:12,student_count:30,freeze_date:'2026-10-05',freeze_months:['2026-09','2026-10'],can_archive:true};
  let modal;api.setRequest(async()=>preview);api.setModal(value=>{modal=value;});api.setupClassDangerZone();await nodes.get('#archive-class').handlers.click();
  assert(modal.fields.includes('30 人'));assert(modal.fields.includes('12 条'));assert(modal.fields.includes('2026-10-05'));
  api.getData().classroom.revision=99;
  const payload=modal.build({elements:{confirmation:{value:'合成甲班'}}});
  assert.equal(payload.revision,3);assert.equal(payload.report_revision,7);assert.equal(payload.freeze_date,'2026-10-05');
});

test('unknown historical activity is distinct from a known zero and row links use their own term',()=>{
  const {api}=harness();assert.equal(api.activityCount({activity_count:null,records_unavailable:true}),'—');assert.equal(api.lastActivity({records_unavailable:true}),'历史记录待核实');assert.equal(api.activityCount({activity_count:0}), '0');assert.equal(api.lastActivity({activity_count:0}),'暂无活动');assert.equal(api.itemTermQuery({term_key:'2025-autumn'}),'?term=2025-autumn');
});

test('mobile records retain semantic person counts and student state',()=>{
  const {api}=harness();const rows=[{id:1,name:'合成记录',date:'2026-10-05',kind:'class',student_count:2},{id:2,name:'合成活动',date:'2026-10-05',kind:'activity',student_count:3}];
  const html=api.recordsTable(rows);assert(html.includes('mobile-record-status'));assert(html.includes('异常 2 人'));assert(html.includes('计分 3 人'));
  assert(api.recordsTable([{...rows[0],value:'late'}],{studentMode:true}).includes('本次：迟到'));
});

test('clipboard refusal reports manual copying instead of success with correct content label',async()=>{
  const {api,context,nodes}=harness();context.navigator.clipboard.writeText=async()=>{throw Error('denied');};
  const result=await api.copyText('synthetic-link',{label:'访问链接'});assert.equal(result.copied,false);assert(nodes.get('#copy-dialog').innerHTML.includes('手动复制访问链接'));assert(nodes.get('#copy-dialog').innerHTML.includes('尚未写入剪贴板'));assert.equal(nodes.get('#manual-copy-text').value,'synthetic-link');
});

test('event labels and units distinguish record rows from people',()=>{
  const {api,nodes}=harness({events:[{kind:'class_data_cleared',summary:'合成清空',time:'2026-10-05T10:00:00+08:00',affected_count:3,affected_unit:'条'},{kind:'public_report_restored',summary:'合成恢复',affected_count:0,affected_unit:'个'}]});
  api.eventsPage();const html=nodes.get('#page-content').innerHTML;assert(html.includes('影响 3 条'));assert(html.includes('恢复公开报告'));
});

test('Beijing wall time and explicit offsets agree across local and DST timezones',()=>{
  const outputs=['Asia/Shanghai','UTC','America/New_York'].map(TZ=>{
    const child=spawnSync(process.execPath,[__filename,'--time-probe'],{env:{...process.env,TZ},encoding:'utf8'});assert.equal(child.status,0,child.stderr);return JSON.parse(child.stdout);
  });
  for (const output of outputs) { assert.deepEqual(output,outputs[0]);assert.equal(new Set(output).size,1);assert(output[0].endsWith('10:00')); }
});

(async()=>{let passed=0;for(const [name,run] of cases){await run();passed++;console.log(`PASS ${name}`);}console.log(`${passed} frontend state checks passed`);})().catch(error=>{console.error(error);process.exitCode=1;});
