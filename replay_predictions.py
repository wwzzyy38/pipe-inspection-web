import fcntl
import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from bag_replay import connect

MODEL = '78d06ebca47500c08b304f3ca1acc5efa8dc505a2e9a8f0c8062c671d027ad51'


BENCH_LIMIT=60*60


class Predictions:
    def __init__(self, root, bags, live):
        self.root, self.bags, self.live = root, bags, live
        self.cache = root/'prediction-cache'
        self.cache.mkdir(mode=0o700,exist_ok=True)
        self.job = None
        self.last = {}
        self.closing = threading.Event()

    def destination(self, bag):
        key=hashlib.sha256(repr(('pwc-pointnext-v1',MODEL,bag.fingerprint)).encode()).hexdigest()
        return self.cache/f'{bag.id}-{key}.db3'

    def block_reason(self):
        # The caller holds the ROS lock; background ROS activity is not a flight state.
        if not self.live.ready:
            return 'telemetry_unavailable'
        return self.live.flight.reason()

    def bench_reason(self):
        if not self.live.ready:
            return 'bench_waiting_graph'
        return self.live.flight.bench_reason()

    def job_block(self, job):
        if not job['bench']:
            return self.block_reason()
        if time.monotonic() >= job['expires']:
            return 'bench_expired'
        return self.bench_reason()

    def status(self, bag):
        with self.live.lock:
            job=self.job
            if self.destination(bag).is_file():
                return {'state':'ready','total':bag.count}
            if job:
                if job['bag']!=bag:
                    return {'state':'blocked','reason':'other_job'}
                try: done=int((Path(job['temp'].name)/'progress').read_text(encoding='ascii'))
                except (FileNotFoundError,ValueError): done=0
                return {'state':job['reason'] or 'running','reason':job.get('blocked'),'bench':job['bench'],'done':done,'total':job['total']}
            reason=self.block_reason()
            if reason:
                return {'state':'blocked','reason':reason,'last':self.last.get(bag.id),'benchAvailable':self.bench_reason() is None}
            return {'state':self.last.get(bag.id,'idle'),'total':bag.count}

    def read(self, bag, index):
        p=self.destination(bag)
        if not p.is_file(): raise FileNotFoundError(p)
        with connect(p) as con:
            row=con.execute('SELECT data FROM predictions WHERE id=? AND length(data)<=480008',(index,)).fetchone()
        if row is None: raise IndexError(index)
        return row[0]

    def action(self, bag, action):
        with self.live.lock:
            if action=='cancel':
                if not self.job or self.job['bag']!=bag:
                    return False,'no_job'
                self.job['reason']='cancelling'
                return True,'cancelling'
            if self.destination(bag).is_file(): return True,'ready'
            if self.job: return False,'other_job'
            bench=action=='start-bench'
            reason=self.bench_reason() if bench else self.block_reason()
            if reason: return False,reason
            python=Path(os.environ.get('PIPE_POINT_PYTHON','/nonexistent'))
            if not os.access(python,os.X_OK) or any(not Path(os.environ.get(k,'/nonexistent')).exists() for k in ('PIPE_POINT_UPSTREAM','PIPE_POINT_MODEL')):
                return False,'model_missing'
            if shutil.disk_usage(self.cache).free < bag.count*480008*2+512*1024*1024:
                return False,'disk_full'
            with (self.cache/'.worker.lock').open('ab') as slot:
                try: fcntl.flock(slot,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError: return False,'other_job'
                work=tempfile.TemporaryDirectory(prefix='.pending-',dir=self.cache)
                try:
                    with (self.root/'logs'/'offline-predictions.log').open('ab') as log:
                        process=subprocess.Popen([str(python),str(self.root/'predict_worker.py'),str(self.bags.root),bag.id,repr(bag.fingerprint),work.name],
                          cwd=self.root,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=(slot.fileno(),))
                except BaseException:
                    work.cleanup()
                    raise
            job={'bag':bag,'proc':process,'temp':work,'reason':None,'total':bag.count,'bench':bench,'expires':time.monotonic()+BENCH_LIMIT}
            self.job=job
            job['thread']=threading.Thread(target=self.watch,args=(job,),daemon=True)
            job['thread'].start()
            return True,'running'

    def watch(self, job):
        bag,p=job['bag'],job['proc']
        state='failed'
        try:
            while p.poll() is None:
                with self.live.lock:
                    blocked=self.job_block(job)
                    interrupted=self.closing.is_set() or job['reason'] or blocked
                    if interrupted:
                        if not job['reason'] and blocked and not self.closing.is_set():job['blocked']=blocked
                        job['reason']='cancelling'
                if interrupted:
                    try:
                        p.terminate();p.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        p.kill();p.wait(timeout=3)
                    state='cancelled'
                    break
                self.closing.wait(.4)
            if p.returncode==0 and not self.closing.is_set() and not job['reason']:
                current=self.bags.scan()[0].get(bag.id)
                expected=len(self.bags.index(bag).refs) if current==bag else -1
                path=Path(job['temp'].name)/'frames.db3'
                with connect(path) as con:
                    rows=con.execute('SELECT count(*),min(id),max(id) FROM predictions').fetchone()
                with self.live.lock:
                    blocked=self.job_block(job)
                    if rows==(expected,0,expected-1) and not job['reason'] and not blocked and not self.closing.is_set():
                        path.replace(self.destination(bag)); state='ready'
                    elif blocked:
                        job['blocked']=blocked;job['reason']='cancelling'
            if job['reason']:
                blocked=job.get('blocked')
                state=blocked if blocked and job['bench'] else ('flight_stopped' if blocked in ('in_flight','armed') else ('telemetry_stopped' if blocked else 'cancelled'))
        except Exception as exc:
            with (self.root/'logs'/'offline-predictions.log').open('a',encoding='utf-8') as log:
                print(f'Cache verification failed: {type(exc).__name__}: {exc}',file=log)
        finally:
            job['temp'].cleanup()
            with self.live.lock:
                self.last[bag.id]=state
                self.job=None

    def close(self):
        self.closing.set()
        with self.live.lock:
            thread=self.job['thread'] if self.job else None
        if thread:thread.join(9)
