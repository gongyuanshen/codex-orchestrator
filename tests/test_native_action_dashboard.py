"""Read-only M1 projections through the existing dashboard Node harness."""
from test_dashboard_behavior import run_dashboard


def test_native_transfer_summary_is_bounded_escaped_and_collapsed():
    run_dashboard(r'''(()=>{
      const native_actions={next_actions:[{source_type:'inbox',source_id:'msg-1',recipient:'<img>',action_type:'followup',source:{summary:'<script>alert(1)</script>'}}],needs_reconciliation:[{action_id:'act-1',source_type:'inbox',source_id:'msg-2',recipient:'author',status:'needs_reconciliation',revision:2}],needs_clarification:[{source_type:'human',source_id:'7',seq:7,reason:'ambiguous <target>'}],counts:{claimed:1,invoking:2,confirmed:4,failed:1,needs_reconciliation:1,cancelled:0},next_actions_count:3,needs_reconciliation_count:1,needs_clarification_count:1,limit:50,counts_complete:false,native_actions_has_more:true};
      applySnapshot({run:{id:'A',mode:'swarm',workflow_version:0},native_actions,workers:[]});
      state.loadState='ready';renderOverview();const html=el.overviewContent.innerHTML;
      assert.strictEqual(state.nativeActions,native_actions);
      for(const text of ['等待转交 ≥3','已转交 4','需核对 ≥1','待澄清 ≥1','领取中 1','调用中 2','转交失败 1','有更多记录','不代表宿主实时状态','不等于业务完成或独立验收','msg-1','act-1','ambiguous &lt;target&gt;','当前查询下界'])assert.ok(html.includes(text),text);
      assert.ok(html.includes('data-overview-detail="native-ledger"'));
      assert.ok(!/data-overview-detail="native-ledger"\s+open/.test(html));
      assert.ok(!html.includes('<img>'));assert.ok(!html.includes('<script>'));
      state.overviewExpanded.add('native-ledger');renderOverview();
      assert.ok(/data-overview-detail="native-ledger"\s+open/.test(el.overviewContent.innerHTML));
      assert.equal(requests.length,0);
      clearRunState();assert.equal(state.nativeActions,null);
      applySnapshot({run:{id:'old',mode:'swarm',workflow_version:0},workers:[]});state.loadState='ready';renderOverview();
      assert.ok(el.overviewContent.innerHTML.includes('转交投影未加载'));
      assert.ok(!el.overviewContent.innerHTML.includes('已转交 4'));
    })()''')


def test_native_transfer_event_refresh_is_run_scoped_and_read_only():
    run_dashboard(r'''(async()=>{
      state.selectedRunId='A';state.selectedProjectId='p';
      applySnapshot({run:{id:'A',project_id:'p',mode:'swarm'},workers:[]});state.loadState='ready';connectEventSource('A');
      const first=streams[0];assert.ok(first.events.native_action_updated);
      first.events.native_action_updated({data:JSON.stringify({run_id:'other'})});assert.equal(requests.length,0);
      first.events.native_action_updated({data:JSON.stringify({run_id:'A'})});assert.equal(requests.length,1);
      requests[0].resolve({run:{id:'A',project_id:'p',mode:'swarm'},native_actions:{next_actions:[],needs_reconciliation:[],needs_clarification:[],counts:{claimed:0,invoking:0,confirmed:8,failed:0,needs_reconciliation:0,cancelled:0},next_actions_count:0,needs_reconciliation_count:0,needs_clarification_count:0,limit:50,counts_complete:true,native_actions_has_more:false},workers:[]});
      await new Promise(setImmediate);assert.equal(state.nativeActions.counts.confirmed,8);
      const next=selectRun('B');first.events.native_action_updated({data:JSON.stringify({run_id:'A'})});assert.equal(requests.length,2);assert.equal(state.nativeActions,null);
      requests[1].resolve({run:{id:'B',project_id:'p',mode:'swarm'},workers:[]});await next;assert.equal(state.nativeActions,null);
    })()''')
