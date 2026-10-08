"""Chat credentials are separate from image credentials and encrypted with DPAPI."""
import base64, json, urllib.parse, uuid
from pathlib import Path
import settings

def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8');tmp.replace(path)
def read(path,default):
    p=Path(path)
    return json.loads(p.read_text('utf-8')) if p.is_file() else default
def providers():
    rows=read(settings.CONFIG/'agent-providers.json',[])
    return [dict(x,available=(settings.CONFIG/'agent-keys'/(x['id']+'.dpapi')).is_file()) for x in rows]
def get_provider(ident):
    p=next((p for p in providers() if p['id']==ident),None)
    if not p: raise ValueError('请选择已保存的对话或视觉模型配置。')
    return p
def save_provider(name,base,model,key='',ident=None,mode='tools',vision=False):
    u=urllib.parse.urlsplit(base.rstrip('/'))
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment: raise ValueError('请输入完整 HTTPS API base，例如 https://api.deepseek.com/v1。')
    if not name.strip() or not model.strip() or mode not in ('tools','json'): raise ValueError('名称、模型或调用方式无效。')
    rows=providers();old=next((p for p in rows if p['id']==ident),None)
    if ident and not old: raise ValueError('未知配置 ID。')
    if old and old['base']!=base.rstrip('/') and not key: raise ValueError('修改地址需重新输入 key，避免把旧 key 发送到其他服务。')
    if not old and not key.strip(): raise ValueError('新配置需要 API key。')
    ident=ident or uuid.uuid4().hex
    with settings.LOCK:
        if key:
            p=settings.CONFIG/'agent-keys'/(ident+'.dpapi');p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(base64.b64encode(settings.crypt(key.strip().encode())).decode(),'ascii')
        row=dict(id=ident,name=name.strip(),base=base.rstrip('/'),model=model.strip(),mode=mode,vision=bool(vision))
        atomic(settings.CONFIG/'agent-providers.json',[{k:v for k,v in x.items() if k!='available'} for x in rows if x['id']!=ident]+[row])
    return row
def credential(ident):
    get_provider(ident)
    return settings.crypt(base64.b64decode((settings.CONFIG/'agent-keys'/(ident+'.dpapi')).read_text('ascii')),True).decode()
def projects(current):
    rows=read(settings.CONFIG/'agent-projects.json',[]);current=str(Path(current).resolve())
    if not any(str(Path(p['root']).resolve()).lower()==current.lower() for p in rows):
        rows.insert(0,dict(root=current,name=Path(current).name));atomic(settings.CONFIG/'agent-projects.json',rows)
    return rows
def add_project(root,current):
    p=Path(root).resolve()
    if not all((p/name).is_dir() for name in ('角色图集','角色特征及参考图','姿势','场景')): raise ValueError('项目需包含角色图集、角色特征及参考图、姿势、场景目录。')
    rows=projects(current)
    if not any(Path(x['root']).resolve()==p for x in rows): rows.append(dict(root=str(p),name=p.name));atomic(settings.CONFIG/'agent-projects.json',rows)
    return rows
def preferences(): return read(settings.CONFIG/'agent-preferences.json',{})
def save_preferences(value): atomic(settings.CONFIG/'agent-preferences.json',value)

