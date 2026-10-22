"""Cheap conservative screening; this is not semantic model accuracy."""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Candidate:
    kind: str
    sources: tuple
    targeted: bool
    safe: bool

PAUSE=re.compile(r'闭嘴|安静|别说|少说|歇会|休息一下|静音|静默|quiet|shut up|stop talking|be silent',re.I)
RESUME=re.compile(r'可以说|继续说|回来聊|说两句|你来回答|你怎么看|请回答|说话吧|speak again|come back|what do you think',re.I)
NEGATED=re.compile(r'不要闭嘴|不用闭嘴|别闭嘴|不用安静|不要安静|不必安静|别停止说|don.t be quiet|not.*shut up',re.I)
QUOTED=re.compile(r'[“”「」『』"`]|(?:^|\n)>|转发|引用|小说|台词|他说|她说|他让|她让')
OTHER=re.compile(r'(?:小明|小红|张三|李四|你们|他|她)[，,:： ]*(?:请|先|给我)?(?:闭嘴|安静|少说)')

def classify(e,bot_account):
    sources=tuple(s.get('type','unknown') for s in e.segments)
    text=e.text
    at=any(s.get('type')=='at' and (s.get('data')==bot_account or isinstance(s.get('data'),dict) and s['data'].get('user_id')==bot_account) for s in e.segments)
    targeted=at or bool(re.search(r'麦麦|麦|maimai|maibot|bot|你',text,re.I))
    safe=all(s in ('text','at') for s in sources) and not NEGATED.search(text) and not QUOTED.search(text) and not OTHER.search(text)
    if text.startswith('/maihush'): return None
    kind='resume' if RESUME.search(text) else 'pause' if PAUSE.search(text) else ''
    return Candidate(kind,sources,targeted,bool(safe and targeted)) if kind else None
