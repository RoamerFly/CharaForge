"""Offline task bundles and verified project snapshots. No credential files or API calls."""
import hashlib,html,json,os,re,shutil,sqlite3,tempfile,time,uuid,zipfile
from pathlib import Path,PurePosixPath
from urllib.parse import quote
from contextlib import closing
from agent.store import Store,now

TABLES={'chats':(), 'messages':('body',), 'tasks':('config',), 'events':('payload',),
        'calls':('request','result'), 'plans':('body',), 'reviews':('body',), 'repair_masks':('body',)}
MEDIA={'.png','.jpg','.jpeg','.webp'}
TEXT={'.json','.txt','.md'}
SCOPE=('姿势','场景','面部表情','质量约束nagetive','角色特征及参考图','角色图集','other/agent')
BLOCKED={'auth.json','config.toml','apis.json','agent-providers.json','network.json','agent-projects.json','agent-preferences.json'}
SECRET_FIELDS={'api_key','api-key','authorization','password','access_token','refresh_token','token','key','credential'}
MAX_BYTES=50*1024**3
MAX_FILES=100000

class Cancelled(Exception):pass

def check(cancel):
    if cancel and cancel.is_set():raise Cancelled('已取消本地操作，未发布不完整的 ZIP 或恢复目录。')

def safe(value,key=''):
    if key.lower() in SECRET_FIELDS or re.sub(r'[_-]','',key.lower()) in {'apikey','accesstoken','refreshtoken'}:return '[redacted]'
    if key.lower() in {'b64_json','image_base64','base64_image','image_data'} and isinstance(value,str):return '[omitted inline image; use image attachment snapshot]'
    if isinstance(value,dict):
        if str(value.get('mimeType',value.get('mime_type',''))).startswith('image/') and isinstance(value.get('data'),str):value=dict(value,data='[omitted inline image; use image attachment snapshot]')
        return {k:safe(v,k) for k,v in value.items()}
    if isinstance(value,list):return [safe(v) for v in value]
    if isinstance(value,str):
        if value.startswith('data:image/'):return '[omitted inline image; use image attachment snapshot]'
        value=re.sub(r'\bsk-[A-Za-z0-9_-]{12,}\b','[redacted key]',value)
        return re.sub(r'(?i)\bBearer\s+[A-Za-z0-9_.-]{12,}','Bearer [redacted]',value)
    return value

def sha_bytes(value):return hashlib.sha256(value).hexdigest()
def dumped(value):return json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')

def allowed(path,root,include_images):
    p=Path(path).resolve()
    if not p.is_relative_to(root) or not p.is_file():return False
    rel=p.relative_to(root)
    if any(x.lower().startswith('temp') for x in rel.parts) or p.name.lower() in BLOCKED:return False
    return p.suffix.lower() in TEXT or (include_images and p.suffix.lower() in MEDIA)

def walk(folder,root):
    if not folder.is_dir() or not folder.resolve().is_relative_to(root):return
    for here,dirs,names in os.walk(folder,followlinks=False):
        dirs[:]=[d for d in dirs if not d.lower().startswith('temp') and not (Path(here)/d).is_symlink() and (Path(here)/d).resolve().is_relative_to(root)]
        for name in names:yield Path(here)/name

def snapshot(store,dest,cancel=None):
    """SQLite online backup captures WAL data; sanitize only the detached copy."""
    check(cancel)
    with store.connect() as src,closing(sqlite3.connect(dest)) as out:
        src.backup(out,pages=128,progress=lambda *args:check(cancel))
        if out.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('数据库快照完整性检查失败。')
        out.execute('PRAGMA secure_delete=ON')
        for table,columns in TABLES.items():
            for col in columns:
                rows=out.execute(f'SELECT rowid,{col} FROM {table}').fetchall()
                for rowid,value in rows:out.execute(f'UPDATE {table} SET {col}=? WHERE rowid=?',(json.dumps(safe(json.loads(value)),ensure_ascii=False),rowid))
        for table,col in (('chats','title'),):
            for rowid,value in out.execute(f'SELECT rowid,{col} FROM {table}').fetchall():out.execute(f'UPDATE {table} SET {col}=? WHERE rowid=?',(safe(value),rowid))
        out.commit();out.execute('VACUUM')
    check(cancel)

def task_data(store,task):
    # One read transaction: report and request inventory refer to the same journal snapshot.
    with store.connect() as db:
        db.execute('BEGIN')
        row=db.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone()
        if not row:raise ValueError('当前没有可导出的任务。')
        row=dict(row);row['config']=json.loads(row['config']);chat=row['chat']
        value=dict(task=row,chat=dict(db.execute('SELECT * FROM chats WHERE id=?',(chat,)).fetchone()))
        for table,columns in TABLES.items():
            if table in ('tasks','chats'):continue
            query='chat' if table=='messages' else 'task'
            rows=[dict(x) for x in db.execute(f'SELECT * FROM {table} WHERE {query}=? ORDER BY rowid',(chat if table=='messages' else task,))]
            for item in rows:
                for col in columns:item[col]=json.loads(item[col])
            value[table]=rows
    return safe(value)

def paths_in(value):
    if isinstance(value,dict):
        for v in value.values():yield from paths_in(v)
    elif isinstance(value,list):
        for v in value:yield from paths_in(v)
    elif isinstance(value,str) and '\n' not in value and len(value)<32768:
        try:
            p=Path(value)
            if p.is_absolute():yield p
        except (OSError,ValueError):pass

def prepare(store,kind='project',task=None,include_images=False,cancel=None):
    if kind not in ('task','project'):raise ValueError('未知导出类型。')
    root=store.root;data=task_data(store,task) if kind=='task' else None;files=set();missing=set()
    def add(p):
        check(cancel)
        try:
            if allowed(p,root,include_images):files.add(Path(p).resolve())
        except (OSError,ValueError):pass
    for name in ('AGENTS.md','生图经验.md'):add(root/name)
    scopes=SCOPE if kind=='project' else ('姿势','场景','面部表情','质量约束nagetive')
    for scope in scopes:
        for p in walk(root/scope,root):add(p)
    if data:
        identities={(e['payload'].get('role'),e['payload'].get('image_id')) for e in data['events'] if e['kind']=='image'}
        for role,image_id in identities:
            if not isinstance(role,str) or Path(role).name!=role or not isinstance(image_id,str) or not re.fullmatch('[a-f0-9]{32}',image_id):continue
            for meta in (root/'角色图集'/role/'other').rglob('metadata.json'):
                if meta.parent.name!=image_id or not allowed(meta,root,False):continue
                add(meta)
                try:
                    for p in paths_in(json.loads(meta.read_text('utf-8'))):add(p)
                except (ValueError,OSError):pass
            for p in walk(root/'角色图集'/role/'other/agent/tasks'/task,root):add(p)
            for p in walk(root/'角色特征及参考图'/role/'other/character',root):add(p)
        for p in paths_in(data):
            try:
                if p.resolve().is_relative_to(root) and not p.is_file():missing.add(str(p))
                add(p)
            except (OSError,ValueError):pass
    inventory=[]
    for p in sorted(files):
        check(cancel);s=p.stat();inventory.append(dict(source=str(p),path=p.relative_to(root).as_posix(),size=s.st_size,mtime_ns=s.st_mtime_ns))
    total=sum(x['size'] for x in inventory)
    if total>MAX_BYTES or len(inventory)>MAX_FILES:raise ValueError('项目超过当前 ZIP 上限（50 GiB／10 万文件）。')
    roles=sorted(p.name for p in (root/'角色图集').iterdir() if p.is_dir() and p.name!='other' and not p.name.lower().startswith('temp')) if (root/'角色图集').is_dir() else []
    directories=list(SCOPE)+['角色图集/'+r for r in roles]+['角色特征及参考图/'+r for r in roles]
    return dict(kind=kind,root=str(root),task=task,include_images=bool(include_images),files=inventory,data=data,missing=sorted(missing),directories=directories,total_bytes=total,image_count=sum(Path(x['path']).suffix.lower() in MEDIA for x in inventory),created=now())

def report(plan):
    data=plan.get('data');parts=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>生图任务记录</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#24334d;background:#f7f9fc}section,details{background:white;padding:18px;border-radius:12px;margin:15px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}img{max-width:280px;max-height:340px}a{color:#3169ff}</style><h1>生图任务记录</h1>']
    parts.append('<p>本地离线记录；导出不调用 API。包含图片：'+str(plan['include_images'])+'。状态是导出时的记录，不代表新增质量审核。</p>')
    if data:
        parts.append('<section><h2>任务</h2><pre>'+html.escape(json.dumps(data['task'],ensure_ascii=False,indent=2))+'</pre></section>')
        parts.append('<section><h2>聊天上下文</h2>')
        for m in data['messages']:parts.append('<h3>'+html.escape(m['role'])+'</h3><pre>'+html.escape(str(m['body'].get('text','') if isinstance(m['body'],dict) else m['body']))+'</pre>')
        parts.append('</section>')
        for call in data['calls']:parts.append('<details><summary>'+html.escape(call['kind']+' · '+call['status']+' · '+call['id'])+'</summary><pre>'+html.escape(json.dumps(call,ensure_ascii=False,indent=2))+'</pre></details>')
    for f in plan['files']:
        if Path(f['path']).suffix.lower() in MEDIA:
            uri='project/'+quote(f['path'],safe='/');parts.append('<section><a href="'+uri+'"><img loading="lazy" src="'+uri+'"></a><p>'+html.escape(f['path'])+'</p></section>')
    parts.append('</html>');return '\n'.join(parts).encode('utf-8')

def export_bundle(store,plan,destination,cancel=None,progress=None):
    if Path(plan['root']).resolve()!=store.root:raise ValueError('导出清单不属于当前项目。')
    target=Path(destination).resolve()
    if target.suffix.lower()!='.zip' or target.exists():raise ValueError('请使用尚不存在的 ZIP 文件名。')
    if not target.parent.is_dir():raise ValueError('导出目录不存在。')
    partial=target.with_name(target.name+'.partial-'+uuid.uuid4().hex)
    entries=[];manifest={k:v for k,v in plan.items() if k not in ('files','data')};manifest.update(format='cosplay-agent-bundle',version=1,entries=entries,credentials_included=False,embedded_image_payloads='omitted; images are optional separate files')
    try:
        with tempfile.TemporaryDirectory() as temp,zipfile.ZipFile(partial,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=4) as z:
            def write(name,value):
                check(cancel);z.writestr(name,value);entries.append(dict(path=name,size=len(value),sha256=sha_bytes(value)))
            def stream(name,source):
                h=hashlib.sha256();size=0
                with source.open('rb') as src,z.open(name,'w') as out:
                    while chunk:=src.read(1024**2):check(cancel);h.update(chunk);size+=len(chunk);out.write(chunk)
                entries.append(dict(path=name,size=size,sha256=h.hexdigest()))
            if plan['kind']=='project':
                db=Path(temp)/'agent.sqlite3';snapshot(store,db,cancel);stream('project/other/agent/agent.sqlite3',db)
            else:write('task.json',dumped(plan['data']))
            for n,f in enumerate(plan['files'],1):
                check(cancel);p=Path(f['source']).resolve()
                if not allowed(p,store.root,plan['include_images']) or p.relative_to(store.root).as_posix()!=f['path']:raise ValueError('清单路径已变化，请重新预览。')
                before=p.stat()
                if before.st_size!=f['size'] or before.st_mtime_ns!=f['mtime_ns']:raise ValueError('文件在预览后发生变化，请重新预览：'+f['path'])
                if p.suffix.lower() in TEXT:
                    if before.st_size>64*1024**2:raise ValueError('单个文本记录超过 64 MiB，请先拆分：'+f['path'])
                    raw=p.read_bytes()
                    text=raw.decode('utf-8-sig');raw=dumped(safe(json.loads(text))) if p.suffix.lower()=='.json' else safe(text).encode('utf-8')
                    write('project/'+f['path'],raw)
                else:stream('project/'+f['path'],p)
                after=p.stat()
                if (after.st_size,after.st_mtime_ns)!=(before.st_size,before.st_mtime_ns):raise ValueError('读取期间文件变化，停止发布备份。')
                if progress:progress(f'正在归档 {n}/{len(plan["files"])}：{f["path"]}')
            write('报告.html',report(plan));write('说明.txt','本地任务导出／项目备份。manifest.json 为文件校验清单。报告.html 可离线查看。\n不包含程序、API配置或凭据文件；聊天与附件仍可能包含私人信息。\n未勾选图片时仅保留记录与文本；恢复不具备完整图片素材。\n项目备份请通过程序恢复到空目录；已提交任务不会自动重发。\n'.encode('utf-8'))
            z.writestr('manifest.json',dumped(manifest))
        verify_bundle(partial,cancel,progress)
        check(cancel)
        if target.exists():raise ValueError('目标 ZIP 已被其他文件占用，未覆盖。')
        partial.rename(target)
        return dict(path=str(target),files=len(entries),bytes=target.stat().st_size,images=plan['image_count'],kind=plan['kind'])
    finally:
        if partial.is_file():partial.unlink()

def member_name(name):
    p=PurePosixPath(name)
    if not name or '\\' in name or p.is_absolute() or any(x in ('','.','..') or ':' in x or x.rstrip(' .')!=x for x in name.split('/')):raise ValueError('ZIP 包含非法相对路径。')
    if any(re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',x) for x in p.parts):raise ValueError('ZIP 含 Windows 保留名称。')
    return p

def verify_bundle(path,cancel=None,progress=None):
    with zipfile.ZipFile(path) as z:
        info=z.infolist();names=[x.filename for x in info]
        if len(names)>MAX_FILES+5 or len({x.casefold() for x in names})!=len(names):raise ValueError('ZIP 文件过多或存在重复文件路径。')
        for x in info:
            member_name(x.filename)
            if x.file_size<0 or x.flag_bits&1 or (x.external_attr>>16)&0o170000==0o120000:raise ValueError('不接受加密或链接文件。')
        if sum(x.file_size for x in info)>MAX_BYTES:raise ValueError('ZIP 解压大小超过 50 GiB 上限。')
        if 'manifest.json' not in names or z.getinfo('manifest.json').file_size>32*1024**2:raise ValueError('缺少有效清单。')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('format')!='cosplay-agent-bundle' or manifest.get('version')!=1 or manifest.get('kind') not in ('task','project') or not isinstance(manifest.get('entries'),list):raise ValueError('不是受支持的生图记录包。')
        entries=manifest['entries'];expected=[x['path'] for x in entries]
        if len(set(expected))!=len(expected) or set(names)!=set(expected)|{'manifest.json'}:raise ValueError('文件清单与 ZIP 内容不一致。')
        for n,item in enumerate(entries,1):
            check(cancel);h=hashlib.sha256();size=0
            with z.open(item['path']) as f:
                while chunk:=f.read(1024**2):check(cancel);h.update(chunk);size+=len(chunk)
            if size!=item['size'] or h.hexdigest()!=item['sha256']:raise ValueError('文件校验失败：'+item['path'])
            if progress:progress(f'正在校验 {n}/{len(entries)}')
        return dict(manifest=manifest,files=len(entries),bytes=sum(x['size'] for x in entries),verified=True)

PROMPT_FIELDS={'prompt','effective_prompt','original_input','character','pose','scene','expression','constraints','repair','summary','text','observation','reason','error','error_message','detail','note','notes','repair_reason','caption'}
def relocate(value,old,new,key=''):
    if key in PROMPT_FIELDS and isinstance(value,str):return value
    if isinstance(value,dict):return {k:relocate(v,old,new,k) for k,v in value.items()}
    if isinstance(value,list):return [relocate(v,old,new,key) for v in value]
    if isinstance(value,str) and '\n' not in value:
        try:
            p=Path(value)
            if p.is_absolute() and p.is_relative_to(old):return str(new/p.relative_to(old))
        except ValueError:pass
    return value

def restore_bundle(path,destination,cancel=None,progress=None):
    check(cancel);verified=verify_bundle(path,cancel,progress);manifest=verified['manifest']
    if manifest['kind']!='project':raise ValueError('任务导出用于离线查看，不能作为完整项目恢复。')
    dest=Path(destination).resolve();old=Path(manifest['root'])
    if not old.is_absolute() or dest==old or not dest.parent.is_dir() or (dest.exists() and (not dest.is_dir() or any(dest.iterdir()))):raise ValueError('恢复目标必须为原项目之外的新目录或空目录。')
    # Extract into our own bounded temporary directory, publish only after full validation.
    with tempfile.TemporaryDirectory(prefix='.agent-restore-',dir=dest.parent) as staging:
        stage=Path(staging)/'project';stage.mkdir()
        with zipfile.ZipFile(path) as z:
            for item in manifest['entries']:
                check(cancel);rel=member_name(item['path'])
                if rel.parts[0]!='project':continue
                target=stage.joinpath(*rel.parts[1:]).resolve()
                if not target.is_relative_to(stage):raise ValueError('恢复路径越界。')
                target.parent.mkdir(parents=True,exist_ok=True)
                h=hashlib.sha256();size=0
                with z.open(item['path']) as src,target.open('xb') as out:
                    while chunk:=src.read(1024**2):check(cancel);h.update(chunk);size+=len(chunk);out.write(chunk)
                if size!=item['size'] or h.hexdigest()!=item['sha256']:raise ValueError('恢复时文件校验失败，未发布目录。')
        for name in manifest.get('directories',[]):
            rel=member_name(name);p=stage.joinpath(*rel.parts).resolve()
            if not p.is_relative_to(stage):raise ValueError('项目目录越界。')
            p.mkdir(parents=True,exist_ok=True)
        dbfile=stage/'other/agent/agent.sqlite3'
        if not dbfile.is_file():raise ValueError('项目备份缺少数据库快照。')
        for p in stage.rglob('*.json'):
            check(cancel);value=json.loads(p.read_text('utf-8-sig'));p.write_bytes(dumped(relocate(value,old,dest)))
        with closing(sqlite3.connect(dbfile)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('恢复数据库校验失败。')
            for table,columns in TABLES.items():
                for col in columns:
                    for rowid,value in db.execute(f'SELECT rowid,{col} FROM {table}').fetchall():db.execute(f'UPDATE {table} SET {col}=? WHERE rowid=?',(json.dumps(relocate(json.loads(value),old,dest),ensure_ascii=False),rowid))
            db.execute("UPDATE tasks SET status='interrupted',updated=? WHERE status IN ('queued','running')",(now(),))
            db.execute("UPDATE calls SET status='unknown' WHERE status='started'")
            for ident,body in db.execute('SELECT id,body FROM repair_masks').fetchall():
                value=json.loads(body)
                if value.get('status')=='approved':value.update(status='pending',approval=None);db.execute('UPDATE repair_masks SET body=? WHERE id=?',(json.dumps(value,ensure_ascii=False),ident))
            db.commit()
        marker=stage/'other/agent/restore-map.json';marker.write_bytes(dumped(dict(old_root=str(old),new_root=str(dest),source_bundle=str(Path(path).resolve()),at=now(),images_included=manifest['include_images'],policy='历史任务只查看；新一轮读取当前条目并重新保存计划。蒙版需重新确认。原始请求文本未改写，原包保留原路径记录。')))
        check(cancel)
        if dest.exists():dest.rmdir() # Fails safely if another process put files there.
        stage.rename(dest)
    return dict(root=str(dest),files=verified['files'],images_included=manifest['include_images'],status='restored_without_replay')
