"""Single owned storage executor serializes all commits and send permits.

No transaction awaits IPC or a model. Snapshots publish only after commit.
"""
from dataclasses import asdict, replace
from hashlib import sha256
import json
import sqlite3
import time
import uuid
from ..domain.models import Denied, State
from ..domain.budget import reserve_end, subtract
from ..storage.database import Database
from ..decision.candidates import classify


def actor_ref(actor): return sha256(actor.encode()).hexdigest()[:24]

class Service:
    def __init__(self, directory, config, wall=time.time, mono=time.monotonic):
        self.config=config.model_copy(deep=True); self.db=Database(directory)
        self.wall=wall; self.mono=mono; self.health='READY'; self.reason='READY'
        self.snapshots={}
        try:
            # Restore only saved verified bindings still explicitly selected.
            # Missing Host discovery must not erase an existing mute deadline.
            if config.configured_groups is not None:
                from ..host.config import Route
                saved=self.db.get('identity',[config.instance_id,[]])[1]
                restored=[]
                for binding in saved:
                    if any(r.stream_id==binding[5] for r in config.routes): continue
                    if any(g['group_id']==binding[4] and g['route_scope']==binding[3]
                           and (not g['bot_account_id'] or g['bot_account_id']==binding[2])
                           for g in config.configured_groups):
                        restored.append(Route(enabled=True,platform=binding[0],adapter_namespace=binding[1],
                            bot_account_id=binding[2],route_scope=binding[3],group_id=binding[4],stream_id=binding[5]))
                config=config.model_copy(update={'routes':[*config.routes,*restored]})
                self.config=config.model_copy(deep=True)
            self.snapshots={k:State(**json.loads(v)) for k,v in self.db.conn.execute('SELECT stream,payload FROM stream_state')}
            self.logical=max(wall(),self.db.get('last_wall',0)); self.last_mono=mono(); self.last_wall=wall()
            if wall()<self.logical-5: self.health='DEGRADED'; self.reason='CLOCK_ROLLBACK'
            identity=[config.instance_id,[list(r.identity()) for r in config.routes]]
            previous=self.db.get('identity',identity)
            if previous[0]!=identity[0]: raise Denied('OFFLINE_ROUTE_MIGRATION_REQUIRED')
            for new_route in identity[1]:
                for old_route in previous[1]:
                    # A newly configured group can be added after a restart;
                    # an existing session or group binding cannot be reassigned.
                    same_stream=new_route[5]==old_route[5]
                    same_group=(new_route[0],new_route[2],new_route[3],new_route[4])==(old_route[0],old_route[2],old_route[3],old_route[4])
                    if (same_stream or same_group) and new_route!=old_route:
                        raise Denied('OFFLINE_ROUTE_MIGRATION_REQUIRED')
            identity[1]=previous[1]+[x for x in identity[1] if x not in previous[1]]
            for route in config.routes:
                if config.route(route.stream_id): self.snapshots.setdefault(route.stream_id,State())
            self.revision=self.db.get('config_revision',0)+1
            if any(s.hard_until-self.logical>config.hard.max_seconds for s in self.snapshots.values() if s.hard=='HARD_SILENT'):
                raise Denied('HARD_CAP_REQUIRES_LOCAL_REVIEW')
            with self.db.transaction():
                self.db.set('identity',identity); self.db.set('config_revision',self.revision); self.db.set('last_wall',self.logical)
                # Applying explicit route/soft disable on startup is an operator action.
                for stream,state in list(self.snapshots.items()):
                    s=replace(state).expire(self.logical,config.recovery.duration_seconds)
                    if not config.route(stream):
                        self._trim(stream,self.logical); s=State(version=s.version+1)
                    elif not config.soft.enabled and s.soft!='NORMAL':
                        self._trim(stream,self.logical); s.soft='NORMAL'; s.soft_until=0; s.version+=1
                    self._save(stream,s); self.snapshots[stream]=s
        except BaseException:
            self.db.close(); raise

    def now(self):
        w=self.wall(); m=self.mono()
        if w < self.last_wall-5 or w < self.logical-5:
            self.health='DEGRADED'; self.reason='CLOCK_ROLLBACK'
        self.logical=max(w,self.logical+max(0,m-self.last_mono))
        self.last_mono=m; self.last_wall=w
        return self.logical

    def _save(self,stream,state):
        self.db.conn.execute('INSERT OR REPLACE INTO stream_state VALUES(?,?)',(stream,json.dumps(asdict(state))))

    def _trim(self,stream,now,kind=None,limit=None):
        boundary=now if limit is None else limit
        sql='UPDATE intervals SET end=MAX(start,MIN(end,?)) WHERE stream=? AND end>?'
        args=[boundary,stream,boundary]
        if kind: sql+=' AND kind=?'; args.append(kind)
        self.db.conn.execute(sql,args)

    def _evidence(self,e,now):
        row=self.db.conn.execute('SELECT fingerprint,conflict FROM message_evidence WHERE stream=? AND mid=?',(e.stream,e.mid)).fetchone()
        if row and (row[0]!=e.fingerprint or row[1]):
            self.conflict_mid=e.mid
            raise Denied('IDEMPOTENCY_CONFLICT')
        self.db.conn.execute('INSERT OR IGNORE INTO message_evidence(stream,mid,fingerprint,at) VALUES(?,?,?,?)',(e.stream,e.mid,e.fingerprint,now))

    def _run(self,stream,fn,write=True):
        now=self.now()
        if self.health!='READY':
            if write: raise Denied(self.reason)
            return replace(self.snapshots.get(stream,State())).expire(now,self.config.recovery.duration_seconds),now
        state=replace(self.snapshots.get(stream,State())).expire(now,self.config.recovery.duration_seconds)
        try:
            with self.db.transaction():
                result=fn(state,now)
                self._save(stream,state); self.db.set('last_wall',now)
            self.snapshots[stream]=state
            return result
        except Denied as exc:
            if str(exc)=='IDEMPOTENCY_CONFLICT':
                # A conflict is a durable denial, not a rollback-to-cache fallback.
                with self.db.transaction():
                    self.db.conn.execute('UPDATE message_evidence SET conflict=1 WHERE stream=? AND mid=?',(stream,self.conflict_mid))
            raise
        except (sqlite3.Error,OSError):
            self.health='DEGRADED'; self.reason='STORAGE_UNAVAILABLE'
            raise Denied(self.reason)

    def state(self,stream):
        if self.health!='READY': return replace(self.snapshots.get(stream,State())).expire(self.now(),self.config.recovery.duration_seconds)
        return self._run(stream,lambda s,n:replace(s))

    def public_summary(self, stream):
        """Read a copy only: no guard, transaction, clock update or reservation."""
        route = self.config.route(stream)
        if route is None or stream not in self.snapshots:
            raise Denied('ROUTE_UNAVAILABLE')
        wall = self.wall()
        now = max(wall, self.logical + max(0, self.mono()-self.last_mono))
        health = self.health
        if wall < max(self.last_wall, self.logical)-5:
            health = 'DEGRADED'
        state = replace(self.snapshots[stream]).expire(now, self.config.recovery.duration_seconds)
        deadlines = [now+5]
        if state.hard == 'HARD_SILENT': deadlines.append(state.hard_until)
        if state.soft != 'NORMAL': deadlines.append(state.soft_until)
        return {'stream_id':stream, 'route_fingerprint':sha256(json.dumps(route.identity(),separators=(',',':')).encode()).hexdigest(),
                'state_version':state.version, 'config_revision':self.revision,
                'hard':state.hard, 'soft':state.soft, 'health':health,
                'remaining_seconds':max(0,int(max(state.hard_until if state.hard=='HARD_SILENT' else 0,state.soft_until)-now)),
                'reason': '管理员静音' if state.hard=='HARD_SILENT' else {'NORMAL':'正常聊天','SOFT_SILENT':'自然劝静','RESTRAINED':'减少发言','RECOVERING':'恢复中'}.get(state.soft,'状态未知'),
                'as_of':now, 'valid_until':min(t for t in deadlines if t>now),
                'send_permit':False, 'guard_scope':'send_service_while_registered'}

    def _request(self,e,op,state,now,fn):
        self._evidence(e,now)
        row=self.db.conn.execute('SELECT fingerprint,result FROM requests WHERE stream=? AND mid=? AND operation=?',(e.stream,e.mid,op)).fetchone()
        if row:
            if row[0]!=e.fingerprint: raise Denied('IDEMPOTENCY_CONFLICT')
            return {**json.loads(row[1]), 'replayed': True}
        result=fn()
        self.db.conn.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)',(e.stream,e.mid,op,e.fingerprint,json.dumps(result),now))
        self.db.conn.execute('INSERT INTO audit_events(at,stream_hash,version,code) VALUES(?,?,?,?)',(now,sha256(e.stream.encode()).hexdigest()[:16],state.version,result.get('code','OK')))
        return result

    def command(self,e,op,seconds=None,authorized=False):
        def change(s,n):
            route=self.config.route(e.stream)
            if not route or (not authorized and route.role(e.actor)=='member'): raise Denied('PERMISSION_DENIED')
            if op not in ('mute','renew','unmute'): raise Denied('INVALID_COMMAND')
            if op!='unmute' and (type(seconds) is not int or not 1<=seconds<=self.config.hard.max_seconds): raise Denied('INVALID_DURATION')
            def apply():
                was_normal = s.hard=='NONE' and s.soft=='NORMAL'
                if op=='unmute':
                    self._trim(e.stream,n); s.hard='NONE'; s.hard_until=0; s.soft='NORMAL'; s.soft_until=0
                    s.pause_after=n+self.config.recovery.invitation_cooldown_seconds
                else:
                    if op=='renew' and s.hard!='HARD_SILENT': raise Denied('NO_HARD_STATE')
                    deadline=n+seconds
                    if op=='mute' and s.hard=='HARD_SILENT' and deadline<s.hard_until: raise Denied('WOULD_SHORTEN')
                    self._trim(e.stream,n); s.hard='HARD_SILENT'; s.hard_until=max(s.hard_until,deadline)
                    s.soft='NORMAL'; s.soft_until=0
                s.episode_start=s.episode_deadline=0; s.version+=1
                s.soft_origin={}
                return {'ok':True,'code':'COMMITTED','version':s.version,'ack':op=='mute' and was_normal}
            return self._request(e,op,s,n,apply)
        return self._run(e.stream,change)

    def admit(self,e,resume=False,context=(),participation=None):
        def change(s,n):
            route=self.config.route(e.stream)
            if not route or not self.config.soft.enabled or not self.config.decision.enabled or s.hard!='NONE': raise Denied('DECISION_DISABLED')
            candidate=classify(e,route.bot_account_id,context,self.config.bot_names)
            if not candidate or not candidate.safe or resume!=(candidate.kind=='resume'): raise Denied('UNSAFE_CANDIDATE')
            def apply():
                if resume:
                    if n<s.resume_after or s.soft not in ('SOFT_SILENT','RESTRAINED') or n-s.dwell_start<self.config.soft.min_dwell_seconds: raise Denied('RECOVERY_COOLDOWN')
                    s.resume_after=n+self.config.recovery.invitation_cooldown_seconds
                else:
                    row=self.db.conn.execute('SELECT until FROM actor_cooldown WHERE stream=? AND actor=?',(e.stream,e.actor)).fetchone()
                    if n<max(s.pause_after,s.candidate_after,row[0] if row else 0): raise Denied('CANDIDATE_COOLDOWN')
                    s.candidate_after=n+self.config.soft.group_candidate_cooldown_seconds
                    self.db.conn.execute('INSERT OR REPLACE INTO actor_cooldown VALUES(?,?,?)',(e.stream,e.actor,n+self.config.soft.requester_cooldown_seconds))
                return {'ok':True,'code':'ADMITTED','version':s.version,'revision':self.revision,'at':n,'call_id':uuid.uuid4().hex,
                        'fingerprint':e.fingerprint,'role':route.role(e.actor),'context':list(context),
                        'participation':participation or {},'signal':candidate.signal}
            existing=self.db.conn.execute("SELECT 1 FROM requests WHERE stream=? AND mid=? AND operation='candidate'",(e.stream,e.mid)).fetchone()
            self._evidence(e,n)
            if existing: raise Denied('DUPLICATE_CANDIDATE')
            return self._request(e,'candidate',s,n,apply)
        return self._run(e.stream,change)

    def start_call(self,e,ticket):
        def change(s,n):
            self._ticket(e,ticket,s,n)
            c=self.config.decision
            rows=self.db.conn.execute('SELECT stream FROM model_calls WHERE at>?',(n-3600,)).fetchall()
            if len(rows)>=c.max_calls_per_instance_hour or sum(x[0]==e.stream for x in rows)>=c.max_calls_per_group_hour: raise Denied('MODEL_BUDGET')
            self.db.conn.execute("INSERT INTO model_calls VALUES(?,?,?)",(ticket['call_id'],e.stream,n))
        return self._run(e.stream,change)

    def _ticket(self,e,t,s,n):
        if not self.config.route(e.stream) or not self.config.soft.enabled or not self.config.decision.enabled or s.hard!='NONE' or t['revision']!=self.revision or t['version']!=s.version or n-t['at']>self.config.decision.candidate_ttl_seconds:
            raise Denied('STALE_STATE')
        self._evidence(e,n)
        if t.get('fingerprint',e.fingerprint)!=e.fingerprint or t.get('role',self.config.route(e.stream).role(e.actor))!=self.config.route(e.stream).role(e.actor): raise Denied('STALE_EVIDENCE')
        candidate=classify(e,self.config.route(e.stream).bot_account_id,t.get('context',()),self.config.bot_names)
        if not candidate or not candidate.safe: raise Denied('UNSAFE_CANDIDATE')
        return candidate

    def decide(self,e,ticket,decision):
        def change(s,n):
            candidate=self._ticket(e,ticket,s,n)
            if decision.decision!='keep' and e.mid not in decision.evidence_message_ids: raise Denied('CURRENT_EVIDENCE_REQUIRED')
            def apply():
                action=decision.decision; soft=self.config.soft
                was_normal=s.soft=='NORMAL'
                if action=='keep': return {'ok':True,'code':'KEEP'}
                if (action=='recover')!=(candidate.kind=='resume'): raise Denied('INTENT_MISMATCH')
                if action=='recover':
                    if s.soft not in ('SOFT_SILENT','RESTRAINED') or n-s.dwell_start<soft.min_dwell_seconds: raise Denied('RECOVERY_COOLDOWN')
                    self._trim(e.stream,n); s.soft='RECOVERING'; s.soft_until=n+self.config.recovery.duration_seconds
                    s.pause_after=n+self.config.recovery.invitation_cooldown_seconds
                else:
                    if n<s.pause_after: raise Denied('PAUSE_COOLDOWN')
                    # Restrained cannot shorten or escape a current silence episode.
                    if action=='restrain' and s.soft=='SOFT_SILENT': raise Denied('NO_SOFT_DOWNGRADE')
                    # Weak feedback can reduce speech but never require silence.
                    if candidate.signal=='INDIRECT_RESTRAINT':
                        action='restrain'
                    if action=='restrain' and s.soft=='SOFT_SILENT': raise Denied('NO_SOFT_DOWNGRADE')
                    kind='silence' if action=='soft_silence' else 'restraint'
                    cap=soft.max_seconds if kind=='silence' else self.config.restraint.max_seconds
                    wanted=n+min(decision.duration_seconds,cap)
                    if kind=='silence':
                        start=s.episode_start or n; deadline=s.episode_deadline or n+soft.episode_max_seconds
                        wanted=min(wanted,deadline)
                    else: start=deadline=0
                    rows=self.db.conn.execute('SELECT start,end,actor FROM intervals WHERE stream=? AND kind=? AND end>?',(e.stream,kind,n-3600)).fetchall()
                    existing=[(a,b) for a,b,_ in rows]; owned=[(a,b) for a,b,actor in rows if actor==e.actor]
                    group_cap=soft.group_hour_budget_seconds if kind=='silence' else self.config.restraint.hour_budget_seconds
                    actor_cap=soft.requester_hour_budget_seconds if kind=='silence' else group_cap
                    end=reserve_end(n,wanted,existing,owned,group_cap,actor_cap)
                    if end-n<10: raise Denied('SOFT_BUDGET')
                    s.soft_origin={'source':'social','start':n,'until':end}
                    if s.soft in ('SOFT_SILENT','RESTRAINED') and end<s.soft_until:
                        s.version+=1
                        return {'ok':True,'code':'NO_EXTENSION','version':s.version}
                    for a,b in subtract((n,end),existing):
                        self.db.conn.execute('INSERT INTO intervals VALUES(?,?,?,?,?,?)',(e.stream,kind,a,b,e.actor,e.mid))
                    if kind=='silence':
                        self._trim(e.stream,n,'restraint'); s.episode_start=start; s.episode_deadline=deadline
                    if s.soft!=('SOFT_SILENT' if kind=='silence' else 'RESTRAINED'): s.dwell_start=n
                    s.soft='SOFT_SILENT' if kind=='silence' else 'RESTRAINED'; s.soft_until=end
                s.version+=1
                return {'ok':True,'code':'SOFT_COMMITTED','version':s.version,'ack':was_normal and action in ('restrain','soft_silence')}
            return self._request(e,'decision',s,n,apply)
        return self._run(e.stream,change)

    def auto_restrain(self,stream,revision):
        """Private internal event, never a forged message/user or public API."""
        def change(s,n):
            cfg=self.config; auto=cfg.autonomous
            if revision!=self.revision or not cfg.route(stream) or not auto.enabled or not cfg.soft.enabled: raise Denied('AUTO_DISABLED')
            if s.hard!='NONE' or s.soft!='NORMAL' or n<max(s.auto_after,s.pause_after): return False
            rows=self.db.conn.execute("SELECT start,end,actor FROM intervals WHERE stream=? AND kind='restraint' AND end>?",(stream,n-3600)).fetchall()
            existing=[(a,b) for a,b,_ in rows]; owned=[(a,b) for a,b,actor in rows if actor=='internal:auto']
            end=reserve_end(n,n+auto.duration_seconds,existing,owned,cfg.restraint.hour_budget_seconds,auto.hour_budget_seconds)
            if end-n<10: return False
            for a,b in subtract((n,end),existing):
                self.db.conn.execute('INSERT INTO intervals VALUES(?,?,?,?,?,?)',(stream,'restraint',a,b,'internal:auto',''))
            s.soft='RESTRAINED'; s.soft_until=end; s.dwell_start=n; s.last_allowed=n; s.auto_after=n+auto.cooldown_seconds; s.version+=1
            s.soft_origin={'source':'autonomous','version':s.version,'start':n,'until':end}
            self.db.conn.execute('INSERT INTO audit_events(at,stream_hash,version,code) VALUES(?,?,?,?)',(n,sha256(stream.encode()).hexdigest()[:16],s.version,'AUTO_RESTRAINED'))
            return True
        return self._run(stream,change)

    def peek(self,stream):
        now=max(self.wall(),self.logical+max(0,self.mono()-self.last_mono))
        return replace(self.snapshots.get(stream,State())).expire(now,self.config.recovery.duration_seconds)

    def guard(self,stream):
        self.now()
        if self.health!='READY':
            s=self.state(stream)
            # No permits while degraded: known deadlines still expire. NORMAL
            # can pass; interval states conservatively consume in memory.
            return self._permit(s,self.now(),stream,persist=False)
        return self._run(stream,lambda s,n:self._permit(s,n,stream,persist=True))

    def _permit(self,s,n,stream,persist):
        if s.hard=='HARD_SILENT' or s.soft=='SOFT_SILENT': return False
        interval=self.config.restraint.min_send_interval_seconds if s.soft=='RESTRAINED' else self.config.recovery.min_send_interval_seconds if s.soft=='RECOVERING' else 0
        if interval:
            if 0<=n-s.last_allowed<=3 and s.burst_count<8:
                s.burst_count+=1
                if not persist: self.snapshots[stream]=replace(s)
                return True
            if n-s.last_allowed<interval: return False
            s.last_allowed=n
            s.burst_count=1
            if not persist: self.snapshots[stream]=replace(s)
        return True

    def reconfigure(self,new):
        old=self.config
        if old.instance_id!=new.instance_id: raise Denied('RELOAD_REQUIRED')
        for route in new.routes:
            prior=next((r for r in old.routes if r.stream_id==route.stream_id),None)
            if prior and prior.identity()!=route.identity(): raise Denied('RELOAD_REQUIRED')
        n=self.now()
        if self.health!='READY': raise Denied(self.reason)
        states={k:replace(s) for k,s in self.snapshots.items()}
        for route in new.routes:
            if new.route(route.stream_id): states.setdefault(route.stream_id,State())
        bindings=self.db.get('identity',[new.instance_id,[]])[1]
        for route in new.routes:
            binding=list(route.identity())
            for previous in bindings:
                if (previous[5]==binding[5] or (previous[0],previous[2],previous[3],previous[4])==
                        (binding[0],binding[2],binding[3],binding[4])) and previous!=binding:
                    raise Denied('RELOAD_REQUIRED')
            if binding not in bindings: bindings.append(binding)
        try:
            with self.db.transaction():
                for stream,s in states.items():
                    route=new.route(stream)
                    if not route:
                        self._trim(stream,n); states[stream]=s=State(version=s.version+1)
                    elif not new.soft.enabled:
                        self._trim(stream,n); s.soft='NORMAL'; s.soft_until=0; s.version+=1
                        s.soft_origin={}
                    elif s.soft in ('SOFT_SILENT','RESTRAINED'):
                        # Conservative clipping also covers requester/group cap reductions.
                        changed=old.soft!=new.soft or old.restraint!=new.restraint
                        if changed:
                            self._trim(stream,n); s.soft='RECOVERING'; s.soft_until=n+new.recovery.duration_seconds; s.version+=1
                    self._save(stream,s)
                self.db.set('config_revision',self.revision+1); self.db.set('last_wall',n)
                self.db.set('identity',[new.instance_id,bindings])
        except (sqlite3.Error,OSError):
            self.health='DEGRADED'; self.reason='STORAGE_UNAVAILABLE'; raise Denied(self.reason)
        self.config=new.model_copy(deep=True); self.snapshots=states; self.revision+=1

    def maintain(self):
        if self.health!='READY': return
        n=self.now()
        for stream in list(self.snapshots): self.state(stream)
        with self.db.transaction():
            for table,col,age in [('intervals','end',7200),('model_calls','at',7200),('requests','at',self.config.storage.request_retention_hours*3600),('message_evidence','at',self.config.storage.request_retention_hours*3600),('audit_events','at',self.config.storage.audit_retention_days*86400)]:
                self.db.conn.execute(f'DELETE FROM {table} WHERE {col}<?',(n-age,))
            self.db.conn.execute('DELETE FROM actor_cooldown WHERE until<?',(n-7200,))

    def close(self):
        self.db.close()
