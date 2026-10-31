from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
import json

class Verdict(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid',frozen=True)
    schema_version: int=Field(ge=1,le=1)
    intent: Literal['none','reduce','pause','resume']
    target: Literal['bot','other','uncertain']
    decision: Literal['keep','restrain','soft_silence','recover']
    duration_seconds: int=Field(ge=0,le=2147483647)
    reason_code: Literal['NOT_TARGETED','QUOTED_OR_NEGATED','LOW_PARTICIPATION','OVERPARTICIPATION','RESPECT_REQUEST','REINVITED','UNCERTAIN']
    evidence_message_ids: list[str]=Field(max_length=3)
    reply: str = Field('', max_length=80)

    @model_validator(mode='after')
    def consistent(self):
        expected={'keep':'none','restrain':'reduce','soft_silence':'pause','recover':'resume'}
        if self.intent!=expected[self.decision]: raise ValueError('inconsistent action')
        if self.decision in ('keep','recover') and self.duration_seconds!=0: raise ValueError('zero duration required')
        if self.decision in ('restrain','soft_silence') and self.duration_seconds<=0: raise ValueError('positive duration required')
        if self.decision!='keep' and (self.target!='bot' or not self.evidence_message_ids): raise ValueError('target and evidence required')
        if self.decision=='recover' and self.reason_code!='REINVITED': raise ValueError('explicit invitation required')
        return self

def parse(text,ids):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result: raise ValueError('duplicate JSON key')
            result[k]=v
        return result
    if not isinstance(text,str) or len(text.encode())>8192: raise ValueError('oversize output')
    raw=json.loads(text,object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('nonfinite')))
    verdict=Verdict.model_validate(raw)
    if not set(verdict.evidence_message_ids)<=set(ids): raise ValueError('unknown evidence')
    return verdict
