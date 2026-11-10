from __future__ import annotations
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from .contracts import canonical

def utcnow(): return datetime.now(timezone.utc).isoformat()

class Repository:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock()
        self.db.executescript('''PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS objects(kind TEXT,id TEXT,project TEXT,data TEXT,updated TEXT,PRIMARY KEY(kind,id));
          CREATE TABLE IF NOT EXISTS records(seq INTEGER PRIMARY KEY AUTOINCREMENT,project TEXT,source TEXT,
            raw_id TEXT,event_time REAL,received_at TEXT,quality TEXT,entity TEXT,payload TEXT,reason TEXT,
            UNIQUE(project,source,raw_id));
          CREATE TABLE IF NOT EXISTS frames(project TEXT,cursor INTEGER,state TEXT,hash TEXT,
            PRIMARY KEY(project,cursor));
          CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
        ''')
        self.db.commit()
    def put(self,kind,id,data,project=""):
        with self.lock,self.db:
            self.db.execute("INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?)",(kind,id,project,canonical(data),utcnow()))
    def get(self,kind,id):
        with self.lock:
            r=self.db.execute("SELECT data FROM objects WHERE kind=? AND id=?",(kind,id)).fetchone()
        return json.loads(r[0]) if r else None
    def list(self,kind,project=None):
        with self.lock:
            rows=self.db.execute("SELECT data FROM objects WHERE kind=?"+(" AND project=?" if project else "")+" ORDER BY updated DESC",
                (kind,project) if project else (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]
    def meta(self,key,value=None):
        with self.lock:
            if value is not None:
                with self.db: self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",(key,canonical(value)))
                return value
            r=self.db.execute("SELECT value FROM metadata WHERE key=?",(key,)).fetchone()
        return json.loads(r[0]) if r else None
    def record(self,project,source,raw_id,event_time,payload,quality="good",entity="",reason=""):
        with self.lock,self.db:
            cur=self.db.execute("INSERT OR IGNORE INTO records(project,source,raw_id,event_time,received_at,quality,entity,payload,reason) VALUES (?,?,?,?,?,?,?,?,?)",
                (project,source,raw_id,event_time,utcnow(),quality,entity,canonical(payload),reason))
            if cur.rowcount==0 and quality=="good":
                original=self.db.execute("SELECT payload FROM records WHERE project=? AND source=? AND raw_id=?",(project,source,raw_id)).fetchone()
                if original[0]!=canonical(payload): raise ValueError("重复来源 ID 的消息内容不一致，拒绝覆盖原始证据")
            return cur.rowcount==1
    def records(self,project,limit=100):
        with self.lock:
            rows=self.db.execute("SELECT * FROM records WHERE project=? ORDER BY seq DESC LIMIT ?",(project,limit)).fetchall()
        return [{**dict(r),"payload":json.loads(r["payload"])} for r in rows]
    def counts(self,project):
        with self.lock:
            rows=self.db.execute("SELECT source,quality,count(*) AS count FROM records WHERE project=? GROUP BY source,quality",(project,)).fetchall()
        return [dict(r) for r in rows]
    def frame(self,project,cursor,state,hash):
        with self.lock,self.db:
            self.db.execute("INSERT OR REPLACE INTO frames VALUES (?,?,?,?)",(project,cursor,canonical(state),hash))
    def frames(self,project,through=None):
        with self.lock:
            rows=self.db.execute("SELECT cursor,state,hash FROM frames WHERE project=?"+(" AND cursor<=?" if through is not None else "")+" ORDER BY cursor",
                (project,through) if through is not None else (project,)).fetchall()
        return [{"cursor":r[0],"state":json.loads(r[1]),"hash":r[2]} for r in rows]
    def close(self): self.db.close()
