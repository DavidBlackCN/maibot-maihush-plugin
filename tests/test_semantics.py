from pathlib import Path
import json
import pytest
from tests.helpers import config,ev
from maihush.decision.candidates import classify

CASES=json.loads((Path(__file__).parent/'fixtures/semantic-80.json').read_text(encoding='utf8'))

@pytest.mark.parametrize('row',CASES,ids=lambda row:row['id'])
def test_source_screening_only(row):
    cfg=config(); e=ev(cfg,text=row['text'],actor='10001' if row['actor_role']=='admin' else '10003')
    candidate=classify(e,'123456789')
    if row['category'] in ('quoted','negated','other') and candidate: assert not candidate.safe
    if row['category']=='resume': assert candidate and candidate.kind=='resume'
    assert row['hard_authorized'] is False

def test_forwarded_and_lone_at():
    from dataclasses import replace
    cfg=config(); e=ev(cfg)
    e=replace(e,segments=({'type':'forward','data':'麦麦闭嘴'},))
    assert not classify(e,'123456789').safe
    e=replace(e,text='',segments=({'type':'at','data':'123456789'},))
    assert classify(e,'123456789') is None

def test_annotated_candidate_matrix():
    from tools.semantic_report import evaluate
    report=evaluate()
    assert report['passed'],[r for r in report['rows'] if not r['passed']]

def test_duplicate_direct_candidate_is_durably_rejected(tmp_path):
    from maihush.application.service import Service
    from maihush.domain.models import Denied
    cfg=config(); s=Service(tmp_path,cfg)
    try:
        e=ev(cfg); s.admit(e)
        with pytest.raises(Denied,match='DUPLICATE'): s.admit(e)
    finally: s.close()
