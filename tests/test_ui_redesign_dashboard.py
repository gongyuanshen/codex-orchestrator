"""Verify the redesigned project/run selectors, command palette, and KPI strip."""
from test_dashboard_behavior import run_dashboard


def test_project_popover_run_sidebar_and_command_palette_drive_selection():
    run_dashboard(r'''(async()=>{
      const projA={id:'pa',name:'Alpha Repo',root_path:'G:/alpha',is_git:true};
      const projB={id:'pb',name:'Beta Docs',root_path:'G:/beta',is_git:false};
      const run1={id:'r1',project_id:'pa',mode:'swarm',status:'active',title:'Build Auth Flow',updated_at:Date.now()/1000};
      const run2={id:'r2',project_id:'pa',mode:'swarm',status:'completed',title:'Fix Login Bug',updated_at:Date.now()/1000-3600};
      state.projects=[projA,projB];state.selectedProjectId='pa';
      state.runs=[run1,run2];state.selectedRunId='r1';
      renderProjectSelector();
      renderRunSelector();

      assert.equal(el.projectSwitcherName.textContent,'Alpha Repo');
      assert.equal(el.topbarContextProject.textContent,'Alpha Repo');
      assert.equal(el.topbarContextRun.textContent,'Build Auth Flow');
      assert.ok(el.projectPopoverList.innerHTML.includes('Alpha Repo'));
      assert.ok(el.projectPopoverList.innerHTML.includes('Beta Docs'));
      assert.ok(el.runSidebarList.innerHTML.includes('进行中'));
      assert.ok(el.runSidebarList.innerHTML.includes('历史任务'));

      state.projectSearchQuery='beta';
      renderProjectPopover();
      assert.ok(el.projectPopoverList.innerHTML.includes('Beta Docs'));
      assert.ok(!el.projectPopoverList.innerHTML.includes('Alpha Repo'));

      state.runStatusFilter='active';
      renderRunSidebar();
      assert.ok(el.runSidebarList.innerHTML.includes('Build Auth Flow'));
      assert.ok(!el.runSidebarList.innerHTML.includes('Fix Login Bug'));

      state.runStatusFilter='all';
      state.runSearchQuery='login';
      renderRunSidebar();
      assert.ok(!el.runSidebarList.innerHTML.includes('Build Auth Flow'));
      assert.ok(el.runSidebarList.innerHTML.includes('Fix Login Bug'));

      openCommandPalette();
      assert.equal(state.cmdPaletteOpen,true);
      assert.equal(el.cmdPaletteBackdrop.classList.contains('hidden'),false);
      state.cmdPaletteQuery='beta';
      renderCommandPalette();
      const entries=getCommandPaletteEntries();
      assert.equal(entries.length,1);
      assert.equal(entries[0].kind,'project');
      assert.equal(entries[0].id,'pb');

      const loadPromise=executeCommandEntry('project','pb');
      assert.equal(state.cmdPaletteOpen,false);
      assert.equal(el.cmdPaletteBackdrop.classList.contains('hidden'),true);
      requests[0].resolve({runs:[]});
      await loadPromise;
      assert.equal(state.selectedProjectId,'pb');
    })()''')


def test_overview_kpi_strip_highlights_blockers_and_sets_mode_accent():
    run_dashboard(r'''(()=>{
      applySnapshot({
        run:{id:'r-kpi',project_id:'p1',mode:'boost',status:'active',workflow_version:5,title:'KPI Test'},
        workflow:{steps:[{id:'s1',step_key:'step-1',state:'done',attempt:0},{id:'s2',step_key:'step-2',state:'needs_decision',attempt:1}],verifications:[{id:'v1'}],artifacts:[],blockers:[{step_id:'s2',state:'needs_decision'}]},
        inbox:{counts:{pending:1,accepted:0,completed:1,failed:0,cancelled:0},blocking_count:1},
        workers:[{id:'w1',name:'coder-1',role:'coder',status:'working'},{id:'w2',name:'ver-1',role:'verifier',status:'waiting'}],
        messages:[{seq:1,sender:'coder-1',body_md:'hi'}]
      });
      state.loadState='ready';
      updateTopRunControls();
      renderOverview();
      const html=el.overviewContent.innerHTML;
      assert.ok(html.includes('overview-kpi-strip'));
      assert.ok(html.includes('步骤 1 / 2 完成'));
      assert.ok(html.includes('1 执行 · 1 待命'));
      assert.ok(html.includes('1 条动态'));
      assert.ok(html.includes('kpi-card is-alert'));
      assert.equal(el.dashboardShell.getAttribute('data-mode'),'boost');
    })()''')


def test_codex_cli_source_url_defaults_and_palette_selection():
    run_dashboard(r'''(async()=>{
      const snapshot={id:'job',session_id:'cli-bound',project_id:'db-root',mode:'swarm',
        title:'Batch Run',updated_at:1,status:'completed'};
      const run={...snapshot,canonical_project_id:'projection'};
      const desktopRun={...snapshot,id:'sprint',session_id:'visual',project_id:'desktop-root',status:'active'};
      const projects=[
        {id:'desktop-root',name:'Graphical Project',project_origin:'desktop',
          threads:[{session_id:'visual',title:'Visual Session',client_kind:'desktop',runs:[desktopRun]}]},
        {id:'projection',name:'Terminal Project',project_origin:'cli',threads:[
          {session_id:'cli-bound',title:'Batch Session',client_kind:'cli',codex_source:'exec',runs:[run]},
          {session_id:'interactive',title:'Terminal Discussion',client_kind:'cli',codex_source:'cli',runs:[]},
        ]},
      ];
      const loading=loadProjects();
      requests[0].resolve({codex_catalog:{available:true,projects}});
      await new Promise(resolve=>setImmediate(resolve));
      assert.equal(requests[1].url,'/api/runs/job/snapshot');
      requests[1].resolve({run:snapshot});
      await loading;
      assert.equal(state.run.id,'job');
      assert.equal(state.loadState,'ready');
      assert.ok(!el.runSidebarList.innerHTML.includes('Visual Session'));
      assert.ok(el.runSidebarList.innerHTML.includes('Terminal Discussion'));
      assert.ok(!el.projectPopoverList.innerHTML.includes('Graphical Project'));
      const href=nodes.get('nav-teamwork').getAttribute('href');
      assert.equal(new URLSearchParams(href.split('?')[1]).get('source'),'cli');

      const pickingProject=executeCommandEntry('project','projection');
      requests.at(-1).resolve({run:snapshot});
      await pickingProject;
      assert.equal(state.selectedProjectId,'projection');
      assert.equal(state.run.id,'job');
      await executeCommandEntry('thread','interactive');
      assert.equal(state.selectedSessionId,'interactive');
      assert.equal(state.selectedRunId,null);
      assert.ok(el.runSidebarList.innerHTML.includes('data-select-thread="interactive"'));
      const selecting=executeCommandEntry('run','job');
      requests.at(-1).resolve({run:snapshot});
      await selecting;
      assert.equal(state.run.id,'job');
      assert.equal(state.loadError,'');
      assert.equal(state.loadState,'ready');
      const params=new URLSearchParams(window.location.search);
      assert.equal(params.get('run_id'),'job');
      assert.equal(params.get('project_id'),'projection');
      assert.equal(params.get('source'),'cli');
      assert.equal(params.get('mode'),'all');
    })()''', search='?source=cli&mode=all')


def test_codex_source_toggle_declares_accessible_default():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "plugin/src/orchestrator/web/index.html").read_text(encoding="utf-8")
    toggle = html.split('id="runSourceToggle"', 1)[1].split("</div>", 1)[0]
    assert 'role="group" aria-label="来源端筛选"' in toggle
    assert 'data-run-source="all" aria-pressed="true">全部来源' in toggle
    assert 'data-run-source="desktop" aria-pressed="false">桌面端' in toggle
    assert 'data-run-source="cli" aria-pressed="false">CLI' in toggle
