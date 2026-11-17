"""Workflow3 local collection metadata is not host authentication or a PASS."""
from test_dashboard_behavior import run_dashboard


def test_workflow3_shows_structured_gate_and_bounded_local_evidence_details():
    run_dashboard(r'''(()=>{
      const artifacts=Array.from({length:5},(_,i)=>({id:'artifact-'+i,scope:'candidate',evidence_level:'local_collector_v1',verification_command_count:1}));
      const workflow={steps:[],blockers:[],verifications:[{artifact_id:'artifact-0',verdict:'PASS',evidence_manifest:[{path:'SECRET-RAW-LOG',sha256:'abc'}]}],artifacts};
      applySnapshot({run:{id:'A',mode:'swarm',workflow_version:3},workflow,workers:[]});state.loadState='ready';renderOverview();
      const html=el.overviewContent.innerHTML;
      for(const text of ['结构化门禁 · v3','local_collector_v1','本地采集','不是宿主身份认证','已冻结 5 份采集清单','artifact-0','登记证据文件 1','另有 2 份清单','门禁使用时重新核对'])assert.ok(html.includes(text),text);
      assert.ok(html.includes('data-overview-detail="local-receipts"'));
      assert.ok(!/data-overview-detail="local-receipts"\s+open/.test(html));
      assert.ok(!html.includes('SECRET-ARGV'));assert.ok(!html.includes('SECRET-RAW-LOG'));assert.ok(!html.includes('artifact-3'));
      state.overviewExpanded.add('local-receipts');renderOverview();assert.ok(/data-overview-detail="local-receipts"\s+open/.test(el.overviewContent.innerHTML));
      assert.equal(requests.length,0);
    })()''')


def test_workflow3_missing_manifest_is_explicit_and_legacy_panel_unchanged():
    run_dashboard(r'''(()=>{
      const workflow={steps:[],blockers:[],verifications:[],artifacts:[]};
      for(const version of [1,2,3]){
        applySnapshot({run:{id:'A',mode:'swarm',workflow_version:version},workflow,workers:[]});state.loadState='ready';renderOverview();
        assert.ok(el.overviewContent.innerHTML.includes('结构化门禁 · v'+version));
        assert.equal(el.overviewContent.innerHTML.includes('尚未登记本地采集清单'),version===3);
      }
    })()''')
