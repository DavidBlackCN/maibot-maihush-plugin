"""T0 performance measurement; explicitly not an IPC/Host timing claim."""
from pathlib import Path
import argparse
import asyncio
import json
import os
import platform
import time

from maihush.host.config import MaiHushConfig
from maihush.host.plugin import MaiHushPlugin
from tests.test_worker import start
from tests.helpers import message
from maihush.host.messages import evidence
from maihush.decision.candidates import classify


async def run(work):
    routes=[{'enabled':True,'group_id':str(90000000+i),'stream_id':f'bench-{i}'} for i in range(100)]
    cfg=MaiHushConfig.model_validate({'routes':routes})
    p,model=await start(work/'store',cfg)
    try:
        def seed():
            now=p.service.now()
            rows=[(r.stream_id,'silence',now-3500+j,now-3499.9+j,'10003',f'{j}') for r in cfg.routes for j in range(1000)]
            with p.service.db.transaction():
                p.service.db.conn.executemany('INSERT INTO intervals VALUES(?,?,?,?,?,?)',rows)
        await p.io(seed)
        timings=[]; inbound=[]
        for i in range(1200):
            route=cfg.routes[i%100]
            raw=message(mid=str(i),stream=route.stream_id); raw['message_info']['group_info']['group_id']=route.group_id
            t=time.perf_counter(); answer=await p.before_send(message=raw); elapsed=(time.perf_counter()-t)*1000
            assert answer['action']=='continue'
            if i>=200: timings.append(elapsed)
            t=time.perf_counter(); e=evidence(cfg,raw); classify(e,route.bot_account_id)
            if i>=200: inbound.append((time.perf_counter()-t)*1000)
        def stats(samples):
            samples=sorted(samples)
            return {'p95_ms':samples[int(len(samples)*.95)],'p99_ms':samples[int(len(samples)*.99)],'max_ms':max(samples),'n':len(samples)}
        result={'layer':'T0','scope':'plugin before_send including owned executor and SQLite; no Host IPC',
                'system':platform.platform(),'python':platform.python_version(),'cpu':platform.processor(),'cpu_count':os.cpu_count(),
                'groups':100,'history_intervals':100000,'guard':stats(timings),'inbound_screening':stats(inbound),'model_requests':len(model.calls)}
        result['passed']=result['guard']['p95_ms']<50 and result['guard']['p99_ms']<200 and result['inbound_screening']['p95_ms']<50
        return result
    finally: await p.on_unload()


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output-dir',type=Path,required=True); args=parser.parse_args()
    work=args.output_dir.resolve(); work.mkdir(parents=True,exist_ok=False)
    result=asyncio.run(run(work)); (work/'result.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2)); raise SystemExit(0 if result['passed'] else 1)
