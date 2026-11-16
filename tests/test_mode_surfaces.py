"""Mode2 capabilities and compact UI use the existing transport/presentation."""
import asyncio

import httpx

from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.mcp_shim import REQUIRED_CAPABILITIES
from test_dashboard_behavior import run_dashboard


def test_health_and_client_require_mode_workflow_capability(tmp_path):
    async def check():
        app = create_app(Settings(data_dir=tmp_path), start_watchdog=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                health = (await client.get("/api/health")).json()
                assert "workflow_modes_v2" in health["capabilities"]
                assert "workflow_modes_v2" in REQUIRED_CAPABILITIES

    asyncio.run(check())


def test_mode2_dashboard_uses_existing_compact_structured_overview():
    run_dashboard(r'''(()=>{
      applySnapshot({run:{id:'modes',mode:'swarm',workflow_version:2},
        workflow:{steps:[],verifications:[],artifacts:[],blockers:[]},workers:[]});
      state.loadState='ready';renderOverview();
      assert.ok(el.overviewContent.innerHTML.includes('结构化门禁'));
      assert.ok(!el.overviewContent.innerHTML.includes('旧流程'));
    })()''')
