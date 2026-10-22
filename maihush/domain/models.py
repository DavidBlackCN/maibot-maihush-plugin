from dataclasses import dataclass
import re

class Denied(Exception):
    pass

@dataclass
class State:
    version: int = 0
    hard: str = 'NONE'
    hard_until: float = 0
    soft: str = 'NORMAL'
    soft_until: float = 0
    episode_start: float = 0
    episode_deadline: float = 0
    dwell_start: float = 0
    pause_after: float = 0
    resume_after: float = 0
    candidate_after: float = 0
    last_allowed: float = 0

    def expire(self, now, recovery):
        before=vars(self).copy()
        if self.hard == 'HARD_SILENT' and now >= self.hard_until:
            self.hard='NONE'; self.soft='RECOVERING'; self.soft_until=self.hard_until+recovery
        if self.hard == 'NONE' and self.soft in ('SOFT_SILENT','RESTRAINED') and now >= self.soft_until:
            self.soft='RECOVERING'; self.soft_until+=recovery
        if self.soft == 'RECOVERING' and now >= self.soft_until:
            self.soft='NORMAL'; self.soft_until=0; self.episode_start=0; self.episode_deadline=0
        if before != vars(self): self.version+=1
        return self

def duration(text, cap):
    if not isinstance(text,str) or not re.fullmatch(r'[1-9][0-9]{0,7}[smh]?',text): raise Denied('INVALID_DURATION')
    n=int(text[:-1] if text[-1] in 'smh' else text)
    factor={'s':1,'m':60,'h':3600}.get(text[-1],1)
    if n > cap//factor: raise Denied('INVALID_DURATION')
    return n*factor
