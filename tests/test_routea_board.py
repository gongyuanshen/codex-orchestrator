"""Status is a stored-state projection, never a filesystem acceptance check."""
from orchestrator.board import BoardService
from orchestrator.db import Database


def test_status_has_lightweight_workflow_and_inbox_summary(tmp_path):
    db = Database(tmp_path / 'state.db')
    try:
        run = db.create_run(mode='boost', title='overview', project_root=tmp_path)
        result = BoardService(db).board_status(run['id'])
        assert result['workflow']['steps'] == []
        assert result['inbox'] == {
            'counts': {'pending': 0, 'accepted': 0, 'completed': 0, 'failed': 0, 'cancelled': 0},
            'blocking_count': 0,
        }
    finally:
        db.close()
