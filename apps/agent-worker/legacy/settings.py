"""Editable API profiles. Custom keys are encrypted by Windows DPAPI."""
import base64
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import threading
import urllib.parse
import uuid
import os,time
from paths import APP

CONFIG=APP/'config'
LOCK=threading.RLock()

def network():
    p=CONFIG/'network.json'
    return dict(proxy_enabled=False,proxy_url='http://127.0.0.1:7890')|(json.loads(p.read_text('utf-8')) if p.is_file() else {})

def proxy_url(value):
    value=value.strip()
    if value and '://' not in value:value='http://'+value
    u=urllib.parse.urlsplit(value)
    try:port=u.port
    except ValueError:raise ValueError('代理端口无效。')
    if u.scheme not in ('http','https') or not u.hostname or not port or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        raise ValueError('请输入 HTTP/HTTPS 代理地址和端口，如 http://127.0.0.1:7890；不含账号、路径或查询参数。')
    return value.rstrip('/')

def save_network(enabled,url):
    result=dict(proxy_enabled=bool(enabled),proxy_url=proxy_url(url))
    with LOCK:
        CONFIG.mkdir(parents=True,exist_ok=True);p=CONFIG/'network.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8');tmp.replace(p)
    return result

def active_proxy():
    value=network()
    return proxy_url(value['proxy_url']) if value['proxy_enabled'] else None

def network_env(original=None):
    env=dict(os.environ if original is None else original);url=active_proxy()
    if url:
        for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','WS_PROXY','WSS_PROXY'):
            env[key]=url;env[key.lower()]=url
        # Local services should remain reachable without a proxy hop.
        bypass=[x.strip() for x in env.get('NO_PROXY',env.get('no_proxy','')).split(',') if x.strip()]
        env['NO_PROXY']=env['no_proxy']=','.join(dict.fromkeys(bypass+['localhost','127.0.0.1','::1']))
    return env

def probe_proxy(url):
    from api_client import DefaultHttpxClient
    url=proxy_url(url);started=time.monotonic()
    with DefaultHttpxClient(proxy=url,trust_env=False,timeout=15,follow_redirects=False) as client:
        response=client.get('https://chatgpt.com/cdn-cgi/trace')
    return dict(proxy_url=url,https_connected=True,http_status=response.status_code,seconds=round(time.monotonic()-started,2))
BUILTINS={
    'sraiapi-upscale':dict(name='老接口 · 超分 key',base='https://sraiapi.com/v1',model='gpt-image-2',mask_support='unknown'),
    'sraiapi-native':dict(name='老接口 · 原生 key',base='https://sraiapi.com/v1',model='gpt-image-2',mask_support='unknown'),
    'newtransfer':dict(name='新接口',base='https://www.newtransfer.site/v1',model='gpt-image-2',mask_support='unknown'),
    'custom':dict(name='临时自定义',base='',model='gpt-image-2',mask_support='unknown'),
}
PROTOCOLS={'auto':'自动识别模型', 'openai-images':'GPT / OpenAI Images', 'grok-json':'Grok / JSON参考图'}
GROK_MODELS=('grok-imagine-image-quality','grok-imagine','grok-imagine-image')

def request_protocol(profile,model):
    return resolve_protocol(get(profile) or {},model)

def resolve_protocol(cfg,model):
    protocol=cfg.get('protocol','auto')
    if protocol not in PROTOCOLS:raise ValueError('接口请求格式无效，请在接口设置中重新选择。')
    return ('grok-json' if model.lower().startswith('grok-') else 'openai-images') if protocol=='auto' else protocol

class BLOB(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]

def crypt(raw, decrypt=False):
    buffer=ctypes.create_string_buffer(raw)
    src=BLOB(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));dst=BLOB()
    dll=ctypes.WinDLL('crypt32',use_last_error=True)
    dll.CryptProtectData.argtypes=[ctypes.POINTER(BLOB),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(BLOB)]
    dll.CryptProtectData.restype=wintypes.BOOL
    dll.CryptUnprotectData.argtypes=[ctypes.POINTER(BLOB),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(BLOB)]
    dll.CryptUnprotectData.restype=wintypes.BOOL
    if decrypt:
        ok=dll.CryptUnprotectData(ctypes.byref(src),None,None,None,None,1,ctypes.byref(dst))
    else:
        ok=dll.CryptProtectData(ctypes.byref(src),'CharacterImageWorkbench',None,None,None,1,ctypes.byref(dst))
    if not ok:raise ctypes.WinError(ctypes.get_last_error())
    try:return ctypes.string_at(dst.pbData,dst.cbData)
    finally:
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
        kernel.LocalFree(dst.pbData)

def load():
    p=CONFIG/'apis.json'
    if not p.is_file():return []
    return json.loads(p.read_text('utf-8'))

def profiles():
    overrides={x['id']:x for x in load()}
    result=[]
    for pid,v in BUILTINS.items():result.append(dict(v,**{'id':pid})|overrides.pop(pid,{}))
    result+=list(overrides.values())
    for item in result:
        item.setdefault('protocol','auto')
        item['available']=(CONFIG/'keys'/(item['id']+'.dpapi')).is_file() or (Path.home()/'.codex/secrets/imagegen'/(item['id']+'.dpapi')).is_file()
    import codex_channel
    return result+[codex_channel.profile()]

def get(pid):
    return next((x for x in profiles() if x['id']==pid),None)

def save(name,base,model,key='',pid=None,mask_support='unknown',protocol='auto'):
    if not name.strip() or not model.strip():raise ValueError('接口名称和模型不能为空。')
    u=urllib.parse.urlsplit(base.rstrip('/'))
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment:raise ValueError('接口地址需为完整HTTPS base，不含key或查询参数。')
    if mask_support not in ('unknown','supported','unsupported'):raise ValueError('蒙版能力标记无效。')
    if protocol not in PROTOCOLS:raise ValueError('接口请求格式无效。')
    pid=pid or ('user-'+uuid.uuid4().hex)
    if pid=='custom' or not (pid in BUILTINS or (pid.startswith('user-') and len(pid)==37 and all(c in '0123456789abcdef' for c in pid[5:]))):raise ValueError('请选择已保存接口，或新建接口。')
    with LOCK:
        CONFIG.mkdir(exist_ok=True);(CONFIG/'keys').mkdir(exist_ok=True)
        previous=get(pid)
        if previous and previous['base']!=base.rstrip('/') and not key:
            raise ValueError('修改接口地址时请重新输入key，防止旧key被发往其他服务。')
        if key:
            encrypted=crypt(key.encode())
            (CONFIG/'keys'/(pid+'.dpapi')).write_text(base64.b64encode(encrypted).decode(),'ascii')
        entry=dict(id=pid,name=name.strip(),base=base.rstrip('/'),model=model.strip(),mask_support=mask_support,protocol=protocol)
        rows=[x for x in load() if x['id']!=pid]+[entry]
        p=CONFIG/'apis.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf-8');tmp.replace(p)
        return entry

def custom_key(pid):
    if get(pid) is None:raise ValueError('未知凭据配置。')
    p=CONFIG/'keys'/(pid+'.dpapi')
    return crypt(base64.b64decode(p.read_text('ascii')),decrypt=True).decode() if p.is_file() else None
