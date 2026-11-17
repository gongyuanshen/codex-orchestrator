"""Workflow4 dashboard projections are bounded, escaped and strictly read-only."""
from test_dashboard_behavior import run_dashboard


def test_workflow4_keeps_receipts_and_classifies_wait_without_claiming_telemetry():
    run_dashboard(r'''(()=>{
      const workflow={steps:[],blockers:[],verifications:[],artifacts:[]};
      const progress_status={classification:'host_unknown',incomplete:true,executable_candidates:[],
        blockers:[{kind:'host_unknown',reason:'Inspect <author>',step_id:'step-1'}],coordinator_required:true};
      applySnapshot({run:{id:'A',mode:'swarm',workflow_version:4},workflow,progress_status,workers:[]});
      state.loadState='ready';renderOverview();let html=el.overviewContent.innerHTML;
      for(const text of ['结构化门禁 · v4','local_collector_v1','等待协调者处理','需要观察宿主状态','Inspect &lt;author&gt;','登记事实'])assert.ok(html.includes(text),text);
      assert.ok(!html.includes('Inspect <author>'));assert.equal(requests.length,0);
      assert.ok(!/data-overview-detail="progress-blockers"\s+open/.test(html));
      state.overviewExpanded.add('progress-blockers');renderOverview();
      assert.ok(/data-overview-detail="progress-blockers"\s+open/.test(el.overviewContent.innerHTML));
      clearRunState();assert.equal(state.progressStatus,null);
      applySnapshot({run:{id:'B',mode:'boost',workflow_version:3},workflow,workers:[]});state.loadState='ready';renderOverview();
      assert.ok(!el.overviewContent.innerHTML.includes('Inspect'));
    })()''')


def test_progress_status_labels_and_missing_projection_are_explicit():
    run_dashboard(r'''(()=>{
      const workflow={steps:[],blockers:[],verifications:[],artifacts:[]};
      const labels={ready:'有可推进事项',completed:'步骤已完成',inactive:'任务非活动',normal_wait:'正常等待',dependency_wait:'等待前序步骤',user_decision:'等待用户决策',
        needs_reconciliation:'需要核对',host_unknown:'需要观察宿主状态',no_progress:'无可执行推进'};
      for(const [classification,label] of Object.entries(labels)){
        applySnapshot({run:{id:'A',mode:'teamwork',workflow_version:4},workflow,
          progress_status:{classification,incomplete:true,executable_candidates:[],blockers:[],coordinator_required:false},workers:[]});
        state.loadState='ready';renderOverview();assert.ok(el.overviewContent.innerHTML.includes(label),label);
      }
      applySnapshot({run:{id:'A',mode:'teamwork',workflow_version:4},workflow,workers:[]});state.loadState='ready';renderOverview();
      assert.ok(el.overviewContent.innerHTML.includes('推进状态未加载'));
      assert.ok(!el.overviewContent.innerHTML.includes('代理死亡'));assert.equal(requests.length,0);
    })()''')


def test_handoff_summary_is_bound_to_digest_and_preserves_original_lifecycle_authority():
    run_dashboard(r'''(()=>{
      const workflow={steps:[],blockers:[],verifications:[],artifacts:[]};
      const resume_context={facts_digest:'facts-1',frozen_packet_id:'packet-1',milestones:[],
        artifact_refs:[{id:'artifact-1',digest:'sha1'}],verification_refs:[{id:'verify-1',artifact_id:'artifact-1',request_digest:'sha2'}],
        unresolved:{inbox:[{id:'msg-1',status:'accepted'}],controls:[],native_actions:[{action_id:'act-1',status:'invoking'}]},
        handoffs:[{step_id:'step-1',digest:'handoff-digest',recipient_agent_id:'/root/<next>',current:false,acknowledged:false}],
        lifecycle_authority:'original_root_coordinator',forbidden_actions:['Do not replace <author>']};
      const plan_readiness={ready:false,uncovered_criteria:['end-to-end <test>'],ownership_conflicts:[{step_id:'a',other_step_id:'b',paths:['src','src/file.py']}]};
      applySnapshot({run:{id:'A',mode:'teamwork',workflow_version:4},workflow,resume_context,plan_readiness,workers:[]});
      state.loadState='ready';renderOverview();let html=el.overviewContent.innerHTML;
      for(const text of ['计划与交接','未覆盖验收项 1','范围冲突 1','交接包已过期','handoff-digest','/root/&lt;next&gt;',
        '未处置业务消息 1','原协调者仍掌握旧子代理生命周期','artifact-1','verify-1','Do not replace &lt;author&gt;'])assert.ok(html.includes(text),text);
      assert.ok(!html.includes('<next>'));assert.ok(!/data-overview-detail="resume-context"\s+open/.test(html));
      assert.equal(requests.length,0);
      clearRunState();assert.equal(state.resumeContext,null);assert.equal(state.planReadiness,null);
      applySnapshot({run:{id:'B',mode:'boost',workflow_version:4},workflow,workers:[]});state.loadState='ready';renderOverview();
      assert.ok(el.overviewContent.innerHTML.includes('交接投影未加载'));assert.ok(!el.overviewContent.innerHTML.includes('handoff-digest'));
    })()''')


def test_review_dimensions_do_not_claim_project_success():
    run_dashboard(r'''(()=>{
      const workflow={steps:[{id:'s',step_key:'a',state:'done',attempt:0,checkpoint:{review_dimensions:{v1:{specification:'PASS',code:'PASS',stress:'NOT_RUN',evidence:'FAIL'}}}}],
        blockers:[],verifications:[{id:'v1',verdict:'FAIL'}],artifacts:[]};
      applySnapshot({run:{id:'A',mode:'boost',workflow_version:4},workflow,workers:[]});state.loadState='ready';renderOverview();
      const html=el.overviewContent.innerHTML;
      for(const text of ['代码正确性：PASS','证据检查：FAIL','压力与反例：NOT_RUN','项目目标达成仍需最终集成门禁'])assert.ok(html.includes(text),text);
      assert.ok(!/data-overview-detail="review-dimensions"\s+open/.test(html));assert.equal(requests.length,0);
    })()''')
