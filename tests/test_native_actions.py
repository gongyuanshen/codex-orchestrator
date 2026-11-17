import pytest
from orchestrator.native_actions import NativeActionStore, NativeActionConflictError
from orchestrator.db import Database
from orchestrator.config import Settings
from orchestrator.board import BoardService
from orchestrator.inbox import InboxStore

@pytest.fixture
def setup(tmp_path):
    db = Database(settings=Settings(data_dir=tmp_path/'state'))
    board = BoardService(db)
    run = db.create_run(mode='boost',title='native',project_root=tmp_path)
    with db.transaction() as conn:
        conn.execute('UPDATE runs SET workflow_version=3 WHERE id=?', (run['id'],))
    board.worker_register(run['id'],'author','coder',agent_id='/root/author')
    message = InboxStore(db,board).send(run['id'],'root','author','one','notice','hello','original') ['message']
    store = NativeActionStore(db)
    yield db,board,run,message,store
    db.close()

def claim_args(setup):
    db,board,run,message,store = setup
    item = store.project(run['id'])['next_actions'][0]
    return {k:item[k] for k in ('run_id','source_type','source_id','recipient','action_type','source_digest')}

def test_claim_begin_record_and_no_business_ack(setup):
    db,board,run,message,store = setup
    args=claim_args(setup)
    claimed=store.update(action='claim',**args)
    with pytest.raises(NativeActionConflictError): store.update(action='claim',**args)
    begun=store.update(action='begin',action_id=claimed['action_id'],expected_revision=claimed['revision'],claim_token=claimed['claim_token'])
    result=store.update(action='record',action_id=begun['action_id'],expected_revision=begun['revision'],claim_token=begun['claim_token'],outcome='confirmed',evidence_ref='native:test',evidence_digest='a'*64,result='delivered')
    assert result['status']=='confirmed'
    assert InboxStore(db,board).get(message['id'])['status']=='pending'

def test_restart_fences_token_and_unknown_never_replays(setup):
    db,board,run,message,store=setup
    args=claim_args(setup)
    claimed=store.update(action='claim',**args)
    store.recover()
    row=store.get(claimed['action_id'])
    assert row['status']=='needs_reconciliation'
    with pytest.raises(NativeActionConflictError): store.update(action='begin',action_id=row['action_id'],expected_revision=claimed['revision'],claim_token=claimed['claim_token'])
    with pytest.raises(NativeActionConflictError): store.update(action='claim',**args)

def test_source_change_rejected(setup):
    db,board,run,message,store=setup
    args=claim_args(setup)
    with db.transaction() as conn: conn.execute("UPDATE inbox_messages SET body_md='changed' WHERE id=?",(message['id'],))
    with pytest.raises(NativeActionConflictError,match='source'): store.update(action='claim',**args)

def test_begin_rechecks_stop_and_record_preserves_race(setup):
    db,board,run,message,store=setup
    claimed=store.update(action='claim',**claim_args(setup))
    worker=db.get_worker_by_name(run['id'],'author')
    db.request_worker_control(worker['id'],'stopped')
    with pytest.raises(NativeActionConflictError,match='control'):
        store.update(action='begin',action_id=claimed['action_id'],expected_revision=claimed['revision'],claim_token=claimed['claim_token'])

def test_unknown_and_definite_no_call_reconciliation(setup):
    db,board,run,message,store=setup
    args=claim_args(setup)
    claimed=store.update(action='claim',**args)
    store.recover()
    row=store.get(claimed['action_id'])
    evidence=dict(evidence_ref='fixture:observed',evidence_digest='b'*64,result='host inspected')
    unknown=store.update(action='reconcile',action_id=row['action_id'],expected_revision=row['revision'],outcome='unknown',**evidence)
    assert unknown['status']=='needs_reconciliation'
    with pytest.raises(NativeActionConflictError): store.update(action='claim',**args)
    rejected=store.update(action='reconcile',action_id=row['action_id'],expected_revision=unknown['revision'],outcome='rejected',**evidence)
    assert rejected['status']=='failed'
    assert store.update(action='claim',**args)['claim_token']!=claimed['claim_token']

def test_expired_late_receipt_persists_fence(setup):
    db,board,run,message,store=setup
    row=store.update(action='claim',**claim_args(setup))
    row=store.update(action='begin',action_id=row['action_id'],expected_revision=row['revision'],claim_token=row['claim_token'])
    with db.transaction() as conn: conn.execute('UPDATE native_actions SET lease_expires_at=0 WHERE action_id=?',(row['action_id'],))
    with pytest.raises(NativeActionConflictError):
        store.update(action='record',action_id=row['action_id'],expected_revision=row['revision'],claim_token=row['claim_token'],outcome='confirmed',evidence_ref='fixture:late',evidence_digest='c'*64,result='late')
    assert store.get(row['action_id'])['status']=='needs_reconciliation'
    assert store.get(row['action_id'])['history'][-1]['reason']=='lease_expired'

def test_control_uses_original_id_and_does_not_ack(setup):
    db,board,run,message,store=setup
    worker=db.get_worker_by_name(run['id'],'author')
    control=db.request_worker_control(worker['id'],'stopped')
    item=store.project(run['id'])['next_actions'][0]
    assert item['source_type']=='control' and item['source_id']==control['id']
    row=store.update(action='claim',**{k:item[k] for k in ('run_id','source_type','source_id','recipient','action_type','source_digest')})
    row=store.update(action='begin',action_id=row['action_id'],expected_revision=row['revision'],claim_token=row['claim_token'])
    store.update(action='record',action_id=row['action_id'],expected_revision=row['revision'],claim_token=row['claim_token'],outcome='confirmed',evidence_ref='fixture:interrupt',evidence_digest='d'*64,result='interrupted')
    assert db.list_worker_controls(run['id'])[0]['status']=='pending'

def test_human_ambiguity_read_only_and_no_starvation(setup):
    db,board,run,message,store=setup
    pointer=board.board_post(run['id'],'human',body_md='original ambiguous text',msg_type='human')
    before=db.connect().total_changes
    projection=store.project(run['id'])
    assert projection['needs_clarification'][0]['seq']==pointer['seq']
    assert projection['needs_clarification'][0]['body_md']=='original ambiguous text'
    assert db.connect().total_changes==before


def test_claim_is_atomic_between_threads(setup):
    from concurrent.futures import ThreadPoolExecutor
    db,board,run,message,store=setup
    args=claim_args(setup)
    def claim():
        try: return store.update(action='claim',**args)['action_id']
        except NativeActionConflictError: return 'conflict'
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(lambda _:claim(),range(2)))
    assert results.count('conflict')==1


def test_business_requires_admitted_step_even_old_inbox_api(setup):
    db,board,run,message,store=setup
    InboxStore(db,board).send(run['id'],'root','author','business','task','work','unadmitted')
    projected=store.project(run['id'])
    assert any('admitted workflow step' in r['reason'] for r in projected['needs_clarification'])


def test_record_after_control_race_keeps_actual_result(setup):
    db,board,run,message,store=setup
    row=store.update(action='claim',**claim_args(setup))
    row=store.update(action='begin',action_id=row['action_id'],expected_revision=row['revision'],claim_token=row['claim_token'])
    worker=db.get_worker_by_name(run['id'],'author')
    db.request_worker_control(worker['id'],'stopped')
    result=store.update(action='record',action_id=row['action_id'],expected_revision=row['revision'],claim_token=row['claim_token'],outcome='confirmed',evidence_ref='native:race',evidence_digest='e'*64,result='actual external delivery')
    assert result['status']=='needs_reconciliation'
    assert result['result']=='actual external delivery'
    assert 'control' in result['history'][-1]['source_race']

def test_ambiguous_sources_cannot_hide_later_valid_human(setup):
    db,board,run,message,store=setup
    for i in range(51): board.board_post(run['id'],'human',body_md=f'ambiguous {i}',msg_type='human')
    pointer=board.board_post(run['id'],'human',body_md='exact target',mentions=['author'],msg_type='human')
    projection=store.project(run['id'])
    assert any(r['source_id']==str(pointer['seq']) for r in projection['next_actions'])
    assert len(projection['needs_clarification'])==50
    assert projection['needs_clarification_count']==51
    assert projection['counts_complete'] is True


def test_normal_ack_before_record_is_not_race(setup):
    db,board,run,message,store=setup
    row=store.update(action='claim',**claim_args(setup))
    row=store.update(action='begin',action_id=row['action_id'],expected_revision=row['revision'],claim_token=row['claim_token'])
    InboxStore(db,board).ack(message['id'],'author','accepted','actual receive')
    result=store.update(action='record',action_id=row['action_id'],expected_revision=row['revision'],claim_token=row['claim_token'],outcome='confirmed',evidence_ref='native:received',evidence_digest='f'*64,result='received')
    assert result['status']=='confirmed'


def test_cancel_requires_definite_no_call(setup):
    db,board,run,message,store=setup
    row=store.update(action='claim',**claim_args(setup))
    store.recover(); row=store.get(row['action_id'])
    args=dict(action='reconcile',action_id=row['action_id'],expected_revision=row['revision'],evidence_ref='native:inspected',evidence_digest='1'*64,result='never invoked',cancel=True)
    with pytest.raises(ValueError): store.update(outcome='unknown',**args)
    result=store.update(outcome='not_called',**args)
    assert result['status']=='cancelled'
    assert store.project(run['id'])['next_actions']==[]

@pytest.mark.parametrize('mode',['boost','teamwork'])
def test_business_context_frozen_at_send_across_modes(tmp_path,mode):
    from orchestrator.workflow import WorkflowService
    db=Database(settings=Settings(data_dir=tmp_path/'state'))
    try:
        run=db.create_run(mode=mode,title='frozen',project_root=tmp_path)
        with db.transaction() as conn:
            conn.execute('UPDATE runs SET workflow_version=3 WHERE id=?', (run['id'],))
        board=BoardService(db)
        for name,role in [('author','coder'),('verifier','verifier')]: board.worker_register(run['id'],name,role,agent_id='/root/'+name)
        db.save_task_packet(run['id'],title='one',goal='one',file_ownership={'author':['source.txt']},acceptance_criteria=['one'])
        flow=WorkflowService(db)
        step=flow.step_save(run['id'],'one','author','verifier')
        step=flow.step_advance(step['id'],'dispatch',step['revision'])
        inbox=InboxStore(db,board)
        message=inbox.send(run['id'],'root','author','frozen','task','one','obsolete v1 body',step_id=step['id'])['message']
        assert message['step_packet_version']==1
        assert message['step_attempt']==step['attempt']
        db.save_task_packet(run['id'],title='two',goal='two',file_ownership={'author':['source.txt']},acceptance_criteria=['one'])
        current=flow.get_step(step['id'])
        flow.step_advance(current['id'],'dispatch',current['revision'])
        projected=NativeActionStore(db).project(run['id'])
        assert projected['next_actions']==[]
        assert projected['needs_clarification']
    finally: db.close()


def test_lease_is_checked_inside_cas_transaction(setup,monkeypatch):
    import time
    db,board,run,message,store=setup
    row=store.update(action='claim',**claim_args(setup))
    with db.transaction() as conn: conn.execute('UPDATE native_actions SET lease_expires_at=? WHERE action_id=?',(time.time()+0.05,row['action_id']))
    original=store.expire
    def delayed_expire(): original(); time.sleep(0.1)
    monkeypatch.setattr(store,'expire',delayed_expire)
    with pytest.raises(NativeActionConflictError,match='expired'):
        store.update(action='begin',action_id=row['action_id'],expected_revision=row['revision'],claim_token=row['claim_token'])
    assert store.get(row['action_id'])['status']=='needs_reconciliation'

@pytest.mark.parametrize('alias',['01','+1','1.0','1e0'])
def test_human_seq_alias_does_not_create_second_action(setup,alias):
    db,board,run,message,store=setup
    pointer=board.board_post(run['id'],'human',body_md='single source',mentions=['author'],msg_type='human')
    item=next(r for r in store.project(run['id'])['next_actions'] if r['source_type']=='human')
    args={k:item[k] for k in ('run_id','source_type','source_id','recipient','action_type','source_digest')}
    store.update(action='claim',**args)
    with pytest.raises(ValueError,match='canonical'):
        store.update(action='claim',**(args|{'source_id':alias.replace('1',str(pointer['seq']))}))
