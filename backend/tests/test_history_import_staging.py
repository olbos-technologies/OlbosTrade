from datetime import datetime, timezone
import pytest
from app.services.history_import_staging import HistoryImportStaging

START=datetime(2026,1,1,tzinfo=timezone.utc)
END=datetime(2026,1,3,tzinfo=timezone.utc)

@pytest.fixture
def staging(tmp_path):
    store=HistoryImportStaging(tmp_path/'history.sqlite')
    store.begin('fixture',account='DU_FIXTURE',organization='fixture-org',start=START,end=END,pages=2)
    yield store
    store.close()

def record(**changes):
    data=dict(execution_id='fixture-exec',account='DU_FIXTURE',organization='fixture-org',filled_at='2026-01-02T00:00:00+00:00',quantity=1)
    data.update(changes)
    return data

def test_restart_resume_duplicates_and_no_proof(staging,tmp_path):
    staging.add_page('fixture',1,[record()]); staging.add_page('fixture',1,[record()])
    with pytest.raises(ValueError,match='history_pages_missing'): staging.finish('fixture')
    resumed=HistoryImportStaging(tmp_path/'history.sqlite')
    try:
        resumed.add_page('fixture',2,[record()])
        assert resumed.finish('fixture') == dict(state='staged_unverified',verified=False,pages=2)
        assert resumed.db.execute('SELECT COUNT(*) FROM executions').fetchone()[0] == 1
    finally: resumed.close()

@pytest.mark.parametrize('field,value',[('account','OTHER'),('organization','OTHER'),('execution_id',''),('filled_at','2026-01-03T00:00:00+00:00'),('filled_at','2026-01-02T00:00:00'),('filled_at','bad')])
def test_bad_scope_or_period_quarantines(staging,field,value):
    with pytest.raises(ValueError,match='history_page_rejected'): staging.add_page('fixture',1,[record(**{field:value})])
    assert staging.db.execute('SELECT COUNT(*) FROM pages').fetchone()[0] == 0
    with pytest.raises(ValueError,match='import_quarantined'): staging.finish('fixture')

def test_conflict_rolls_back_whole_page(staging):
    staging.add_page('fixture',1,[record()])
    with pytest.raises(ValueError,match='history_page_rejected'):
        staging.add_page('fixture',2,[record(execution_id='another'),record(quantity=2)])
    assert staging.db.execute('SELECT COUNT(*) FROM executions').fetchone()[0] == 1
    assert staging.db.execute('SELECT COUNT(*) FROM pages').fetchone()[0] == 1

def test_changed_page_quarantines(staging):
    staging.add_page('fixture',1,[])
    with pytest.raises(ValueError): staging.add_page('fixture',1,[record()])

@pytest.mark.parametrize('number',[0,3,True,'1'])
def test_invalid_page_number(staging,number):
    with pytest.raises(ValueError): staging.add_page('fixture',number,[])

def test_conflicting_manifest_and_unknown_import(staging):
    with pytest.raises(ValueError,match='conflicting_import_manifest'):
        staging.begin('fixture',account='OTHER',organization='fixture-org',start=START,end=END,pages=2)
    with pytest.raises(ValueError,match='unknown_import'): staging.finish('missing')
    with pytest.raises(ValueError,match='unknown_import'): staging.add_page('missing',1,[])

def test_finalized_import_rejects_more_pages(staging):
    staging.add_page('fixture',1,[]); staging.add_page('fixture',2,[]); staging.finish('fixture')
    with pytest.raises(ValueError,match='import_not_collecting'): staging.add_page('fixture',1,[])

@pytest.mark.parametrize('pages',[0,True,'2'])
def test_invalid_manifest(staging,pages):
    with pytest.raises(ValueError,match='invalid_import_manifest'):
        staging.begin('bad',account='A',organization='O',start=START,end=END,pages=pages)
