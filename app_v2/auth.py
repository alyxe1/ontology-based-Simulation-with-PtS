from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time

SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


class Accounts:
    def __init__(self,path):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.lock=threading.RLock()
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL,salt BLOB NOT NULL,password BLOB NOT NULL,active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL);
        ''');self.db.commit();self.failures={}

    def users(self):
        with self.lock:return [dict(r) for r in self.db.execute("SELECT id,name,role,active FROM users ORDER BY id")]
    def initialized(self):return bool(self.users())

    def create(self,username,name,role,password):
        import re
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,48}",username):raise ValueError("账号需为 3–48 位字母、数字、点、下划线或短横线")
        if role not in ("admin","analyst","auditor"):raise ValueError("角色无效")
        if len(password)<10:raise ValueError("密码至少 10 个字符")
        salt=os.urandom(16);encoded=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1)
        with self.lock,self.db:self.db.execute("INSERT INTO users(id,name,role,salt,password) VALUES (?,?,?,?,?)",(username,name,role,salt,encoded))
        return {"id":username,"name":name,"role":role}

    def login(self,username,password):
        with self.lock:
            attempts=[t for t in self.failures.get(username,[]) if t>time.time()-300]
            if len(attempts)>=10:raise ValueError("登录失败次数过多，请 5 分钟后重试")
            user=self.db.execute("SELECT * FROM users WHERE id=? AND active=1",(username,)).fetchone()
        salt=user["salt"] if user else b"0"*16
        candidate=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1)
        if not user or not hmac.compare_digest(candidate,user["password"]):
            with self.lock:self.failures.setdefault(username,[]).append(time.time())
            raise ValueError("账号或密码错误")
        token=secrets.token_urlsafe(40)
        with self.lock,self.db:
            self.failures.pop(username,None)
            self.db.execute("INSERT INTO sessions VALUES (?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),username,time.time()+SESSION_TTL_SECONDS))
        return token,{k:user[k] for k in ("id","name","role")}

    def session(self,token):
        if not token:return None
        # FastAPI sync dependencies run in different threads. Serialize both reads
        # and writes on the shared connection, including cursor consumption.
        with self.lock:
            row=self.db.execute("SELECT u.id,u.name,u.role FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1",(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        return dict(row) if row else None

    def logout(self,token):
        with self.lock,self.db:self.db.execute("DELETE FROM sessions WHERE token=?",(hashlib.sha256(token.encode()).hexdigest(),))

    def close(self):
        with self.lock:self.db.close()
