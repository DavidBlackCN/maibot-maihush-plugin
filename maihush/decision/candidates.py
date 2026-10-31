"""Bounded rule candidates are advisory, never state-change authorization."""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Candidate:
    kind: str
    sources: tuple
    targeted: bool
    safe: bool
    signal: str = 'DIRECT_PAUSE'
    flags: tuple = ()
    target_evidence: str = 'none'

PAUSE=re.compile(r'闭嘴|安静|别说|少说|歇会|休息一下|静音|静默|别回复|不要回复|quiet|shut up|stop talking|be silent',re.I)
RESUME=re.compile(r'可以说|继续说|回来聊|(?<!少)说两句|你来回答|你怎么看|请回答|(?<!别)说话吧|speak again|come back|what do you think',re.I)
NEGATED=re.compile(r'不要闭嘴|不用闭嘴|别闭嘴|不用安静|不要安静|不必安静|别停止说|don.t be quiet|not.*shut up',re.I)
QUOTED=re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"|`[^`]*`|(?m:^>[^\n]*)')
REPORTED=re.compile(r'转发|引用|小说|台词|(?:他|她|有人)(?:说|让|告诉)|昵称|我叫|人物叫')
INDIRECT=re.compile(r'话怎么这么多|话太多|太吵|抢话|我们先自己聊一会|talk too much|too noisy|interrupt',re.I)
IMPERSONATION=re.compile(r'我是.{0,6}(?:主人|管理员|群主|owner|admin)|主人(?:命令|让)|我命令|最高权限|owner\s*=|I am (?:owner|admin)|"(?:role|verified|force_mute)"',re.I)
OTHER=re.compile(r'(?:小明|小红|张三|李四|你们|他|她)[，,:： ]*(?:请|先|给我)?(?:闭嘴|安静|少说)')

def classify(e,bot_account,context=(),names=('麦麦','maimai','maibot')):
    sources=tuple(s.get('type','unknown') for s in e.segments)
    if e.text.lstrip().lower().startswith('/maihush'): return None
    # Actual pinned Host AtComponent shape, not an @display-name guess.
    targets=[s['data'].get('target_user_id') for s in e.segments if s.get('type')=='at' and isinstance(s.get('data'),dict)]
    text=QUOTED.sub('',e.text)
    text=re.sub(r"'[^'\n]*'",'',text)
    resume=RESUME.search(text) or re.search(r'回来一起讨论|来帮我们看看',text)
    indirect=INDIRECT.search(text)
    kind='resume' if resume else 'restrain' if indirect else 'pause' if PAUSE.search(text) else ''
    if not kind:
        if PAUSE.search(e.text) or RESUME.search(e.text): return Candidate('pause',sources,False,False,flags=('quoted',))
        return None
    named=any(re.match(r'^\s*'+re.escape(name)+(r'\b' if name.isascii() else ''),text,re.I) for name in names if name)
    target='structured_at' if bot_account in targets and all(x==bot_account for x in targets) else 'explicit_name' if named else 'none'
    if target=='none' and re.search(r'你|我们先自己聊',text) and any(x.get('same_actor_direct_exchange') is True for x in context[-2:]): target='recent_direct_exchange'
    flags=[]
    if text!=e.text: flags.append('contains_quoted_fragment')
    if re.search(r'[“”「」『』"`]',text): flags.append('unbalanced_quote')
    if any(s not in ('text','at') for s in sources): flags.append('mixed_or_forwarded')
    if NEGATED.search(text) or re.search(r'(?:不要|不用|不必|别让|不要让|不想让).{0,12}(?:闭嘴|安静|静默|静音|少说)',text): flags.append('negated')
    if re.search(r'(?:不要|不用|不必).{0,8}(?:回来|回答|参与|继续说|说话吧)|别.{0,8}(?:回来|回答|参与|继续说)',text): flags.append('negated')
    if REPORTED.search(text): flags.append('reported_or_nickname')
    if OTHER.search(text) or targets and (bot_account not in targets or any(x!=bot_account for x in targets)): flags.append('other_target')
    if IMPERSONATION.search(e.text): flags.append('identity_claim')
    if re.search(r'开玩笑|just kidding|讽刺',text,re.I): flags.append('ambiguous_joke')
    if target=='none': flags.append('ambiguous_target')
    return Candidate(kind,sources,target!='none',not any(x!='contains_quoted_fragment' for x in flags),'EXPLICIT_REINVITE' if resume else 'INDIRECT_RESTRAINT' if indirect else 'DIRECT_PAUSE',tuple(flags),target)
