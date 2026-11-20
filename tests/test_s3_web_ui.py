from test_dashboard_behavior import run_dashboard


def test_t6_distinct_project_activity_and_persistent_write_warning():
    run_dashboard(r'''(async()=>{
      const p={id:'p',name:'Project',active_run_count:2,active_session_count:1};
      const run={id:'a',project_id:'p',session_id:'one',mode:'swarm',status:'active'};
      state.projects=[p];state.selectedProjectId='p';
      const loading=refreshGlobalSessions();
      requests[0].resolve({sessions:[{runs:[run]}],global_active_runs:[run,{...run,id:'b'}],
        concurrent_risks:[{canonical_project_id:'p',concurrent_local_write_risk:true}]});
      await loading;
      renderProjectPopover();
      assert.ok(el.projectPopoverList.innerHTML.includes('1 个会话'));
      assert.ok(el.projectPopoverList.innerHTML.includes('2 个活跃'));
      const warning=document.getElementById('projectWriteRisk');
      assert.ok(warning.textContent.includes('并发写冲突'));
      switchTab('board');
      assert.equal(warning.classList.contains('hidden'),false);
    })()''')

def test_t6_context_change_keeps_cached_active_runs_outside_page():
    run_dashboard(r'''(async()=>{
      const active={id:'older',project_id:'p',mode:'swarm',status:'active',title:'Older active'};
      const changed={...active,id:'changed'};
      state.globalSessions=[{runs:[active,changed]}];
      state.runStatusFilter='active';
      const loading=loadRuns('p');
      requests[0].resolve({runs:[{...changed,status:'archived'}]});
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(requests[1].url,'/api/runs/older/snapshot');
      requests[1].resolve({run:active});
      await loading;
      assert.ok(el.runSidebarList.innerHTML.includes('Older active'));
      assert.ok(!el.runSidebarList.innerHTML.includes('data-select-run="changed"'));
    })()''')


def test_t6_single_stream_updates_two_scopes_and_ignores_previous_run():
    run_dashboard(r'''(()=>{
      state.selectedRunId='a';state.loadState='ready';
      connectEventSource('a');
      connectGlobalEvents();
      assert.equal(streams.filter(stream=>!stream.closed).length,1);
      const global=streams.find(stream=>!stream.closed);
      state.selectedRunId='b';
      connectEventSource('b');
      assert.equal(streams.find(stream=>!stream.closed),global);
      global.events.workflow_updated({data:JSON.stringify({run_id:'a'})});
      assert.equal(requests.length,0);
      global.events.workflow_updated({data:JSON.stringify({run_id:'b'})});
      assert.equal(requests[0].url,'/api/runs/b/snapshot');
    })()''')


def test_t6_live_updates_preserve_selector_focus():
    run_dashboard(r'''(()=>{
      state.runs=[{id:'a',project_id:'p',session_id:'s',mode:'swarm',status:'active'}];
      const focused=testNode(), replacement=testNode();
      focused.setAttribute('data-select-run','a');
      focused.closest=()=>focused;
      el.runSidebarList.contains=(target)=>target===focused;
      el.runSidebarList.querySelector=(selector)=>selector.includes('a')?replacement:null;
      focused.focus();
      renderRunSidebar();
      assert.equal(document.activeElement,replacement);
    })()''')


def test_t6_delayed_catalog_keeps_newer_run_list_projection():
    run_dashboard(r'''(async()=>{
      const old={id:'r',project_id:'p',mode:'swarm',status:'active',title:'Run'};
      state.projects=[{id:'p'}];
      const catalog=refreshGlobalSessions();
      const loading=loadRuns('p');
      const completed={...old,status:'archived'};
      requests[1].resolve({runs:[completed]});
      await new Promise(resolve=>setImmediate(resolve));
      requests[2].resolve({run:completed});
      await loading;
      requests[0].resolve({sessions:[{runs:[old]}],global_active_runs:[old]});
      await catalog;
      assert.equal(state.runs[0].status,'archived');
      assert.equal(state.run.status,'archived');
    })()''')


def test_t6_palette_preserves_keyboard_item_when_results_reorder():
    run_dashboard(r'''(()=>{
      const run=(id)=>({id,project_id:'p',mode:'swarm',status:'active',title:id});
      state.globalActiveRuns=['a','b'].map(run);
      bindEvents();
      openCommandPalette(true);
      el.cmdPaletteInput.dispatchEvent({type:'keydown',key:'ArrowDown',preventDefault(){}});
      state.globalActiveRuns=['b','a'].map(run);
      renderCommandPalette();
      assert.equal(getCommandPaletteEntries()[state.cmdPaletteIndex].id,'b');
    })()''')

def test_t6_project_picker_separates_workspaces_and_collapsible_fixtures():
    run_dashboard(r'''(()=>{
      state.projects=[
        {id:'p',name:'old name',display_name:'Canonical Repo',root_path:'G:/real',project_kind:'workspace',is_git:true,active_run_count:2},
        {id:'f',name:'Fixture',root_path:'G:/fixture',project_kind:'fixture'},
        {id:'t',name:'Temp',root_path:'G:/temp',project_kind:'temp'},
        {id:'child',name:'Merged child',root_path:'G:/real/child',project_kind:'workspace',canonical_project_id:'p'},
      ];
      state.selectedProjectId='p';
      renderProjectSelector();
      const html=el.projectPopoverList.innerHTML;
      assert.equal(el.projectSwitcherName.textContent,'Canonical Repo');
      assert.ok(html.indexOf('data-select-project="__all__"') < html.indexOf('Canonical Repo'));
      assert.ok(html.includes('全部项目（跨项目并发）'));
      assert.ok(html.includes('G:/real') && html.includes('[Git]') && html.includes('2 个活跃'));
      const details=html.match(/<details([^>]*)>([\s\S]*?)<\/details>/);
      assert.ok(details && !/\bopen\b/.test(details[1]));
      assert.ok(details[2].includes('临时与验收夹具（3）'));
      for (const name of ['Fixture','Temp','Merged child']) assert.ok(details[2].includes(name));
      assert.ok(!details[2].includes('Canonical Repo'));
    })()''')


def test_t6_session_sidebar_orders_turns_groups_unbound_and_toggles():
    run_dashboard(r'''(()=>{
      state.projects=[{id:'p',project_kind:'workspace'}];state.selectedProjectId='p';
      const run=(ids, n, status='completed', prompt='')=>({id:ids,session_id:'s',project_id:'p',
        codex_thread_name:'Project thread',session_seq:n,mode:'swarm',status,title:'Turn '+n,trigger_prompt:prompt,created_at:1});
      state.runs=[{...run('old',8),session_id:'old-s',codex_thread_name:'Old thread',updated_at:20},
        run('one',1),run('three',3,'active','<img src="x"> full prompt'),run('two',2),
        {id:'no-bind',project_id:'p',mode:'swarm',title:'Unbound task',status:'active',session_id:null},
      ];
      renderRunSidebar();bindEvents();
      const html=el.runSidebarList.innerHTML;
      assert.ok(html.includes('Project thread') && html.includes('3 次') && html.includes('session-pulse'));
      assert.ok(html.indexOf('Project thread') < html.indexOf('Old thread'));
      assert.ok(html.includes('未绑定会话任务') && html.includes('Unbound task'));
      assert.ok(html.indexOf('Turn 3') < html.indexOf('Turn 2') && html.indexOf('Turn 2') < html.indexOf('Turn 1'));
      assert.ok(html.includes('#3') && html.includes('Swarm') && html.includes('进行中') && html.includes('已完成'));
      assert.ok(html.includes('title="&lt;img src=&quot;x&quot;&gt; full prompt"') && !html.includes('<img'));
      assert.ok(html.includes('触发：&lt;img'));
      const active=html.match(/<details[^>]*\bopen\b[^>]*>[\s\S]*?<summary[^>]*>[\s\S]*?Project thread/);
      assert.ok(active);
      const activeDetails=testNode(), summary=testNode();
      activeDetails.dataset.sessionGroup='s';activeDetails.open=true;
      summary.closest=(selector)=>selector==='summary'?summary:selector==='[data-session-group]'?activeDetails:null;
      el.runSidebarList.dispatchEvent({type:'click',target:summary});
      activeDetails.open=false;
      renderRunSidebar();
      assert.ok(!el.runSidebarList.innerHTML.match(/<details[^>]*data-session-group="s"[^>]*\bopen\b/));
      state.selectedRunId='old';renderRunSidebar();
      assert.ok(el.runSidebarList.innerHTML.match(/<details[^>]*data-session-group="old-s"[^>]*\bopen\b/));
    })()''')


def test_t6_all_projects_and_all_modes_accept_merged_cross_mode_snapshots():
    run_dashboard(r'''(async()=>{
      state.projects=[{id:'parent',display_name:'Parent repo',project_kind:'workspace'},
        {id:'child',canonical_project_id:'parent',project_kind:'fixture'}];
      state.selectedProjectId='parent';
      const cross={id:'r',project_id:'child',canonical_project_id:'parent',mode:'boost',status:'active',
        session_id:'s',session_seq:3,codex_thread_name:'Codex thread',title:'Source title'};
      bindEvents();
      assert.equal(el.runModeCurrentBtn.textContent,'仅 Swarm');
      assert.equal(el.runModeCurrentBtn.getAttribute('aria-pressed'),'true');
      assert.equal(el.runModeCurrentBtn.classList.contains('active'),true);
      assert.equal(el.runModeAllBtn.getAttribute('aria-pressed'),'false');
      assert.equal(el.runModeAllBtn.classList.contains('active'),false);
      el.runModeAllBtn.dispatchEvent({type:'click'});
      assert.equal(requests[0].url,'/api/runs?project_id=parent&mode=all');
      requests[0].resolve({runs:[cross]});
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(requests[1].url,'/api/runs/r/snapshot');
      requests[1].resolve({run:cross});
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(state.loadState,'ready');
      assert.equal(el.dashboardShell.getAttribute('data-mode'),'boost');
      assert.equal(el.topbarContextProject.textContent,'Parent repo');
      assert.equal(el.topbarContextRun.textContent,'Codex thread · #3');
      assert.equal(el.runTitleHeading.textContent,'Source title');
      assert.equal(el.runModeCurrentBtn.getAttribute('aria-pressed'),'false');
      assert.equal(el.runModeCurrentBtn.classList.contains('active'),false);
      assert.equal(el.runModeAllBtn.getAttribute('aria-pressed'),'true');
      assert.equal(el.runModeAllBtn.classList.contains('active'),true);
      const load=loadRuns('__all__');
      assert.equal(requests[2].url,'/api/runs?mode=all');
      requests[2].resolve({runs:[]});await load;
      assert.equal(state.selectedProjectId,'__all__');
      assert.equal(el.projectSwitcherName.textContent,'全部项目（跨项目并发）');
    })()''')
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "plugin/src/orchestrator/web/styles.css").read_text(encoding="utf-8")
    base_idx = css.index(".run-filter-pills {")
    mode_idx = css.index(".run-filter-pills.run-mode-pills {")
    assert mode_idx > base_idx
    assert "repeat(2, minmax(0, 1fr))" in css[mode_idx : mode_idx + 120]
    assert "white-space: nowrap;" in css


def test_t6_global_palette_badge_searches_all_three_sections_and_navigates():
    run_dashboard(r'''(async()=>{
      const active={id:'other',project_id:'q',canonical_project_id:'q',mode:'teamwork',status:'active',
        session_id:'s2',session_seq:2,codex_thread_name:'Thread Zebra',trigger_prompt:'unique sprint',title:'Foreign run'};
      const history={...active,id:'history',status:'completed',title:'Archived run',trigger_prompt:'archived prompt'};
      state.projects=[{id:'p',project_kind:'workspace',name:'Selected Repo'},
        {id:'q',project_kind:'workspace',name:'Raw name',display_name:'Other Repo',root_path:'G:/other'},
        {id:'f',project_kind:'fixture',name:'Internal Fixture'}];state.selectedProjectId='p';
      bindEvents();
      const loading=refreshGlobalSessions();
      assert.equal(requests[0].url,'/api/sessions');
      requests[0].resolve({sessions:[{project_id:'q',session_id:'s2',runs:[active,history],active_count:1}],
        global_active_runs:[active,{...active,id:'also'},{...active,id:'unbound',session_id:null}],concurrent_risks:[]});
      await loading;
      assert.ok(el.globalActiveBadge.textContent.includes('1 个会话运行中'));
      openCommandPalette();
      const html=el.cmdPaletteResults.innerHTML;
      assert.ok(html.indexOf('正在运行') < html.indexOf('Codex 会话与轮次'));
      assert.ok(html.indexOf('Codex 会话与轮次') < html.indexOf('切换真实项目'));
      assert.ok(!html.includes('Internal Fixture'));
      for(const q of ['Thread Zebra','s2','unique sprint','Foreign run','Other Repo','G:/other']) {
        state.cmdPaletteQuery=q;
        assert.ok(getCommandPaletteEntries().some(item=>item.kind==='run' && item.id==='other'),q);
      }
      el.globalActiveBadge.dispatchEvent({type:'click'});
      assert.equal(document.activeElement,el.cmdPaletteInput);
      assert.equal(getCommandPaletteEntries()[0].id,'other');
      assert.ok(!el.cmdPaletteResults.innerHTML.includes('Archived run'));
      const selecting=executeCommandEntry('run','other');
      assert.equal(requests[1].url,'/api/runs?project_id=q&mode=all');
      requests[1].resolve({runs:[active]});
      await new Promise(resolve=>setImmediate(resolve));
      requests[2].resolve({run:active});await selecting;
      assert.equal(state.selectedProjectId,'q');
      assert.equal(state.selectedRunId,'other');
    })()''')


def test_t6_snapshot_session_details_risk_and_escaped_turn_separators():
    run_dashboard(r'''(()=>{
      state.projects=[{id:'p',root_path:'G:/real',project_kind:'workspace'}];state.selectedProjectId='p';
      state.loadState='ready';
      const run={id:'r',project_id:'p',mode:'swarm',status:'active',session_id:'s<img>',
        codex_thread_name:'Thread <script>',session_seq:4,trigger_prompt:'Raw <b> prompt',
        execution_root:'G:/real/<slice>',concurrent_local_write_risk:true,concurrent_project_active_count:2};
      applySnapshot({run,messages:[
        {seq:1,summary:'Before',created_at:10},
        {seq:2,summary:'After',created_at:30},
      ],session_turns:[
        {session_id:'s<img>',turn_id:'turn<id>',prompt_preview:'<img onerror=bad>',created_at:20,updated_at:40},
        {session_id:'s<img>',turn_id:'later',prompt_preview:'Latest prompt',created_at:40},
      ]});
      renderAll();
      const overview=el.overviewContent.innerHTML, feed=el.boardFeed.innerHTML;
      for(const value of ['并发写冲突','Thread &lt;script&gt;','s&lt;img&gt;','#4','Raw &lt;b&gt; prompt','G:/real/&lt;slice&gt;'])
        assert.ok(overview.includes(value),value);
      assert.ok(!overview.includes('<script>') && !overview.includes('<img>'));
      assert.ok(feed.indexOf('Before') < feed.indexOf('对话轮次') && feed.indexOf('对话轮次') < feed.indexOf('After'));
      assert.ok(feed.includes('&lt;img onerror=bad&gt;') && feed.includes('turn&lt;id&gt;') && !feed.includes('<img'));
      assert.ok(feed.indexOf('After') < feed.indexOf('Latest prompt'));
      applySnapshot({run:{...run,execution_root:'G:/real',concurrent_local_write_risk:false},messages:[]});
      renderAll();
      assert.ok(!el.overviewContent.innerHTML.includes('并发写冲突') && !el.overviewContent.innerHTML.includes('执行目录'));
      assert.ok(!el.boardFeed.innerHTML.includes('对话轮次'));
    })()''')


def test_t6_background_sessions_do_not_interrupt_core_fetch_and_react_to_events():
    run_dashboard(r'''(async()=>{
      const project={id:'p',project_kind:'workspace',name:'Repo'};
      const run={id:'r',project_id:'p',mode:'swarm',status:'active',title:'Current'};
      const loading=loadProjects();
      assert.equal(requests[0].url,'/api/projects');requests[0].resolve({projects:[project]});
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(requests[1].url,'/api/runs?status=active&mode=swarm');requests[1].resolve({runs:[run]});
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(requests[2].url,'/api/runs?project_id=p&mode=swarm');requests[2].resolve({runs:[run]});
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(requests[3].url,'/api/runs/r/snapshot');requests[3].resolve({run});
      await loading;
      assert.equal(state.loadState,'ready');assert.equal(requests[4].url,'/api/sessions');
      requests[4].resolve({sessions:[{runs:[run]}],global_active_runs:[run]});
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(state.loadState,'ready');assert.equal(el.runTitleHeading.textContent,'Current');
      const global=streams.find(s=>s.url==='/api/events');
      assert.ok(global && global.events.run_updated && global.events.session_updated);
      assert.equal(streams.filter(s=>!s.closed).length,1);
      global.events.run_updated({data:'{}'});global.events.session_updated({data:'{}'});
      await new Promise(resolve=>setImmediate(resolve));
      const sessionRequests=requests.slice(5).filter(req=>req.url==='/api/sessions');
      assert.ok(sessionRequests.length>=1);
      assert.equal(sessionRequests.length,1);
      const newRun={...run,id:'new',session_id:'new-session',codex_thread_name:'New session'};
      sessionRequests[0].resolve({
        sessions:[{runs:[newRun]},{runs:[run]}],global_active_runs:[newRun,run],concurrent_risks:[]
      });
      await new Promise(resolve=>setImmediate(resolve));
      assert.ok(el.runSidebarList.innerHTML.includes('New session'));
      assert.equal(state.selectedRunId,'r');
      assert.equal(el.globalActiveBadge.textContent,'1 个会话运行中');
    })()''')
