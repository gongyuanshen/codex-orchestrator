import pathlib
import re
import subprocess
from html.parser import HTMLParser

import pytest

APP = pathlib.Path(__file__).parents[1] / 'plugin/src/orchestrator/web/app.js'

def test_dashboard_control_behavior():
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
function fn(name) {
 const start = source.indexOf('  function ' + name + '(');
 const end = source.indexOf('\n  function ', start + 1);
 assert.ok(start >= 0, name + ' missing');
 return source.slice(start, end);
}
const state = {run:{status:'active'}, workers:[{id:'w',status:'working',agent_id:'a'}], controlRequests:[]};
const ctx = {state, getHeartbeatAgeSec:()=>400, renderInspector:()=>{}};
vm.createContext(ctx);
for (const name of ['escapeHtml','isWorkerStalled','upsertControlRequest','renderWorkerControl','acceptControlResponse']) vm.runInContext(fn(name),ctx);
const req={id:'r',run_id:'run',worker_id:'w',agent_id:'a',requested_status:'stopped',status:'pending',result:null};
ctx.acceptControlResponse({ok:true,control_request:req,workspace_preserved:true},'w','stopped');
assert.equal(state.workers[0].status,'working');
assert.equal(state.controlRequests[0].status,'pending');
ctx.upsertControlRequest({...req,status:'failed',result:'<img onerror="bad">'});
assert.ok(ctx.renderWorkerControl(state.workers[0]).includes('&lt;img'));
assert.ok(!ctx.renderWorkerControl(state.workers[0]).includes('<img'));
assert.throws(()=>ctx.acceptControlResponse({ok:true},'w','stopped'));
assert.equal(ctx.isWorkerStalled({status:'stopped',stalled_alerted:1}),false);
const toggles = {};
const node = {getAttribute:(key)=>({'data-status':'stopped','data-alerted':'1'}[key]),classList:{toggle:(key,value)=>toggles[key]=value},closest:()=>null};
ctx.el = {workersList:{querySelectorAll:()=>[node]}};
ctx.formatAge = String;
vm.runInContext(fn('tickHeartbeats'),ctx);
ctx.tickHeartbeats();
assert.equal(toggles['tint-danger'],false);
state.run.status='completed';
node.getAttribute=(key)=>({'data-status':'working','data-alerted':'1'}[key]);
ctx.tickHeartbeats();
assert.equal(toggles['tint-danger'],false);
assert.equal(ctx.isWorkerStalled({status:'working',stalled_alerted:1}),false);
'''
    result = subprocess.run(['node', '-e', script, str(APP)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

def test_switch_run_refreshes_heading():
    run_dashboard(r'''(async()=>{
      state.run={id:'old',title:'Old title',status:'active'};
      state.selectedRunId='old';state.runs=[state.run,{id:'new',title:'New title',status:'completed'}];
      const selected=selectRun('new');
      requests[0].resolve({run:{id:'new',project_id:'p',mode:'swarm',title:'Snapshot title',status:'completed'},workers:[],control_requests:[]});
      await selected;
      assert.equal(state.run.id,'new');
      assert.equal(el.runTitleHeading.textContent,'Snapshot title');
      assert.equal(el.runTitleHeading.title,'Snapshot title (new)');
      assert.equal(el.runStatusBadge.textContent,labelRunStatus('completed'));
      assert.ok(streams[0].url.includes('run_id=new'));
    })()''')


# Load the real browser module in a tiny DOM shell; only browser IO is substituted.
HARNESS = r"""
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
function node() {
 const classes=new Set(), attributes={}, events={};
 return {innerHTML:'',textContent:'',value:'',style:{},dataset:{},disabled:false,isConnected:true,inert:false,scrollTop:0,scrollHeight:0,clientHeight:100,
 classList:{add:(x)=>classes.add(x),remove:(x)=>classes.delete(x),contains:(x)=>classes.has(x),toggle:(x,v)=>v?classes.add(x):classes.delete(x)},
 setAttribute(k,v){attributes[k]=String(v)},removeAttribute(k){delete attributes[k]},getAttribute(k){return attributes[k]??null},
 addEventListener(type,listener){(events[type]??=[]).push(listener)},dispatchEvent(event){for(const listener of events[event.type]||[])listener(event)},
 querySelectorAll(){return []},querySelector(q){return q==='.toast-close'?node():null},
 appendChild(){},getBoundingClientRect(){return {width:1000,height:600}},getClientRects(){return [{}]},closest(){return null},focus(){document.activeElement=this},contains(target){return this===target}};
}
const nodes=new Map(), requests=[], streams=[], intervals=new Map();let nextInterval=0;
const links=['boost','teamwork','swarm'].flatMap(mode=>['chooser','nav'].map(kind=>{const link=node();link.dataset.modeLink=mode;nodes.set(kind+'-'+mode,link);return link}));
const tabs=['overview','board','members','graph'].map(name=>{const tab=node();tab.setAttribute('data-tab',name);return tab});
const background=[node(),node(),node()];
const document=Object.assign(node(),{activeElement:null,getElementById:(id)=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},querySelectorAll:(selector)=>selector==='[data-mode-link]'?links:selector==='.tab-item'?tabs:selector==='.mode-bar, .topbar, .main-pane'?background:[],createElement:node,documentElement:node()});
document.getElementById('exportMenu').classList.add('hidden');
class EventSource {static CONNECTING=0;constructor(url){this.url=url;this.events={};streams.push(this)}addEventListener(n,f){this.events[n]=f}close(){this.closed=true}}
const ctx={document,URLSearchParams,EventSource,console,Map,Set,Date,encodeURIComponent,setImmediate,requestAnimationFrame:(f)=>f(),setTimeout:()=>0,clearTimeout(){},setInterval:(f)=>{intervals.set(++nextInterval,f);return nextInterval},clearInterval:(id)=>intervals.delete(id),CSS:{escape:String},
 window:Object.assign(node(),{location:{search:process.argv[3]||'',pathname:process.argv[2]||'/swarm',replace(url){this.redirected=url}},history:{replaceState(_a,_b,url){ctx.window.location.pathname=url.split('?')[0];ctx.window.location.search=url.includes('?')?'?'+url.split('?')[1]:''}},matchMedia:()=>({matches:ctx.window.reducedMotion===true}),innerWidth:1200}),
 fetch:(url)=>new Promise((resolve,reject)=>requests.push({url,resolve:(data)=>resolve({ok:true,headers:{get:()=> 'application/json'},json:async()=>data}),reject}))};
vm.createContext(ctx);
vm.runInContext(source.replace('  init();','  globalThis.runTest = (code) => eval(code);'),ctx);
ctx.assert=assert;ctx.requests=requests;ctx.streams=streams;ctx.nodes=nodes;ctx.intervals=intervals;ctx.testNode=node;ctx.background=background;
async function run(code){await ctx.runTest(code)}
"""


def run_dashboard(code, path='/swarm', search=''):
    result = subprocess.run(
        ['node', '-e', HARNESS + '\nconst timeout=setTimeout(()=>{console.error("Unresolved test");process.exit(1)},1500);run(' + repr(code) + ').then(()=>clearTimeout(timeout)).catch(e=>{clearTimeout(timeout);console.error(e);process.exitCode=1});', str(APP), path, search],
        capture_output=True, text=True, encoding='utf-8',
    )
    assert result.returncode == 0, result.stderr


def test_selection_clears_old_details_and_owns_aba_responses():
    run_dashboard(r'''(async()=>{
      state.selectedProjectId='p'; state.runs=[{id:'A',title:'A'},{id:'B',title:'B'}];
      state.run={id:'old',title:'Old'};state.messages=[{seq:1,body_md:'old'}];
      const a1=selectRun('A');
      assert.equal(state.run,null);assert.equal(state.messages.length,0);
      assert.equal(el.humanSendBtn.disabled,true);assert.equal(state.loadState,'loading');
      const b=selectRun('B'), a2=selectRun('A');
      requests[2].resolve({run:{id:'A',project_id:'p',mode:'swarm',title:'Newest A'},workers:[],messages:[]});await a2;
      requests[1].resolve({run:{id:'B',project_id:'p',mode:'swarm',title:'Late B'},workers:[],messages:[]});await b;
      requests[0].resolve({run:{id:'A',project_id:'p',mode:'swarm',title:'Old A'},workers:[],messages:[]});await a1;
      assert.equal(state.run.title,'Newest A');assert.equal(streams.length,1);assert.ok(streams[0].url.includes('run_id=A'));
      const oldStream=streams[0];const next=selectRun('B');assert.equal(oldStream.closed,true);
      oldStream.events.board_message({data:JSON.stringify({seq:9,run_id:'B',body_md:'stale stream'})});
      assert.equal(state.messages.length,0);
      requests[3].reject(new Error('snapshot offline'));await next;
      assert.equal(state.loadState,'error');assert.ok(el.contextStatus.textContent.includes('snapshot offline'));
      assert.equal(el.humanSendBtn.disabled,true);assert.equal(state.run,null);assert.equal(streams.length,1);
    })()''')


def test_project_lists_ignore_late_a_b_a_requests():
    run_dashboard(r'''(async()=>{
      const a1=loadRuns('A'), b=loadRuns('B'), a2=loadRuns('A');
      requests[2].resolve({runs:[]});await a2;
      requests[1].resolve({runs:[{id:'B-old'}]});await b;
      requests[0].resolve({runs:[{id:'A-old'}]});await a1;
      assert.equal(state.selectedProjectId,'A');assert.equal(state.runs.length,0);assert.equal(requests.length,3);
    })()''')


def test_log_is_summary_first_expandable_escaped_and_persistent():
    run_dashboard(r'''(()=>{
      assert.equal(state.activeTab,'overview');
      state.messages=[{seq:1,sender:'coder',summary:'<img src=x onerror=alert(1)>',body_md:'SECRET FULL BODY',channel:'main'}];
      renderBoardFeed();
      assert.ok(el.boardFeed.innerHTML.includes('<details'));assert.ok(!el.boardFeed.innerHTML.includes('<img'));
      assert.ok(el.boardFeed.innerHTML.includes('&lt;img'));assert.ok(!el.boardFeed.innerHTML.includes('SECRET FULL BODY'));
      state.expandedMessages.add('1');renderBoardFeed();
      assert.ok(el.boardFeed.innerHTML.includes(' open'));assert.ok(el.boardFeed.innerHTML.includes('SECRET FULL BODY'));
      upsertBoardMessage({seq:2,sender_role:'verifier',summary:'verified',body_md:'second'});
      assert.ok(el.boardFeed.innerHTML.includes('SECRET FULL BODY'));
      assert.equal(inferSenderRole({sender:'coder'}),'unknown');
      assert.equal(getWorkspaceUnmergedCount({id:'w'},{}),null);
      assert.ok(renderMemberRow({id:2,name:'x',workspace_id:'w'}).includes('职责未知'));
      assert.equal(messageSummary({body_md:'x'.repeat(500)}).length,161);
      assert.equal(messageSummary({}),'未填写摘要或正文');
    })()''')


def test_invalid_json_is_not_success():
    run_dashboard(r'''(async()=>{
      // Override the transport, preserving the production response parser.
      fetch=async()=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>{throw new SyntaxError('bad JSON')}});
      await assert.rejects(apiRequest('/bad'),/bad JSON/);
    })()''')


def test_old_human_receipt_cannot_clear_new_context_draft():
    run_dashboard(r'''(async()=>{
      state.selectedRunId='A';state.run={id:'A'};state.loadState='ready';
      el.humanChannelSelect.value='human';el.humanMessageInput.value='old draft';
      const sent=handleHumanSubmit({preventDefault(){}});
      const next=selectRun('B');el.humanMessageInput.value='new draft';
      requests[0].resolve({message:{seq:7,run_id:'A',body_md:'old draft'}});await sent;
      assert.equal(state.messages.length,0);assert.equal(el.humanMessageInput.value,'new draft');assert.equal(el.humanSendBtn.disabled,true);
      requests[1].resolve({run:{id:'B',project_id:'p',mode:'swarm'},workers:[],messages:[]});await next;
      assert.equal(state.loadState,'ready');
    })()''')


def test_overview_prioritizes_current_members_and_bounds_goal():
    run_dashboard(r'''(()=>{
      state.run={id:'A',status:'active'};state.loadState='ready';state.taskPacket={goal:'long goal '.repeat(300)};
      state.workers=[{id:1,role:'coder',name:'live',status:'working',current_task:'Fix'}, {id:2,role:'verifier',name:'paused',status:'stopped'}];
      renderOverview();
      assert.ok(el.overviewContent.innerHTML.includes('overview-goal-details'));
      assert.ok(el.overviewContent.innerHTML.includes('retired-members'));
      assert.ok(el.overviewContent.innerHTML.includes('overview-columns'));
      assert.ok(el.overviewContent.innerHTML.indexOf('@live') < el.overviewContent.innerHTML.indexOf('retired-members'));
    })()''')


def test_default_entry_selects_active_project_and_explicit_url_wins():
    run_dashboard(r'''(async()=>{
      const urls=[];
      const active={id:'active',project_id:'busy',mode:'swarm',status:'active'};
      const archive={id:'archive',project_id:'old',mode:'swarm',status:'completed'};
      const data={
        '/api/projects':{projects:[{id:'old',name:'Old'},{id:'busy',name:'Busy'}]},
        '/api/runs?status=active&mode=swarm':{runs:[active]},
        '/api/runs?project_id=busy&mode=swarm':{runs:[active]},
        '/api/runs?project_id=old&mode=swarm':{runs:[archive]},
        '/api/runs/archive':{run:archive,project:{id:'old'}},
        '/api/runs/active/snapshot':{run:active,workers:[],messages:[]},
        '/api/runs/archive/snapshot':{run:archive,workers:[],messages:[]},
      };
      fetch=async(url)=>{urls.push(url);assert.ok(url in data,url);return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data[url]}};
      await loadProjects();assert.equal(state.selectedProjectId,'busy');assert.equal(state.selectedRunId,'active');
      assert.equal(urls.filter(u=>u.endsWith('/snapshot')).length,1);
      state.selectedProjectId=null;state.selectedRunId=null;window.location.search='?project_id=old&run_id=archive';urls.length=0;
      await loadProjects();assert.equal(state.selectedProjectId,'old');assert.equal(state.selectedRunId,'archive');
      assert.ok(!urls.includes('/api/runs?status=active&mode=swarm'));
      assert.equal(state.loadState,'ready');
    })()''')


def test_late_project_list_cannot_overwrite_user_selection():
    run_dashboard(r'''(async()=>{
      const projects=loadProjects();const selected=selectRun('manual');
      requests[1].resolve({run:{id:'manual',project_id:'p',mode:'swarm'},workers:[],messages:[]});await selected;
      requests[0].resolve({projects:[{id:'other'}]});await projects;
      assert.equal(state.selectedRunId,'manual');assert.equal(requests.length,2);
      assert.equal(state.loadState,'ready');
    })()''')


def test_diff_is_unknown_until_requested_and_failure_is_visible():
    run_dashboard(r'''(async()=>{
      state.workers=[{id:'w',name:'w',workspace_id:'ws'}];state.selectedWorkerId='w';state.workspaces=[{id:'ws'}];
      renderInspector();assert.ok(!el.inspectorBody.innerHTML.includes('工作树干净'));
      upsertWorkspace({id:'ws',status:'active'});assert.equal(requests.length,0);
      const diff=loadWorkspaceDiff('ws');requests[0].reject(new Error('permission denied'));await diff;
      assert.ok(el.inspectorBody.innerHTML.includes('permission denied'));assert.ok(!el.inspectorBody.innerHTML.includes('工作树干净'));
    })()''')


def test_successful_human_send_preserves_new_text_in_same_task():
    run_dashboard(r'''(async()=>{
      state.run={id:'A'};state.selectedRunId='A';state.loadState='ready';el.humanMessageInput.value='sent';
      const sent=handleHumanSubmit({preventDefault(){}});el.humanMessageInput.value='next draft';
      requests[0].resolve({message:{seq:1,body_md:'sent'}});await sent;
      assert.equal(el.humanMessageInput.value,'next draft');assert.equal(state.messages.length,1);
    })()''')


def test_inspected_diff_overrides_old_backend_summary_count():
    run_dashboard(r'''(()=>{
      const ws={id:'ws',unmerged_files_count:0};const worker={id:'w',name:'w',workspace_id:'ws',unmerged_files_count:0};
      state.workspaces=[ws];state.diffByWorkspace.set('ws',{dirty_count:2,files:[{path:'a.py'},{path:'b.py'}]});
      assert.equal(getWorkspaceUnmergedCount(ws,worker),2);
      assert.ok(renderMemberRow(worker).includes('2 处变更'));
      assert.ok(!renderMemberRow(worker).includes('上次检查无变更'));
    })()''')


def test_root_chooser_does_not_load_dashboard_and_keeps_project_context():
    run_dashboard(r'''(async()=>{
      await init();
      assert.equal(requests.length,0);assert.equal(streams.length,0);
      assert.equal(el.dashboardShell.classList.contains('hidden'),true);
      assert.equal(el.modeChooser.classList.contains('hidden'),false);
      for(const mode of ['boost','teamwork','swarm']) {
        assert.equal(nodes.get('chooser-'+mode).getAttribute('href'),'/'+mode+'?project_id=project-one');
      }
    })()''', path='/', search='?project_id=project-one')


@pytest.mark.parametrize('mode', ['boost', 'teamwork', 'swarm'])
def test_each_mode_filters_active_and_project_lists(mode):
    run_dashboard(r'''(async()=>{
      const mode=window.location.pathname.slice(1), urls=[];
      const run={id:'r',project_id:'p',mode,status:'active'};
      fetch=async(url)=>{
        urls.push(url);let data;
        if(url==='/api/projects')data={projects:[{id:'p'}]};
        else if(url==='/api/runs?status=active&mode='+mode)data={runs:[run]};
        else if(url==='/api/runs?project_id=p&mode='+mode)data={runs:[run]};
        else if(url==='/api/runs/r/snapshot')data={run};
        else throw Error('Unexpected URL '+url);
        return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data};
      };
      await loadProjects();
      assert.equal(state.loadState,'ready');assert.equal(state.run.mode,mode);
      assert.equal(window.location.pathname,'/'+mode);assert.equal(streams.length,1);
      assert.equal(urls.filter(url=>url.startsWith('/api/runs?')).length,2);
    })()''', path='/'+mode)


@pytest.mark.parametrize('mode', ['boost', 'teamwork', 'swarm'])
def test_legacy_root_run_link_resolves_exact_id_to_canonical_mode(mode):
    run_dashboard(r'''(async()=>{
      const mode=new URLSearchParams(window.location.search).get('test_mode');
      const boot=init();assert.equal(requests.length,1);
      assert.equal(requests[0].url,'/api/runs/old');
      requests[0].resolve({run:{id:'old',project_id:'p',mode},project:{id:'p'}});
      await boot;
      const redirected=window.location.redirected;
      assert.equal(redirected.split('?')[0],'/'+mode);
      const q=new URLSearchParams(redirected.split('?')[1]);
      assert.equal(q.get('run_id'),'old');assert.equal(q.get('project_id'),'p');
      assert.equal(requests.length,1);assert.equal(streams.length,0);
    })()''', path='/', search='?run_id=old&project_id=p&test_mode='+mode)


def test_explicit_old_run_outside_latest_100_is_loaded_without_changing_mode():
    run_dashboard(r'''(async()=>{
      const old={id:'old',project_id:'p',mode:'boost',status:'completed'}, urls=[];
      const recent=Array.from({length:100},(_,i)=>({id:'new-'+i,project_id:'p',mode:'boost',status:'completed'}));
      const data={
        '/api/projects':{projects:[{id:'p'}]},
        '/api/runs/old':{run:old,project:{id:'p'}},
        '/api/runs?project_id=p&mode=boost':{runs:recent},
        '/api/runs/old/snapshot':{run:old,messages:[{seq:1,body_md:'old log'}]},
      };
      fetch=async(url)=>{urls.push(url);assert.ok(url in data,url);return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data[url]}};
      await loadProjects();
      assert.equal(state.loadState,'ready');assert.equal(state.run.id,'old');
      assert.equal(state.runs.length,101);assert.ok(el.runSelect.innerHTML.includes('value="old" selected'));
      assert.equal(window.location.pathname,'/boost');assert.equal(state.selectedProjectId,'p');
      assert.equal(urls.filter(url=>url.endsWith('/snapshot')).length,1);
      assert.equal(streams.length,1);assert.ok(streams[0].url.includes('run_id=old'));
    })()''', path='/boost', search='?run_id=old')


@pytest.mark.parametrize('fault', ['mode', 'project', 'id', 'metadata', 'missing'])
def test_invalid_explicit_link_never_applies_other_task_or_opens_stream(fault):
    run_dashboard(r'''(async()=>{
      const fault=new URLSearchParams(window.location.search).get('fault');
      const run={id:'r',project_id:'p',mode:'teamwork'};
      if(fault==='mode')run.mode='swarm';
      if(fault==='project')run.project_id='other';
      if(fault==='id')run.id='other';
      fetch=async(url)=>{
        if(url==='/api/projects')return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({projects:[{id:'p'}]})};
        assert.equal(url,'/api/runs/r');
        if(fault==='missing')throw Error('Run not found: r');
        return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({run,project:{id:fault==='metadata'?'wrong':run.project_id}})};
      };
      state.run={id:'previous'};state.messages=[{seq:10,body_md:'previous'}];
      await loadProjects();
      assert.equal(state.loadState,'error');assert.equal(state.run,null);
      assert.equal(state.messages.length,0);assert.equal(state.runs.length,0);
      assert.equal(streams.length,0);assert.equal(el.humanSendBtn.disabled,true);
      assert.ok(el.contextStatus.textContent.includes('加载失败'));
      assert.ok(!el.overviewContent.innerHTML.includes('previous'));
    })()''', path='/teamwork', search='?run_id=r&project_id=p&fault='+fault)


@pytest.mark.parametrize('fault', ['mode', 'project', 'id'])
def test_snapshot_scope_failure_does_not_render_body_or_connect_sse(fault):
    run_dashboard(r'''(async()=>{
      const fault=new URLSearchParams(window.location.search).get('fault');
      state.selectedProjectId='p';
      const run={id:'r',project_id:'p',mode:'boost'};
      if(fault==='mode')run.mode='swarm';
      if(fault==='project')run.project_id='other';
      if(fault==='id')run.id='other';
      const selected=selectRun('r');
      requests[0].resolve({run,messages:[{seq:99,body_md:'OTHER MODE SECRET'}]});await selected;
      assert.equal(state.loadState,'error');assert.equal(state.run,null);assert.equal(state.messages.length,0);
      assert.equal(streams.length,0);assert.equal(el.humanSendBtn.disabled,true);
      assert.ok(!el.overviewContent.innerHTML.includes('OTHER MODE SECRET'));
    })()''', path='/boost', search='?fault='+fault)


def test_late_exact_link_resolution_cannot_replace_new_project_selection():
    run_dashboard(r'''(async()=>{
      const loading=loadProjects();
      requests[0].resolve({projects:[{id:'p'},{id:'other'}]});
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(requests[1].url,'/api/runs/old');
      const selected=loadRuns('other');
      requests[2].resolve({runs:[]});await selected;
      requests[1].resolve({run:{id:'old',project_id:'p',mode:'swarm'},project:{id:'p'}});await loading;
      assert.equal(state.selectedProjectId,'other');assert.equal(state.selectedRunId,null);
      assert.equal(state.runs.length,0);assert.equal(state.run,null);assert.equal(streams.length,0);
      assert.equal(requests.length,3);assert.equal(window.location.search,'?project_id=other');
    })()''', search='?project_id=p&run_id=old')


def test_empty_project_stays_empty_in_its_mode_and_manual_refresh_discovers_new_run():
    run_dashboard(r'''(async()=>{
      const urls=[];let created=false;
      const run={id:'new',project_id:'p',mode:'boost',status:'active'};
      fetch=async(url)=>{
        urls.push(url);let data;
        if(url==='/api/projects')data={projects:[{id:'p'}]};
        else if(url==='/api/runs?project_id=p&mode=boost')data={runs:created?[run]:[]};
        else if(url==='/api/runs/new')data={run,project:{id:'p'}};
        else if(url==='/api/runs/new/snapshot')data={run};
        else throw Error('Unexpected unscoped URL '+url);
        return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data};
      };
      await loadProjects();
      assert.equal(state.loadState,'idle');assert.equal(state.runs.length,0);assert.equal(streams.length,0);
      assert.ok(el.overviewContent.innerHTML.includes('Boost'));assert.ok(el.runSelect.innerHTML.includes('暂无'));
      created=true;await loadProjects();
      assert.equal(state.loadState,'ready');assert.equal(state.selectedRunId,'new');
      assert.equal(window.location.pathname,'/boost');
      await loadProjects();
      assert.equal(state.selectedRunId,'new');assert.equal(state.runs.length,1);
      assert.ok(streams[0].closed);assert.equal(streams.length,2);
      assert.equal(nodes.get('nav-teamwork').getAttribute('href'),'/teamwork?project_id=p');
      assert.equal(nodes.get('nav-boost').getAttribute('aria-current'),'page');
      assert.equal(nodes.get('nav-teamwork').getAttribute('aria-current'),null);
    })()''', path='/boost', search='?project_id=p')


def test_no_active_run_chooses_latest_project_with_tasks_in_current_mode():
    run_dashboard(r'''(async()=>{
      const run={id:'recent',project_id:'busy',mode:'teamwork',status:'completed'};
      const data={
        '/api/projects':{projects:[{id:'empty'},{id:'busy'}]},
        '/api/runs?status=active&mode=teamwork':{runs:[]},
        '/api/runs?mode=teamwork':{runs:[run]},
        '/api/runs?project_id=busy&mode=teamwork':{runs:[run]},
        '/api/runs/recent/snapshot':{run},
      };
      fetch=async(url)=>{assert.ok(url in data,url);return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data[url]}};
      await loadProjects();
      assert.equal(state.loadState,'ready');assert.equal(state.selectedProjectId,'busy');
      assert.equal(state.selectedRunId,'recent');
    })()''', path='/teamwork')


@pytest.mark.parametrize('fault', ['mode', 'project', 'id'])
def test_nested_run_updated_cannot_switch_selected_mode_project_or_run(fault):
    run_dashboard(r'''(async()=>{
      const fault=new URLSearchParams(window.location.search).get('fault');
      state.selectedProjectId='p';
      const run={id:'r',project_id:'p',mode:'swarm',status:'active'};
      const selected=selectRun('r');requests[0].resolve({run});await selected;
      const update={...run,title:'WRONG TASK'};
      if(fault==='mode')update.mode='teamwork';
      if(fault==='project')update.project_id='other';
      if(fault==='id')update.id='other';
      streams[0].events.run_updated({data:JSON.stringify({run_id:'r',run:update})});
      assert.equal(state.loadState,'error');assert.equal(state.run,null);assert.equal(state.runs.length,0);
      assert.equal(streams[0].closed,true);assert.equal(el.humanSendBtn.disabled,true);
      assert.ok(!el.runTitleHeading.textContent.includes('WRONG TASK'));
    })()''', search='?fault='+fault)


def test_reconnect_snapshot_uses_same_scope_and_ignores_prior_stream_after_switch():
    run_dashboard(r'''(async()=>{
      state.selectedProjectId='p';
      const run={id:'r',project_id:'p',mode:'swarm',status:'active'};
      const selected=selectRun('r');requests[0].resolve({run});await selected;
      const oldStream=streams[0];oldStream.onerror();oldStream.onopen();
      assert.equal(requests[1].url,'/api/runs/r/snapshot');
      const next=loadRuns('other');requests[2].resolve({runs:[]});await next;
      requests[1].resolve({run,messages:[{seq:9,body_md:'old message'}]});
      await new Promise(resolve=>setImmediate(resolve));
      oldStream.onerror();oldStream.onopen();
      assert.equal(requests.length,3);assert.equal(state.messages.length,0);assert.equal(state.run,null);
      assert.equal(state.selectedProjectId,'other');
    })()''')


def test_wrong_mode_list_response_is_visible_failure_not_silent_filtering():
    run_dashboard(r'''(async()=>{
      const loading=loadRuns('p');
      requests[0].resolve({runs:[{id:'other',project_id:'p',mode:'teamwork',title:'WRONG TASK'}]});
      await loading;
      assert.equal(state.loadState,'error');assert.equal(state.runs.length,0);assert.equal(state.run,null);
      assert.equal(streams.length,0);assert.equal(requests.length,1);
      assert.ok(el.contextStatus.textContent.includes('Teamwork'));
      assert.ok(!el.runSelect.innerHTML.includes('WRONG TASK'));
    })()''')


@pytest.mark.parametrize('fault', ['missing', 'project', 'mode'])
def test_invalid_legacy_root_link_stays_on_chooser_with_visible_error(fault):
    run_dashboard(r'''(async()=>{
      const fault=new URLSearchParams(window.location.search).get('fault');
      const boot=init();
      if(fault==='missing')requests[0].reject(Error('Run not found'));
      else requests[0].resolve({run:{id:'r',project_id:fault==='project'?'other':'p',mode:fault==='mode'?'invalid':'swarm'},project:{id:'p'}});
      await boot;
      assert.ok(el.modeChooserStatus.textContent.includes('打开失败'));
      assert.equal(window.location.redirected,undefined);assert.equal(requests.length,1);assert.equal(streams.length,0);
      assert.equal(el.dashboardShell.classList.contains('hidden'),true);
    })()''', path='/', search='?run_id=r&project_id=p&fault='+fault)


def test_bootstrap_error_replaces_loading_selectors_with_recoverable_controls():
    run_dashboard(r'''(async()=>{
      el.projectSelect.innerHTML='<option>加载项目中...</option>';
      el.runSelect.innerHTML='<option>暂无运行任务</option>';
      fetch=async(url)=>{
        const data=url==='/api/projects'?{projects:[{id:'p',name:'Project'}]}:{run:{id:'r',project_id:'p',mode:'swarm'},project:{id:'p'}};
        return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data};
      };
      await loadProjects();
      assert.equal(state.loadState,'error');
      assert.ok(el.projectSelect.innerHTML.includes('请选择项目'));
      assert.ok(el.projectSelect.innerHTML.includes('Project'));
      assert.ok(!el.projectSelect.innerHTML.includes('加载项目中'));
      assert.equal(el.projectSelect.disabled,false);assert.equal(el.runSelect.disabled,true);
      assert.ok(el.runSelect.innerHTML.includes('未加载'));
      assert.equal(state.selectedProjectId,null);
    })()''', path='/boost', search='?run_id=r')


@pytest.mark.parametrize('older_result', ['success', 'failure'])
@pytest.mark.parametrize('newer_result', ['success', 'failure'])
def test_diff_latest_request_owns_success_and_error(older_result, newer_result):
    run_dashboard(r'''(async()=>{
      const params=new URLSearchParams(window.location.search);
      state.workers=[{id:'w',name:'w',workspace_id:'ws'}];state.selectedWorkerId='w';state.workspaces=[{id:'ws'}];
      const older=loadWorkspaceDiff('ws'), newer=loadWorkspaceDiff('ws');
      if(params.get('newer')==='failure')requests[1].reject(Error('new inspection denied'));
      else requests[1].resolve({dirty_count:2,files:[{path:'new-a.py'},{path:'new-b.py'}]});
      await newer;
      if(params.get('older')==='failure')requests[0].reject(Error('old inspection denied'));
      else requests[0].resolve({dirty_count:0,files:[]});
      await older;
      if(params.get('newer')==='failure'){
        assert.equal(state.diffByWorkspace.has('ws'),false,'stale success must not replace newer error');
        assert.equal(state.diffErrors.get('ws'),'new inspection denied');
        assert.ok(el.inspectorBody.innerHTML.includes('new inspection denied'));
      }else{
        assert.equal(state.diffByWorkspace.get('ws')?.dirty_count,2,'stale response must not replace newer diff');
        assert.equal(state.diffErrors.has('ws'),false);
        assert.ok(el.inspectorBody.innerHTML.includes('new-a.py'));
      }
      assert.ok(!el.inspectorBody.innerHTML.includes('old inspection denied'));
      assert.ok(!el.inspectorBody.innerHTML.includes('工作树干净'));
    })()''', search=f'?older={older_result}&newer={newer_result}')


@pytest.mark.parametrize('first_result', ['success', 'failure'])
def test_diff_completion_does_not_clear_other_workspace_loading(first_result):
    run_dashboard(r'''(async()=>{
      state.workers=[{id:'a',name:'a',workspace_id:'A'},{id:'b',name:'b',workspace_id:'B'}];
      state.workspaces=[{id:'A'},{id:'B'}];state.selectedWorkerId='a';
      const a=loadWorkspaceDiff('A');state.selectedWorkerId='b';const b=loadWorkspaceDiff('B');
      assert.ok(el.inspectorBody.innerHTML.includes('正在加载工作区文件状态'));
      if(window.location.search.includes('failure'))requests[0].reject(Error('A denied'));
      else requests[0].resolve({dirty_count:0,files:[]});
      await a;
      assert.ok(el.inspectorBody.innerHTML.includes('正在加载工作区文件状态'),'A completion must not clear pending B');
      assert.ok(!el.inspectorBody.innerHTML.includes('A denied'));
      requests[1].resolve({dirty_count:1,files:[{path:'B.py'}]});await b;
      assert.ok(el.inspectorBody.innerHTML.includes('B.py'));
      assert.ok(!el.inspectorBody.innerHTML.includes('正在加载工作区文件状态'));
    })()''', search='?first='+first_result)


@pytest.mark.parametrize('old_result', ['success', 'failure'])
def test_diff_stale_completion_preserves_newer_pending_inspection(old_result):
    run_dashboard(r'''(async()=>{
      state.workers=[{id:'w',name:'w',workspace_id:'ws'}];state.workspaces=[{id:'ws'}];state.selectedWorkerId='w';
      const older=loadWorkspaceDiff('ws'), newer=loadWorkspaceDiff('ws');
      if(window.location.search.includes('failure'))requests[0].reject(Error('stale failure'));
      else requests[0].resolve({dirty_count:0,files:[]});
      await older;
      assert.equal(state.diffByWorkspace.has('ws'),false,'stale completion must not populate the current inspection');
      assert.equal(state.diffErrors.has('ws'),false);
      assert.ok(el.inspectorBody.innerHTML.includes('正在加载工作区文件状态'));
      requests[1].resolve({dirty_count:1,files:[{path:'latest.py'}]});await newer;
      assert.ok(el.inspectorBody.innerHTML.includes('latest.py'));
      assert.ok(!el.inspectorBody.innerHTML.includes('正在加载工作区文件状态'));
    })()''', search='?old='+old_result)


@pytest.mark.parametrize('renderer', ['renderWorkers', 'renderInspector'])
def test_diff_render_exception_is_not_discarded_as_a_stale_request(renderer):
    run_dashboard(r'''(async()=>{
      const renderer=new URLSearchParams(window.location.search).get('renderer');
      if(renderer==='renderWorkers')renderWorkers=()=>{throw Error('worker rendering failed')};
      else renderInspector=()=>{throw Error('inspector rendering failed')};
      const loading=loadWorkspaceDiff('ws',{silent:true});
      requests[0].resolve({dirty_count:1,files:[{path:'valid.py'}]});
      await assert.rejects(loading,/rendering failed/);
      assert.equal(state.diffByWorkspace.get('ws').dirty_count,1);
      assert.equal(state.diffErrors.has('ws'),false,'a renderer failure is not an inspection failure');
    })()''', search='?renderer='+renderer)


def test_diff_loading_survives_switch_back_to_an_earlier_pending_workspace():
    run_dashboard(r'''(async()=>{
      state.workers=[{id:'a',name:'a',workspace_id:'A'},{id:'b',name:'b',workspace_id:'B'}];
      state.workspaces=[{id:'A'},{id:'B'}];state.selectedWorkerId='a';const a=loadWorkspaceDiff('A');
      state.selectedWorkerId='b';const b=loadWorkspaceDiff('B');state.selectedWorkerId='a';renderInspector();
      assert.ok(el.inspectorBody.innerHTML.includes('正在加载工作区文件状态'),'returning to A must still show its pending load');
      requests[1].resolve({dirty_count:0,files:[]});await b;
      assert.ok(el.inspectorBody.innerHTML.includes('正在加载工作区文件状态'));
      requests[0].resolve({dirty_count:1,files:[{path:'A.py'}]});await a;
      assert.ok(el.inspectorBody.innerHTML.includes('A.py'));
    })()''')


@pytest.mark.parametrize('old_result', ['success', 'failure'])
def test_diff_context_reset_rejects_old_result_and_preserves_new_loading(old_result):
    run_dashboard(r'''(async()=>{
      const old=loadWorkspaceDiff('ws');clearRunState();
      state.workers=[{id:'w',name:'w',workspace_id:'ws'}];state.workspaces=[{id:'ws'}];state.selectedWorkerId='w';
      renderInspector();assert.ok(!el.inspectorBody.innerHTML.includes('正在加载工作区文件状态'));
      const current=loadWorkspaceDiff('ws');
      if(window.location.search.includes('failure'))requests[0].reject(Error('old context failed'));
      else requests[0].resolve({dirty_count:0,files:[]});
      await old;
      assert.equal(state.diffByWorkspace.has('ws'),false);assert.equal(state.diffErrors.has('ws'),false);
      assert.ok(el.inspectorBody.innerHTML.includes('正在加载工作区文件状态'));
      requests[1].resolve({dirty_count:1,files:[{path:'current.py'}]});await current;
      assert.ok(el.inspectorBody.innerHTML.includes('current.py'));
    })()''', search='?old='+old_result)


@pytest.mark.parametrize('auto_scroll', ['true', 'false'])
def test_leaving_graph_for_board_always_stops_replay(auto_scroll):
    run_dashboard(r'''(()=>{
      state.messages=[{seq:1,sender:'coder'},{seq:2,sender:'coder'}];state.activeTab='graph';
      state.autoScroll=window.location.search.includes('true');el.boardFeed.scrollHeight=200;
      startGraphReplay();assert.equal(state.graphPlaying,true);assert.equal(intervals.size,1);
      const step=state.graphStep;switchTab('board');
      assert.equal(state.graphPlaying,false,'leaving graph must stop replay even when board auto-scrolls');
      assert.equal(state.graphPlayTimer,null);assert.equal(intervals.size,0);assert.equal(state.graphStep,step);
      assert.equal(el.graphPlayIcon.classList.contains('hidden'),false);
      assert.equal(el.graphPauseIcon.classList.contains('hidden'),true);
      assert.equal(el.boardFeed.scrollTop,state.autoScroll?200:0);
    })()''', search='?auto_scroll='+auto_scroll)


def test_graph_play_button_accessible_name_tracks_replay_state():
    run_dashboard(r'''(()=>{
      state.messages=[{seq:1,sender:'coder'},{seq:2,sender:'coder'}];
      startGraphReplay();
      assert.equal(el.graphPlayBtn.getAttribute('aria-label'),'暂停回放');
      assert.equal(el.graphPlayBtn.title,'暂停回放');
      stopGraphReplay();
      assert.equal(el.graphPlayBtn.getAttribute('aria-label'),'回放协作流');
      assert.equal(el.graphPlayBtn.title,'回放协作流');
    })()''')


def test_graph_nodes_render_as_named_focusable_toggle_buttons():
    run_dashboard(r'''(()=>{
      state.workers=[{id:'w',name:'long-member-name',role:'coder',status:'working'}];renderGraph();
      const svg=el.graphCanvasHost.innerHTML.match(/<svg\b[^>]*>/)[0];
      const renderedNode=el.graphCanvasHost.innerHTML.match(/<g class="graph-node"[^>]*>/)[0];
      assert.ok(svg.includes('role="group"'),'interactive descendants must not be hidden by image semantics');
      assert.ok(renderedNode.includes('tabindex="0"'),'graph member must be keyboard focusable');
      assert.ok(renderedNode.includes('role="button"'));
      assert.ok(renderedNode.includes('aria-label="@long-member-name 开发实现 · 执行中"'));
      assert.ok(renderedNode.includes('aria-pressed="false"'));
      state.graphSelectedNode='long-member-name';renderGraph();
      assert.ok(el.graphCanvasHost.innerHTML.match(/<g class="graph-node"[^>]*>/)[0].includes('aria-pressed="true"'));
    })()''')


def test_graph_keyboard_and_mouse_share_toggle_without_page_space_scroll():
    run_dashboard(r'''(()=>{
      state.workers=[{id:'w',name:'coder',role:'coder',status:'working'}];renderGraph();bindEvents();
      const attrs={'data-node-id':'coder','data-default-stroke':'var(--primary)'}, rectAttrs={};
      const graphNode={style:{},getAttribute:k=>attrs[k]??null,setAttribute:(k,v)=>attrs[k]=v,
        closest:selector=>selector==='.graph-node'?graphNode:null,
        querySelector:selector=>selector==='rect'?{style:{},setAttribute:(k,v)=>rectAttrs[k]=v}:null};
      const svg={querySelectorAll:selector=>selector==='.graph-node'?[graphNode]:[]};
      el.graphCanvasHost.querySelector=selector=>selector==='.graph-svg'?svg:null;
      function activate(key){
        let prevented=false,stopped=false;
        el.graphCanvasHost.dispatchEvent({type:'keydown',key,target:graphNode,
          preventDefault(){prevented=true},stopPropagation(){stopped=true}});
        return {prevented,stopped};
      }
      assert.equal(activate('ArrowRight').prevented,false);assert.equal(state.graphSelectedNode,null);
      assert.equal(activate('Enter').prevented,true,'Enter must activate the focused graph member');
      assert.equal(state.graphSelectedNode,'coder');assert.equal(attrs['aria-pressed'],'true');
      assert.equal(rectAttrs.fill,'var(--bg-subtle)');
      const space=activate(' ');assert.equal(space.prevented,true,'Space must not scroll the page');assert.equal(space.stopped,true);
      assert.equal(state.graphSelectedNode,null);assert.equal(attrs['aria-pressed'],'false');
      el.graphCanvasHost.dispatchEvent({type:'mouseover',target:graphNode});
      assert.equal(state.graphHoverNode,'coder');assert.equal(attrs['aria-pressed'],'false','hover is not selection');
      el.graphCanvasHost.dispatchEvent({type:'click',target:graphNode});
      assert.equal(state.graphSelectedNode,'coder');assert.equal(attrs['aria-pressed'],'true');
      activate('Enter');assert.equal(state.graphSelectedNode,null);assert.equal(attrs['aria-pressed'],'false');
      el.graphCanvasHost.dispatchEvent({type:'mouseleave'});assert.equal(state.graphHoverNode,null);
      el.graphCanvasHost.dispatchEvent({type:'click',target:graphNode});
      el.graphCanvasHost.dispatchEvent({type:'click',target:{closest:()=>null}});
      assert.equal(state.graphSelectedNode,null);assert.equal(attrs['aria-pressed'],'false');
    })()''')


def test_main_tabs_roving_focus_and_arrow_home_end_navigation():
    run_dashboard(r'''(()=>{
      bindEvents();switchTab('overview');
      function key(index,key){let prevented=false;el.tabButtons[index].dispatchEvent({type:'keydown',key,preventDefault(){prevented=true}});assert.equal(prevented,true)}
      assert.deepEqual(el.tabButtons.map(tab=>tab.getAttribute('tabindex')),['0','-1','-1','-1']);
      key(0,'ArrowRight');assert.equal(state.activeTab,'board');assert.equal(document.activeElement,el.tabButtons[1]);
      key(1,'End');assert.equal(state.activeTab,'graph');assert.equal(document.activeElement,el.tabButtons[3]);
      key(3,'ArrowRight');assert.equal(state.activeTab,'overview');
      key(0,'ArrowLeft');assert.equal(state.activeTab,'graph');
      key(3,'Home');assert.equal(state.activeTab,'overview');
      assert.deepEqual(el.tabButtons.map(tab=>tab.getAttribute('tabindex')),['0','-1','-1','-1']);
      document.activeElement=el.overviewContent;switchTab('members');
      assert.equal(document.activeElement,el.tabButtons[2],'a hidden overview action must not retain focus');
    })()''')


def test_channel_filters_are_toggle_buttons_and_keep_focus_when_refreshed():
    run_dashboard(r'''(()=>{
      const old=testNode(),replacement=testNode();old.setAttribute('data-channel','human');old.focus();
      el.channelTabs.contains=target=>target===old;el.channelTabs.querySelector=selector=>selector.includes('human')?replacement:null;
      state.activeChannel='human';renderChannelTabsOnly();
      const buttons=[...el.channelTabs.innerHTML.matchAll(/<button\b[^>]*>/g)].map(match=>match[0]);
      assert.ok(buttons.every(button=>!button.includes('role="tab"')),'filters do not own tabpanels');
      assert.ok(buttons.find(button=>button.includes('data-channel="human"')).includes('aria-pressed="true"'));
      assert.ok(buttons.find(button=>button.includes('data-channel="all"')).includes('aria-pressed="false"'));
      assert.equal(document.activeElement,replacement,'SSE refresh must preserve the focused filter');
    })()''')


def test_members_keep_native_button_role_and_refreshed_worker_focus():
    run_dashboard(r'''(()=>{
      state.workers=[{id:'w',name:'coder',role:'coder',status:'working'}];
      const old=testNode(),replacement=testNode();old.setAttribute('data-worker-id','w');old.closest=()=>old;old.focus();
      el.workersList.contains=target=>target===old;el.workersList.querySelector=selector=>selector.includes('w')?replacement:null;
      renderWorkers();
      assert.ok(!renderMemberRow(state.workers[0]).match(/<button\b[^>]*role="listitem"/));
      assert.ok(renderMemberRow(state.workers[0]).match(/<div[^>]*role="listitem"[^>]*>\s*<button/));
      assert.ok(el.workersList.innerHTML.includes('role="list"'));
      assert.equal(document.activeElement,replacement,'refresh must focus the replacement member button');
    })()''')


def test_inspector_repaint_restores_action_focus_and_removed_action_uses_heading():
    run_dashboard(r'''(()=>{
      state.workers=[{id:'w',name:'coder',role:'coder',status:'working'}];state.selectedWorkerId='w';
      const old=testNode(),replacement=testNode();old.setAttribute('data-action','status-stopped');old.focus();
      el.inspectorDrawer.contains=target=>target===old;el.inspectorDrawer.querySelector=selector=>selector.includes('status-stopped')?replacement:null;
      renderInspector();assert.equal(document.activeElement,replacement);
      old.focus();el.inspectorDrawer.querySelector=()=>null;renderInspector();
      assert.equal(document.activeElement,el.inspectorTitle,'a removed/disabled action has an explicit stable destination');
    })()''')


def test_inspector_refresh_focus_moves_to_heading_when_workspace_disappears():
    run_dashboard(r'''(()=>{
      state.workers=[{id:'w',name:'coder',status:'working'}];state.selectedWorkerId='w';
      el.refreshDiffBtn.setAttribute('id','refreshDiffBtn');el.refreshDiffBtn.focus();
      el.inspectorDrawer.contains=target=>target===el.refreshDiffBtn;
      el.inspectorDrawer.querySelector=selector=>selector==='#refreshDiffBtn'?el.refreshDiffBtn:null;
      el.refreshDiffBtn.closest=selector=>selector==='.hidden'&&el.refreshDiffBtn.classList.contains('hidden')?el.refreshDiffBtn:null;
      renderInspector();assert.equal(document.activeElement,el.inspectorTitle,'a now-hidden header action must not retain focus');
    })()''')


@pytest.mark.parametrize('width', [800, 1200])
def test_inspector_entry_close_escape_and_responsive_modal_focus(width):
    run_dashboard(r'''(async()=>{
      bindEvents();window.innerWidth=Number(new URLSearchParams(window.location.search).get('width'));
      const trigger=testNode(),replacement=testNode();trigger.setAttribute('data-worker-id','w');trigger.closest=selector=>selector==='[data-worker-id]'?trigger:null;
      trigger.focus();state.workers=[{id:'w',name:'coder',status:'working'}];
      el.workersList.querySelector=()=>replacement;
      await selectWorkerAndOpenInspector('w');trigger.isConnected=false;
      assert.equal(document.activeElement,el.inspectorTitle,'opening must enter inspector after member repaint');
      const modal=window.innerWidth<=1080;
      assert.equal(el.inspectorDrawer.getAttribute('role'),modal?'dialog':'complementary');
      assert.equal(el.inspectorDrawer.getAttribute('aria-modal'),modal?'true':null);
      assert.ok(background.every(node=>node.inert===modal));
      document.dispatchEvent({type:'keydown',key:'Escape',preventDefault(){}});
      assert.equal(state.inspectorOpen,false);assert.equal(document.activeElement,replacement);
      assert.equal(el.inspectorDrawer.getAttribute('role'),'complementary');
      assert.ok(background.every(node=>node.inert===false));
      el.toggleInspectorBtn.focus();setInspectorOpen(true);window.innerWidth=800;window.dispatchEvent({type:'resize'});
      assert.equal(el.inspectorDrawer.getAttribute('aria-modal'),'true');assert.ok(background.every(node=>node.inert));
      window.innerWidth=1200;window.dispatchEvent({type:'resize'});
      assert.equal(el.inspectorDrawer.getAttribute('role'),'complementary');
      assert.equal(el.inspectorDrawer.getAttribute('aria-modal'),null);assert.ok(background.every(node=>!node.inert));
      setInspectorOpen(false);assert.equal(document.activeElement,el.toggleInspectorBtn);
    })()''', search='?width='+str(width))


def test_inspector_container_can_switch_from_landmark_to_modal_dialog():
    class DrawerParser(HTMLParser):
        drawer = None

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if attributes.get('id') == 'inspectorDrawer':
                self.drawer = (tag, attributes)

    parser = DrawerParser()
    parser.feed(APP.with_name('index.html').read_text(encoding='utf-8'))
    tag, attributes = parser.drawer
    assert tag == 'div', 'The responsive dialog needs a neutral container, not an aside with a prohibited role override'
    assert attributes['role'] == 'complementary'
    assert attributes['aria-label'] == '工作区检查器与安全操作'


def test_modal_inspector_tab_wraps_and_desktop_does_not_trap():
    run_dashboard(r'''(()=>{
      bindEvents();window.innerWidth=800;el.toggleInspectorBtn.focus();setInspectorOpen(true);
      const first=testNode(),last=testNode();el.inspectorDrawer.querySelectorAll=()=>[first,last];
      function tab(shift){let prevented=false;document.dispatchEvent({type:'keydown',key:'Tab',shiftKey:shift,preventDefault(){prevented=true}});return prevented}
      last.focus();assert.equal(tab(false),true);assert.equal(document.activeElement,first);
      first.focus();assert.equal(tab(true),true);assert.equal(document.activeElement,last);
      window.innerWidth=1200;window.dispatchEvent({type:'resize'});assert.equal(tab(false),false);
    })()''')


def test_export_menu_keyboard_navigation_escape_and_tab_exit():
    run_dashboard(r'''(()=>{
      bindEvents();el.exportMenu.classList.add('hidden');el.exportMdBtn.disabled=false;el.exportJsonBtn.disabled=false;
      el.exportMenu.querySelectorAll=()=>[el.exportMdBtn,el.exportJsonBtn];el.exportMenu.contains=target=>[el.exportMdBtn,el.exportJsonBtn].includes(target);
      function key(node,key){let prevented=false;node.dispatchEvent({type:'keydown',key,preventDefault(){prevented=true}});return prevented}
      assert.equal(key(el.exportMenuBtn,'Enter'),true);assert.equal(document.activeElement,el.exportMdBtn);
      key(el.exportMenu,'ArrowDown');assert.equal(document.activeElement,el.exportJsonBtn);
      key(el.exportMenu,'ArrowDown');assert.equal(document.activeElement,el.exportMdBtn);
      key(el.exportMenu,'End');assert.equal(document.activeElement,el.exportJsonBtn);
      key(el.exportMenu,'Home');assert.equal(document.activeElement,el.exportMdBtn);
      key(el.exportMenu,'ArrowUp');assert.equal(document.activeElement,el.exportJsonBtn);
      document.dispatchEvent({type:'keydown',key:'Escape',preventDefault(){}});
      assert.equal(el.exportMenu.classList.contains('hidden'),true);assert.equal(document.activeElement,el.exportMenuBtn);
      key(el.exportMenuBtn,' ');assert.equal(document.activeElement,el.exportMdBtn);
      assert.equal(key(el.exportMenu,'Tab'),false);assert.equal(el.exportMenu.classList.contains('hidden'),true);
      assert.equal(document.activeElement,el.exportMenuBtn,'native Tab starts from trigger, not a now-hidden item');
    })()''')


def test_reduced_motion_uses_immediate_jump_without_flash():
    run_dashboard(r'''(()=>{
      window.reducedMotion=true;const row=testNode();let behavior;
      row.scrollIntoView=options=>behavior=options.behavior;el.boardFeed.querySelector=()=>row;
      jumpToSeqRow(1);assert.equal(behavior,'auto');assert.equal(row.classList.contains('msg-highlight'),false);
      assert.equal(document.activeElement,row);assert.equal(row.getAttribute('tabindex'),'-1');
      window.reducedMotion=false;jumpToSeqRow(1);assert.equal(behavior,'smooth');assert.equal(row.classList.contains('msg-highlight'),true);
    })()''')


def test_unchanged_health_and_sse_labels_do_not_rebuild_live_subtrees():
    run_dashboard(r'''(()=>{
      let healthWrites=0,sseWrites=0,healthHtml='',sseHtml='';
      Object.defineProperty(el.healthBadge,'innerHTML',{get:()=>healthHtml,set:value=>{healthWrites++;healthHtml=value}});
      Object.defineProperty(el.sseBadge,'innerHTML',{get:()=>sseHtml,set:value=>{sseWrites++;sseHtml=value}});
      state.healthOk=true;renderHealthPill();renderHealthPill();assert.equal(healthWrites,1);
      setSseBadge('connected','已连接');setSseBadge('connected','已连接');assert.equal(sseWrites,1);assert.equal(healthWrites,2);
      setSseBadge('reconnecting','重连中');assert.equal(sseWrites,2);
      state.healthOk=false;renderHealthPill();renderHealthPill();assert.equal(healthWrites,4);
    })()''')


def test_graph_deemphasis_keeps_all_labels_opaque():
    run_dashboard(r'''(()=>{
      state.workers=['a','b','isolated'].map(name=>({id:name,name,role:'coder',status:'working'}));
      state.messages=[{seq:1,sender:'a',mentions:['b']}];state.graphSelectedNode='isolated';renderGraph();
      const groups=[...el.graphCanvasHost.innerHTML.matchAll(/<g class="graph-(?:node|edge)"[^>]*>/g)].map(match=>match[0]);
      assert.ok(groups.every(group=>!group.includes('opacity:')),'text must not inherit group opacity');
      const attrs={'data-node-id':'a','data-default-stroke':'var(--primary)'},rect={style:{},setAttribute(){}};
      const graphNode={style:{},getAttribute:k=>attrs[k],setAttribute:(k,v)=>attrs[k]=v,querySelector:()=>rect};
      const edgePath={style:{},setAttribute(){}};const edge={style:{},getAttribute:k=>({'data-from':'a','data-to':'b'}[k]),querySelector:q=>q==='path'?edgePath:{setAttribute(){}}};
      el.graphCanvasHost.querySelector=()=>({querySelectorAll:q=>q==='.graph-node'?[graphNode]:[edge]});
      applyGraphHighlightInPlace();assert.ok(!graphNode.style.opacity||graphNode.style.opacity==='1');assert.ok(!edge.style.opacity||edge.style.opacity==='1');
      assert.equal(rect.style.opacity,'0.28');assert.equal(edgePath.style.opacity,'0.16');
    })()''')


@pytest.mark.parametrize('format', ['md', 'json'])
def test_actual_export_selection_returns_focus_before_request_completes(format):
    run_dashboard(r'''(async()=>{
      state.selectedRunId='r';el.exportMenu.classList.remove('hidden');
      const format=new URLSearchParams(window.location.search).get('format');
      (format==='md'?el.exportMdBtn:el.exportJsonBtn).focus();
      const exported=handleExport(format);
      assert.equal(document.activeElement,el.exportMenuBtn,'export selection must return focus before awaiting the network');
      assert.equal(el.exportMenu.classList.contains('hidden'),true);
      assert.equal(requests[0].url,'/api/runs/r/export?format='+format);
      requests[0].reject(Error('download transport stopped by test'));await exported;
      assert.equal(document.activeElement,el.exportMenuBtn);
    })()''', search='?format='+format)


@pytest.mark.parametrize('caller', ['worker', 'message'])
@pytest.mark.parametrize('outcome', ['survives', 'removed', 'outside'])
def test_sse_graph_redraw_preserves_logical_node_focus(caller, outcome):
    run_dashboard(r'''(()=>{
      const params=new URLSearchParams(window.location.search),outcome=params.get('outcome');
      state.workers=[{id:'w',name:'coder',status:'working',role:'coder'}];
      const old=testNode(),replacement=testNode(),external=testNode();old.setAttribute('data-node-id','coder');old.closest=selector=>selector==='.graph-node'?old:null;
      el.graphCanvasHost.contains=target=>target===old;
      el.graphCanvasHost.querySelector=selector=>selector.includes('coder')&&outcome!=='removed'?replacement:null;
      Object.defineProperty(el.graphCanvasHost,'innerHTML',{get:()=>'',set(){if(document.activeElement===old)document.activeElement=null}});
      (outcome==='outside'?external:old).focus();
      if(outcome==='removed'){state.workers=[];state.graphHideInactive=true}
      if(params.get('caller')==='worker')upsertWorker({id:'other',name:'other',role:'coder',status:'working'});
      else upsertBoardMessage({seq:1,sender:'other',channel:'main',body_md:'update'});
      assert.equal(document.activeElement,outcome==='outside'?external:outcome==='removed'?el.tabButtons[3]:replacement);
    })()''', search=f'?caller={caller}&outcome={outcome}')


@pytest.mark.parametrize('outcome', ['survives', 'removed', 'outside'])
def test_sse_graph_detail_redraw_preserves_action_and_target_focus(outcome):
    run_dashboard(r'''(()=>{
      const outcome=new URLSearchParams(window.location.search).get('outcome');
      state.workers=[{id:'w',name:'coder',status:'working',role:'coder'}];state.graphSelectedNode='coder';
      const old=testNode(),replacement=testNode(),external=testNode();
      old.setAttribute('data-graph-action','inspect-worker');old.setAttribute('data-worker-id','w');
      el.graphDetailBar.contains=target=>target===old;
      el.graphDetailBar.querySelector=selector=>selector.includes('inspect-worker')&&selector.includes('w')&&outcome!=='removed'?replacement:null;
      Object.defineProperty(el.graphDetailBar,'innerHTML',{get:()=>'',set(){if(document.activeElement===old)document.activeElement=null}});
      (outcome==='outside'?external:old).focus();if(outcome==='removed')state.graphSelectedNode=null;
      upsertBoardMessage({seq:1,sender:'other',channel:'main',body_md:'update'});
      assert.equal(document.activeElement,outcome==='outside'?external:outcome==='removed'?el.tabButtons[3]:replacement);
    })()''', search='?outcome='+outcome)


@pytest.mark.parametrize('attribute', ['data-worker-id', 'data-view', 'data-open-seq', 'summary'])
@pytest.mark.parametrize('outcome', ['survives', 'removed', 'outside'])
def test_sse_overview_redraw_preserves_control_identity_without_stealing_focus(attribute, outcome):
    run_dashboard(r'''(()=>{
      const params=new URLSearchParams(window.location.search),attribute=params.get('attribute'),outcome=params.get('outcome');
      state.run={id:'r',status:'active'};state.loadState='ready';
      state.workers=[{id:'w',name:'coder',status:'working',role:'coder'}];
      const old=testNode(),replacement=testNode(),external=testNode();
      if(attribute==='summary'){
        old.tagName='SUMMARY';old.closest=()=>({getAttribute:()=> 'goal'});
      }else old.setAttribute(attribute,attribute==='data-worker-id'?'w':attribute==='data-view'?'members':'1');
      el.overviewContent.contains=target=>target===old;
      let rendered='';
      el.overviewContent.querySelector=selector=>rendered.includes(attribute==='summary'?'data-overview-detail="goal"':selector.slice(1,-1))?replacement:null;
      Object.defineProperty(el.overviewContent,'innerHTML',{get:()=>rendered,set(value){rendered=value;if(document.activeElement===old)document.activeElement=null}});
      (outcome==='outside'?external:old).focus();
      if(attribute==='data-open-seq')state.messages=[{seq:1,sender:'coder',body_md:'old update'}];
      if(outcome==='removed'){
        if(attribute==='data-worker-id')state.workers=[];
        else if(attribute==='data-open-seq')state.messages=Array.from({length:7},(_,i)=>({seq:i+2,sender:'coder',body_md:'new update'}));
        else state.loadState='error';
      }
      upsertWorker({id:outcome==='removed'?'other':'w',name:outcome==='removed'?'other':'coder',status:'working'});
      assert.equal(document.activeElement,outcome==='outside'?external:outcome==='removed'?el.tabButtons[0]:replacement);
    })()''', search=f'?attribute={attribute}&outcome={outcome}')


def test_sse_overview_member_retired_into_closed_details_uses_visible_tab():
    run_dashboard(r'''(()=>{
      state.run={id:'r',status:'active'};state.loadState='ready';
      state.workers=[{id:'w',name:'coder',status:'working',role:'coder'}];
      const old=testNode(),retired=testNode();old.setAttribute('data-worker-id','w');retired.getClientRects=()=>[];
      el.overviewContent.contains=target=>target===old;el.overviewContent.querySelector=()=>retired;
      let rendered='';Object.defineProperty(el.overviewContent,'innerHTML',{get:()=>rendered,set(value){rendered=value;if(document.activeElement===old)document.activeElement=null}});
      old.focus();upsertWorker({id:'w',name:'coder',status:'stopped'});
      assert.ok(rendered.includes('retired-members'));assert.ok(!state.overviewExpanded.has('retired'));
      assert.equal(document.activeElement,el.tabButtons[0],'a target inside closed details is not a focus destination');
    })()''')


@pytest.mark.parametrize('target_type', ['member', 'summary'])
def test_overview_closed_details_semantics_override_chrome_nonzero_child_rects(target_type):
    run_dashboard(r'''(()=>{
      const isSummary=new URLSearchParams(window.location.search).get('target')==='summary';
      state.run={id:'r',status:'active'};state.loadState='ready';state.workers=[{id:'w',name:'coder',status:'working'}];
      const old=testNode(),replacement=testNode(),summary=testNode(),closed=testNode();
      closed.open=false;closed.setAttribute('data-overview-detail','retired');closed.parentElement=el.overviewContent;
      closed.querySelector=selector=>selector===':scope > summary'?(isSummary?replacement:summary):null;
      replacement.closest=selector=>selector==='details:not([open])'?closed:null;
      if(isSummary){old.tagName='SUMMARY';old.closest=()=>closed}else old.setAttribute('data-worker-id','w');
      el.overviewContent.contains=target=>target===old;el.overviewContent.querySelector=()=>replacement;
      Object.defineProperty(el.overviewContent,'innerHTML',{get:()=>'',set(){if(document.activeElement===old)document.activeElement=null}});
      // Chrome reports geometry even for a button in the non-summary content of closed details.
      assert.equal(replacement.getClientRects().length,1);
      old.focus();upsertWorker({id:'w',name:'coder',status:'stopped'});
      assert.equal(document.activeElement,isSummary?replacement:el.tabButtons[0]);
      assert.equal(closed.open,false,'restoring focus must not implicitly expand retired members');
    })()''', search='?target='+target_type)


def test_cyan_badge_text_contrast_on_real_theme_surfaces_preserves_dark_palette():
    css = APP.with_name('styles.css').read_text(encoding='utf-8')

    def rgb(value):
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))

    def luminance(color):
        channels = [channel / 255 for channel in color]
        linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
        return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    for theme in ('light', 'dark'):
        block = re.search(r'\[data-theme="' + theme + r'"\]\s*\{([^}]+)\}', css).group(1)
        tokens = dict(re.findall(r'(--[\w-]+):\s*([^;]+);', block))
        foreground = rgb(tokens['--cyan'])
        tint = tuple(float(component) for component in re.fullmatch(r'rgba\(([^)]+)\)', tokens['--cyan-tint']).group(1).split(','))
        if theme == 'dark':
            assert foreground == (103, 232, 249)
            assert tint == (34, 211, 238, 0.14)
        for surface in ('--bg-surface', '--bg-card'):
            background = tuple(round(channel * tint[3] + base * (1 - tint[3])) for channel, base in zip(tint[:3], rgb(tokens[surface])))
            high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
            ratio = (high + 0.05) / (low + 0.05)
            assert ratio >= 4.5, f'{theme} cyan badge on {surface}: {ratio:.3f}:1 is below 4.5:1'
