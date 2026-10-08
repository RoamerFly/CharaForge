"""Durable task journal. A request reserved before submission is never replayed."""
import json, sqlite3, time, uuid
from contextlib import contextmanager
from pathlib import Path

def now(): return time.strftime('%Y-%m-%d %H:%M:%S')
def encode(value): return json.dumps(value, ensure_ascii=False)

class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.folder = self.root / 'other' / 'agent'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'agent.sqlite3'
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,title TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,chat TEXT,role TEXT,body TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,chat TEXT,status TEXT,config TEXT,created TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task TEXT,kind TEXT,payload TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,task TEXT,kind TEXT,fingerprint TEXT,status TEXT,request TEXT,result TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS plans(task TEXT PRIMARY KEY,body TEXT);
            CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,task TEXT,asset TEXT,sha TEXT,body TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS repair_masks(id TEXT PRIMARY KEY,task TEXT,asset TEXT,body TEXT,created TEXT);
            CREATE INDEX IF NOT EXISTS events_task ON events(task,seq);
            CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat,seq);
            ''')
    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=30000')
        try:
            with db: yield db
        finally: db.close()
    def chats(self):
        with self.connect() as db: return [dict(x) for x in db.execute('SELECT * FROM chats ORDER BY created DESC,rowid DESC')]
    def new_chat(self, title='新聊天'):
        ident = uuid.uuid4().hex
        with self.connect() as db: db.execute('INSERT INTO chats VALUES(?,?,?)',(ident,title,now()))
        return ident
    def rename_chat(self, chat, title):
        with self.connect() as db: db.execute('UPDATE chats SET title=? WHERE id=?',(title[:70],chat))
    def message(self, chat, role, body):
        with self.connect() as db:
            if not db.execute('SELECT id FROM chats WHERE id=?',(chat,)).fetchone(): raise ValueError('聊天不存在')
            db.execute('INSERT INTO messages(chat,role,body,created) VALUES(?,?,?,?)',(chat,role,encode(body),now()))
    def messages(self, chat):
        with self.connect() as db:
            return [dict(x,body=json.loads(x['body'])) for x in db.execute('SELECT * FROM messages WHERE chat=? ORDER BY seq',(chat,))]
    def new_task(self, chat, config):
        ident = uuid.uuid4().hex
        with self.connect() as db:
            if not db.execute('SELECT id FROM chats WHERE id=?',(chat,)).fetchone(): raise ValueError('聊天不存在')
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',(ident,chat,'queued',encode(config),now(),now()))
        self.event(ident,'status',{'status':'queued'})
        return ident
    def task(self, ident):
        with self.connect() as db: row=db.execute('SELECT * FROM tasks WHERE id=?',(ident,)).fetchone()
        if not row: raise ValueError('任务不存在')
        return dict(row,config=json.loads(row['config']))
    def tasks(self, chat):
        with self.connect() as db: return [dict(x) for x in db.execute('SELECT * FROM tasks WHERE chat=? ORDER BY created,rowid',(chat,))]
    def claim(self, ident):
        with self.connect() as db:
            changed=db.execute("UPDATE tasks SET status='running',updated=? WHERE id=? AND status='queued'",(now(),ident)).rowcount
            if changed!=1: raise ValueError('此任务已被执行或中断，不允许重新提交原请求。请在聊天中继续。')
    def status(self, ident, status):
        with self.connect() as db: db.execute('UPDATE tasks SET status=?,updated=? WHERE id=?',(status,now(),ident))
        self.event(ident,'status',{'status':status})
    def event(self, task, kind, payload):
        # Only structured local outputs and redacted provider errors belong here.
        with self.connect() as db: db.execute('INSERT INTO events(task,kind,payload,created) VALUES(?,?,?,?)',(task,kind,encode(payload),now()))
    def events(self, task, after=0):
        with self.connect() as db:
            return [dict(x,payload=json.loads(x['payload'])) for x in db.execute('SELECT * FROM events WHERE task=? AND seq>? ORDER BY seq',(task,after))]
    def reserve(self, task, kind, fingerprint, request, limit):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count=db.execute('SELECT count(*) FROM calls WHERE task=? AND kind=?',(task,kind)).fetchone()[0]
            if count>=limit: raise ValueError(f'{kind} 请求次数上限已达到（{limit}），停止自动调用。')
            previous=db.execute("SELECT status FROM calls WHERE kind=? AND fingerprint=? AND status IN ('started','unknown','safety_rejected')",(kind,fingerprint)).fetchone()
            if previous: raise ValueError('相同请求已处于未知、运行或安全拒绝状态；不会重复发送，请人工确认。')
            ident=uuid.uuid4().hex
            db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?)',(ident,task,kind,fingerprint,'started',encode(request),'{}',now()))
        return ident
    def finish_call(self, ident, status, result):
        with self.connect() as db: db.execute('UPDATE calls SET status=?,result=? WHERE id=?',(status,encode(result),ident))
    def plan(self, task, body=None):
        with self.connect() as db:
            if body is not None: db.execute('INSERT OR REPLACE INTO plans VALUES(?,?)',(task,encode(body)))
            row=db.execute('SELECT body FROM plans WHERE task=?',(task,)).fetchone()
        return json.loads(row[0]) if row else None
    def review(self, task, asset, sha, body):
        ident=uuid.uuid4().hex
        with self.connect() as db: db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?)',(ident,task,asset,sha,encode(body),now()))
        return ident
    def latest_review(self, asset):
        with self.connect() as db: row=db.execute('SELECT * FROM reviews WHERE asset=? ORDER BY rowid DESC LIMIT 1',(asset,)).fetchone()
        return dict(row,body=json.loads(row['body'])) if row else None
    def save_mask(self, task, asset, body, ident=None):
        ident=ident or uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO repair_masks VALUES(?,?,?,?,?)',(ident,task,asset,encode(body),now()))
        return ident
    def mask(self, ident):
        with self.connect() as db: row=db.execute('SELECT * FROM repair_masks WHERE id=?',(ident,)).fetchone()
        if not row: raise ValueError('蒙版记录不存在。')
        return dict(row,body=json.loads(row['body']))
    def masks(self, asset=None):
        with self.connect() as db:
            query='SELECT * FROM repair_masks'+(' WHERE asset=?' if asset else '')+' ORDER BY rowid DESC'
            rows=db.execute(query,(asset,) if asset else ()).fetchall()
        return [dict(row,body=json.loads(row['body'])) for row in rows]
    def interrupt(self, ident, reason):
        with self.connect() as db:
            db.execute("UPDATE calls SET status='unknown' WHERE task=? AND status='started'",(ident,))
        self.event(ident,'error',{'error':reason})
        self.status(ident,'interrupted')
