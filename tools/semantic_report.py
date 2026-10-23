"""Annotated rule-candidate confusion matrix, NOT real LLM accuracy."""
from pathlib import Path
from collections import Counter
from dataclasses import replace
import argparse
import json
from tests.helpers import config, ev
from maihush.decision.candidates import classify
from maihush.domain.models import Denied

ROOT=Path(__file__).resolve().parents[1]

def evaluate():
    rows=[]; matrix=Counter(); families=Counter()
    for name in ('semantic-80.json','semantic-r02.json'):
        cases=json.loads((ROOT/'tests/fixtures'/name).read_text(encoding='utf8'))
        for r in cases:
            text=r['text']+'x'*r.get('padding_bytes',0)
            try:
                e=ev(config(),text=text,actor='10001' if r.get('actor_role')=='admin' else '10003')
                segments=list(e.segments)
                if r.get('at'): segments.insert(0,{'type':'at','data':{'target_user_id':r['at'],'display_name':'owner'}})
                if r.get('source'): segments.append({'type':r['source'],'data':{}})
                c=classify(replace(e,segments=tuple(segments)),'123456789',[{'same_actor_direct_exchange':True}] if r.get('context') else [])
                actual=bool(c and c.safe); signal=c.signal if c else 'NONE'
            except Denied as exc: actual=False; signal='NONE'
            expected=r['eligible']; matrix[f'expected_{expected}_actual_{actual}']+=1
            families[r['signal']]+=1
            rows.append({'id':r['id'],'expected_eligible':expected,'actual_eligible':actual,'expected_signal':r['signal'],'actual_signal':signal,
                         'passed':actual==expected and (not expected or signal==r['signal'])})
    return {'measurement':'rule candidate screening only; no LLM evaluation','cases':len(rows),'matrix':dict(matrix),'annotated_families':dict(families),
            'hard_authorization_evaluation':'not a candidate-rule metric; see command and state negative tests','passed':all(x['passed'] for x in rows),'rows':rows}

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    if args.output.resolve().is_relative_to(ROOT): ap.error('evidence must be outside repository')
    result=evaluate(); args.output.write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2)); raise SystemExit(0 if result['passed'] else 1)
