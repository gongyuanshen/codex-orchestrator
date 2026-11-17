"""M1 public RPC/snapshot contract and strict schema validation."""
import asyncio
import httpx
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.rpc_handlers import TOOL_DEFINITIONS


def test_native_catalog_has_one_strict_definition():
    definitions=[t for t in TOOL_DEFINITIONS if t['name']=='native_action_update']
    assert len(definitions)==1
    assert definitions[0]['inputSchema']['additionalProperties'] is False
    assert 'cancel' in definitions[0]['inputSchema']['properties']


def test_rpc_projection_and_restart_receipt(tmp_path):
    async def exercise():
        app=create_app(settings=Settings(data_dir=tmp_path/'state'),manage_pid_file=False)
        async with app.router.lifespan_context(app):
            db=app.state.db; board=app.state.board
            run=db.create_run(mode='boost',title='native API',project_root=tmp_path)
            db.connect().execute('UPDATE runs SET workflow_version=3 WHERE id=?', (run['id'],))
            board.worker_register(run['id'],'author','coder',agent_id='/root/author')
            app.state.inbox.send(run['id'],'root','author','api','notice','hello','original')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response=await client.post('/api/rpc',json={'tool':'board_status','arguments':{'run_id':run['id']}})
                assert response.status_code==200
                body=response.json()
                # RPC uses the normal success envelope.
                projected=body.get('result',body)
                item=projected['next_actions'][0]
                args={k:item[k] for k in ('run_id','source_type','source_id','recipient','action_type','source_digest')}
                response=await client.post('/api/rpc',json={'tool':'native_action_update','arguments':dict(action='claim',**args)})
                assert response.status_code==200
                row=response.json().get('result',response.json())
                assert row['claim_token'] and row['status']=='claimed'
                snapshot=await client.get('/api/runs/'+run['id']+'/snapshot')
                assert snapshot.status_code==200
                assert 'native_actions' in snapshot.json()
    asyncio.run(exercise())
