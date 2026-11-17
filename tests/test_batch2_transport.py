"""Second-batch capabilities must be present in both health and client gates."""
import asyncio

import httpx

from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.mcp_shim import REQUIRED_CAPABILITIES


CAPABILITIES = {'plan_admission_v1', 'compact_handoff_v1', 'recipient_groups_v1',
                'progress_classification_v1', 'workflow_reviews_v1'}


def test_batch2_health_and_shim_require_the_same_new_contracts(tmp_path):
    async def check():
        app = create_app(Settings(data_dir=tmp_path), start_watchdog=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                health = (await client.get('/api/health')).json()
                assert CAPABILITIES <= set(health['capabilities'])
                assert CAPABILITIES <= REQUIRED_CAPABILITIES
    asyncio.run(check())


def test_dashboard_snapshot_keeps_batch2_read_only_projections(tmp_path):
    async def check():
        app = create_app(Settings(data_dir=tmp_path / 'state'), start_watchdog=False)
        async with app.router.lifespan_context(app):
            run = app.state.db.create_run(mode='teamwork', title='draft', project_root=tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                response = await client.get(f"/api/runs/{run['id']}/snapshot")
                assert response.status_code == 200, response.text
                snapshot = response.json()
                assert {'resume_context', 'plan_readiness', 'progress_status'} <= snapshot.keys()
                assert snapshot['resume_context']['lifecycle_authority'] == 'original_root_coordinator'
                assert snapshot['progress_status']['classification'] in {
                    'ready', 'completed', 'inactive', 'normal_wait', 'dependency_wait', 'user_decision',
                    'needs_reconciliation', 'host_unknown', 'no_progress'}
    asyncio.run(check())
