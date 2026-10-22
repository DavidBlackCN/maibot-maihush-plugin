from pathlib import Path
from contextlib import contextmanager
import json
import os
import sqlite3
from ..domain.models import Denied

class Database:
    def __init__(self, directory):
        root=Path(directory).absolute()
        if root != root.resolve(): raise Denied('UNSAFE_DATA_PATH')
        root.mkdir(parents=True,exist_ok=True)
        for name in ('writer.lock','maihush.sqlite3','maihush.sqlite3-wal','maihush.sqlite3-shm'):
            p=root/name
            if p.is_symlink() or p.resolve().parent != root: raise Denied('UNSAFE_DATA_PATH')
        self.lease=open(root/'writer.lock','a+b')
        try:
            self.lease.seek(0); self.lease.write(b'0'); self.lease.flush(); self.lease.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.lease.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            self.lease.close(); raise Denied('WRITER_BUSY') from exc
        self.conn=None
        try:
            self.conn=sqlite3.connect(root/'maihush.sqlite3',timeout=.1,isolation_level=None)
            self.conn.execute('PRAGMA busy_timeout=100')
            version=self.conn.execute('PRAGMA user_version').fetchone()[0]
            tables=self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version not in (0,1) or (version==0 and tables): raise Denied('UNKNOWN_SCHEMA')
            self.conn.execute('PRAGMA journal_mode=WAL'); self.conn.execute('PRAGMA synchronous=FULL')
            if version==0:
                self.conn.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE stream_state(stream TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE requests(stream TEXT, mid TEXT, operation TEXT, fingerprint TEXT, result TEXT, at REAL, PRIMARY KEY(stream,mid,operation));
                CREATE TABLE message_evidence(stream TEXT, mid TEXT, fingerprint TEXT, conflict INTEGER DEFAULT 0, at REAL, PRIMARY KEY(stream,mid));
                CREATE TABLE intervals(stream TEXT, kind TEXT, start REAL, end REAL, actor TEXT, mid TEXT, CHECK(end>=start));
                CREATE INDEX intervals_window ON intervals(stream,kind,end);
                CREATE TABLE model_calls(call_id TEXT PRIMARY KEY, stream TEXT, at REAL, status TEXT);
                CREATE INDEX model_calls_window ON model_calls(at,stream);
                CREATE TABLE actor_cooldown(stream TEXT, actor TEXT, until REAL, PRIMARY KEY(stream,actor));
                CREATE TABLE send_reservations(attempt TEXT PRIMARY KEY, stream TEXT, version INTEGER, at REAL, expires REAL);
                CREATE TABLE audit_events(seq INTEGER PRIMARY KEY, at REAL, stream_hash TEXT, version INTEGER, code TEXT);
                PRAGMA user_version=1;
                COMMIT;
                """)
            if self.conn.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise Denied('CORRUPT_STORAGE')
        except BaseException:
            self.close(); raise

    def get(self,key,default=None):
        row=self.conn.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self,key,value):
        self.conn.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',(key,json.dumps(value)))

    @contextmanager
    def transaction(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.conn.execute('COMMIT')
        except BaseException:
            if self.conn.in_transaction: self.conn.execute('ROLLBACK')
            raise

    def close(self):
        if self.conn is not None:
            self.conn.close(); self.conn=None
        if not self.lease.closed:
            if os.name=='nt':
                import msvcrt
                self.lease.seek(0); msvcrt.locking(self.lease.fileno(),msvcrt.LK_UNLCK,1)
            self.lease.close()
