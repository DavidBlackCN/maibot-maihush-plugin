from dataclasses import replace
from maihush.host.config import MaiHushConfig
from maihush.host.messages import evidence
from maihush.decision.schema import Verdict

class Clock:
    def __init__(self): self.t=100000.; self.m=1000.
    def wall(self): return self.t
    def mono(self): return self.m
    def advance(self,n): self.t+=n; self.m+=n

def config(**changes):
    raw={'routes':[{'enabled':True,'admin_accounts':['10001'],'owner_accounts':['10002']}], 'decision':{'enabled':True,'task_name':'synthetic-task'}}
    raw.update(changes)
    return MaiHushConfig.model_validate(raw)

def message(text='麦麦先安静一会',actor='10003',mid='msg-1',stream='synthetic-test-stream',group='987654321'):
    return {'platform':'qq','account_id':'123456789','scope':'synthetic-test-scope','session_id':stream,'message_id':mid,
            'message_info':{'user_info':{'user_id':actor,'user_nickname':'owner'},'group_info':{'group_id':group,'group_name':'synthetic'}},
            'raw_message':[{'type':'text','data':text}],'processed_plain_text':text}

def ev(cfg,**kwargs): return evidence(cfg,message(**kwargs))

def verdict(action='soft_silence',seconds=60,mid='msg-1'):
    return Verdict(schema_version=1,intent={'keep':'none','soft_silence':'pause','restrain':'reduce','recover':'resume'}[action],target='bot',decision=action,duration_seconds=seconds,reason_code='REINVITED' if action=='recover' else 'RESPECT_REQUEST',evidence_message_ids=[mid])

def apply(service,e,seconds=60,action='soft_silence'):
    s=service.state(e.stream)
    t={'version':s.version,'revision':service.revision,'at':service.now()}
    return service.decide(e,t,verdict(action,seconds,e.mid))
