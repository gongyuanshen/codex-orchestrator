import pathlib
import subprocess

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
 const classes=new Set(), attributes={};
 return {innerHTML:'',textContent:'',value:'',style:{},dataset:{},disabled:false,scrollTop:0,scrollHeight:0,clientHeight:100,
 classList:{add:(x)=>classes.add(x),remove:(x)=>classes.delete(x),contains:(x)=>classes.has(x),toggle:(x,v)=>v?classes.add(x):classes.delete(x)},
 setAttribute(k,v){attributes[k]=v},removeAttribute(k){delete attributes[k]},getAttribute(k){return attributes[k]??null},addEventListener(){},querySelectorAll(){return []},querySelector(q){return q==='.toast-close'?node():null},
 appendChild(){},getBoundingClientRect(){return {width:1000,height:600}},focus(){},contains(){return false}};
}
const nodes=new Map(), requests=[], streams=[];
const links=['boost','teamwork','swarm'].flatMap(mode=>['chooser','nav'].map(kind=>{const link=node();link.dataset.modeLink=mode;nodes.set(kind+'-'+mode,link);return link}));
const document={getElementById:(id)=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},querySelectorAll:(selector)=>selector==='[data-mode-link]'?links:[],createElement:node,addEventListener(){},documentElement:node()};
class EventSource {static CONNECTING=0;constructor(url){this.url=url;this.events={};streams.push(this)}addEventListener(n,f){this.events[n]=f}close(){this.closed=true}}
const ctx={document,URLSearchParams,EventSource,console,Map,Set,Date,encodeURIComponent,setImmediate,requestAnimationFrame:(f)=>f(),setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},CSS:{escape:String},
 window:{location:{search:process.argv[3]||'',pathname:process.argv[2]||'/swarm',replace(url){this.redirected=url}},history:{replaceState(_a,_b,url){ctx.window.location.pathname=url.split('?')[0];ctx.window.location.search=url.includes('?')?'?'+url.split('?')[1]:''}},addEventListener(){},innerWidth:1200},
 fetch:(url)=>new Promise((resolve,reject)=>requests.push({url,resolve:(data)=>resolve({ok:true,headers:{get:()=> 'application/json'},json:async()=>data}),reject}))};
vm.createContext(ctx);
vm.runInContext(source.replace('  init();','  globalThis.runTest = (code) => eval(code);'),ctx);
ctx.assert=assert;ctx.requests=requests;ctx.streams=streams;ctx.nodes=nodes;
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
