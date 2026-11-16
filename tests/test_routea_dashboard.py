"""Route A dashboard behavior through the actual browser module in a small DOM shell."""
from test_dashboard_behavior import run_dashboard


def test_routea_snapshot_state_is_context_owned_and_legacy_is_explicit():
    run_dashboard(r'''(()=>{
      const workflow={steps:[],verifications:[],artifacts:[],blockers:[]};
      const inbox={counts:{pending:2,accepted:1,completed:4,failed:0,cancelled:0},blocking_count:3};
      applySnapshot({run:{id:'A',mode:'swarm',workflow_version:1},workflow,inbox,workers:[]});
      assert.strictEqual(state.workflow,workflow);assert.strictEqual(state.inbox,inbox);
      state.loadState='ready';renderOverview();
      assert.ok(el.overviewContent.innerHTML.includes('结构化门禁'));
      assert.ok(el.overviewContent.innerHTML.includes('待接收 2'));
      assert.ok(el.overviewContent.innerHTML.includes('已接收待处置 1'));
      assert.ok(el.overviewContent.innerHTML.includes('执行完成回执 4'));
      assert.ok(el.overviewContent.innerHTML.includes('回执不等于独立验收'));
      clearRunState();assert.equal(state.workflow,null);assert.equal(state.inbox,null);
      applySnapshot({run:{id:'old',workflow_version:0},workers:[]});state.loadState='ready';renderOverview();
      assert.ok(el.overviewContent.innerHTML.includes('旧流程，未启用结构化门禁'));
      assert.ok(!el.overviewContent.innerHTML.includes('待接收 2'));
    })()''')


def test_routea_workflow_stages_rework_blockers_and_overflow_stay_compact():
    run_dashboard(r'''(()=>{
      const steps=[
        {id:'decision',step_key:'<img onerror=x>',state:'needs_decision',attempt:3,checkpoint:{}},
        {id:'recover',step_key:'Recover',state:'reconciliation_required',attempt:1,checkpoint:{}},
        {id:'handoff',step_key:'Handoff',state:'handoff',attempt:0,checkpoint:{}},
        ...Array.from({length:5},(_,i)=>({id:'more'+i,step_key:'Extra'+i,state:'planned',attempt:0,checkpoint:{}}))
      ];
      applySnapshot({run:{id:'A',mode:'teamwork',workflow_version:1},workflow:{steps,artifacts:[],verifications:[],blockers:[{step_id:'decision',state:'needs_decision'},{step_id:'recover',state:'reconciliation_required'}]},workers:[]});
      state.loadState='ready';renderOverview();const html=el.overviewContent.innerHTML;
      assert.ok(html.includes('待用户决策'));assert.ok(html.includes('需要核对'));
      assert.ok(html.includes('等待交接确认'));assert.ok(html.includes('返工 3 / 3'));
      assert.ok(html.includes('workflow-more'));assert.ok(html.includes('其余 5 个步骤'));
      assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<img'));
      assert.ok(!html.includes('[object Object]'));assert.ok(html.includes('共 2 项阻塞'));
      assert.ok(!html.includes('已通过独立验收 0'));
    })()''')


def test_routea_native_observation_is_binding_specific_and_expires():
    run_dashboard(r'''(()=>{
      const worker={id:'w',name:'author',agent_id:'native-A',status:'working',last_heartbeat_at:Date.now()/1000};
      state.workers=[worker];state.workflow={steps:[],verifications:[],artifacts:[],blockers:[]};
      assert.ok(renderHostObservation(worker).includes('宿主：未观察'));
      state.workflow.steps=[{checkpoint:{native_observation:{agent_id:'native-B',state:'running',observed_at:Date.now()/1000,evidence:'actual list'}}}];
      assert.ok(renderHostObservation(worker).includes('宿主：未观察'));
      state.workflow.steps.push({checkpoint:{native_observation:{agent_id:'native-A',state:'idle',observed_at:Date.now()/1000-31,evidence:'actual list'}}});
      let html=renderHostObservation(worker);
      assert.ok(html.includes('最近观察：空闲'));assert.ok(html.includes('已过期'));
      assert.ok(html.includes('data-observed-at'));assert.ok(html.includes('<time'));
      assert.ok(!html.includes('当前在线'));
      state.workflow.steps.push({checkpoint:{native_observation:{agent_id:'native-A',state:'running',observed_at:Date.now()/1000-2,evidence:'actual list'}}});
      html=renderHostObservation(worker);assert.ok(html.includes('最近观察：执行中'));assert.ok(!html.includes('已过期'));
      state.workflow.steps.push({checkpoint:{native_observation:{agent_id:'native-A',state:'running',observed_at:Date.now()/1000+60,evidence:'future'}}});
      assert.ok(renderHostObservation(worker).includes('观察时间异常'));
    })()''')


def test_routea_registered_state_and_host_observation_are_separate():
    run_dashboard(r'''(()=>{
      state.workflow={steps:[],verifications:[],artifacts:[],blockers:[]};
      const worker={id:'w',name:'w',role:'coder',status:'working',agent_id:'a'};
      assert.ok(renderMemberRow(worker).includes('登记：执行中'));
      assert.ok(renderMemberRow(worker).includes('宿主：未观察'));
      assert.ok(renderOverviewMember(worker).includes('登记：执行中'));
      assert.ok(renderOverviewMember(worker).includes('宿主：未观察'));
    })()''')


def test_routea_events_refresh_summary_but_old_stream_cannot_restore_context():
    run_dashboard(r'''(async()=>{
      state.selectedRunId='A';state.selectedProjectId='p';
      applySnapshot({run:{id:'A',project_id:'p',mode:'swarm',workflow_version:1},workers:[]});state.loadState='ready';
      connectEventSource('A');const first=streams[0];
      assert.ok(first.events.workflow_updated);assert.ok(first.events.inbox_updated);
      first.events.workflow_updated({data:JSON.stringify({run_id:'A'})});
      assert.equal(requests.length,1);
      requests[0].resolve({run:{id:'A',project_id:'p',mode:'swarm',workflow_version:1},workflow:{steps:[{id:'review',step_key:'Need review',state:'candidate_review',attempt:0,checkpoint:{}}],verifications:[],artifacts:[],blockers:[{step_id:'review',state:'candidate_review'}]},workers:[]});
      await new Promise(setImmediate);assert.equal(state.workflow.blockers[0].state,'candidate_review');
      const next=selectRun('B');
      first.events.inbox_updated({data:JSON.stringify({run_id:'A'})});
      assert.equal(requests.length,2);assert.equal(state.workflow,null);
      requests[1].resolve({run:{id:'B',project_id:'p',mode:'swarm',workflow_version:1},workers:[]});await next;
      assert.equal(state.run.id,'B');assert.equal(state.workflow,null);
    })()''')


def test_routea_observation_staleness_updates_without_fabricated_online_state():
    run_dashboard(r'''(()=>{
      const now=Date.now();const worker={id:'w',name:'w',agent_id:'native-A',status:'working'};
      state.workers=[worker];state.workflow={steps:[{checkpoint:{native_observation:{agent_id:'native-A',state:'idle',observed_at:now/1000,evidence:'native list'}}}],verifications:[],artifacts:[],blockers:[]};
      const displayed={dataset:{hostWorker:'w'},innerHTML:''};
      document.querySelectorAll=(selector)=>selector==='.host-observation'?[displayed]:[];
      tickHostObservations();assert.ok(!displayed.innerHTML.includes('已过期'));
      Date.now=()=>now+31000;tickHostObservations();
      assert.ok(displayed.innerHTML.includes('已过期'));assert.ok(displayed.innerHTML.includes('最近观察：空闲'));
      assert.equal(worker.status,'working');assert.ok(!displayed.innerHTML.includes('当前在线'));
    })()''')


def test_routea_compact_layout_has_keyboard_and_overflow_rules():
    import pathlib
    css=(pathlib.Path(__file__).parents[1]/'plugin/src/orchestrator/web/styles.css').read_text(encoding='utf-8')
    assert '.workflow-more > .workflow-steps, .workflow-blockers > ul { max-height: 240px; overflow-y: auto; }' in css
    assert '.workflow-step-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }' in css
    assert 'summary:focus-visible' in css
    assert '@media (max-width: 768px)' in css


def test_routea_blockers_use_actual_backend_summary_contract(tmp_path):
    import json
    from orchestrator.config import Settings
    from orchestrator.db import Database
    from orchestrator.workflow import WorkflowService

    project=tmp_path/'project'
    project.mkdir()
    with Database(settings=Settings(data_dir=tmp_path/'state')) as db:
        run=db.create_run(mode='swarm',title='Backend/UI contract',project_root=project)
        db.save_task_packet(run['id'],title='Frozen',goal='Render actual blockers',
                            file_ownership={'author':['source.txt']},acceptance_criteria=['actual contract'])
        db.upsert_worker(run['id'],'author',role='coder',agent_id='/root/ui-author')
        db.upsert_worker(run['id'],'verifier',role='verifier',agent_id='/root/ui-verifier')
        service=WorkflowService(db)
        step=service.step_save(run['id'],'<script>blocked</script>','author','verifier')
        summary=service.summary(run['id'])
        assert summary['blockers']==[{'step_id':step['id'],'state':'planned'}]
    run_dashboard(r'''(()=>{
      state.run={id:'A',workflow_version:1};state.loadState='ready';
      state.workflow=''' + json.dumps(summary,ensure_ascii=False) + r''';
      renderOverview();const html=el.overviewContent.innerHTML;
      assert.ok(html.includes('&lt;script&gt;blocked&lt;/script&gt;：待实施'));
      assert.ok(!html.includes('[object Object]'));assert.ok(!html.includes('<script>'));
    })()''')
