"""Single user-submitted Image API request. Keys stay in server memory only."""
import base64
from contextlib import ExitStack
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

from provenance import ROOT, sha

DEPS = Path(r'E:\freetime\AI_Draw\imagegen-api-deps')
if not getattr(sys,'frozen',False) and DEPS.is_dir(): sys.path.insert(0, str(DEPS))
from openai import OpenAI,DefaultHttpxClient
from PIL import Image

import request_codec as adapter
import settings

PROFILES = {
    'sraiapi-upscale': ('老接口 · 超分 key', 'https://sraiapi.com/v1'),
    'sraiapi-native': ('老接口 · 原生 key', 'https://sraiapi.com/v1'),
    'newtransfer': ('新接口', 'https://www.newtransfer.site/v1'),
    'custom': ('自定义接口 / key', ''),
}
SECRETS = Path.home()/'.codex/secrets/imagegen'
RULES = ROOT/'质量约束nagetive'
MODULES = {'general': '通用人体与手指.txt', 'hosiery': '白鹤白丝袜与Y形鞋.txt', 'inward': '内八站姿补充.txt'}


def profile_list():
    return settings.profiles()


def decrypt_key(profile):
    saved=settings.custom_key(profile)
    if saved:return saved
    if profile not in PROFILES or profile == 'custom': raise ValueError('请填写自定义 API key。')
    p = SECRETS/(profile+'.dpapi')
    if not p.is_file(): raise ValueError('该接口没有本机已保存 key，请改用自定义 key。')
    # Fixed profile whitelist; never interpolate arbitrary shell text or print its output.
    ps = "$p=Join-Path $env:USERPROFILE '.codex\\secrets\\imagegen\\"+profile+".dpapi';$s=(Get-Content -LiteralPath $p -Raw).Trim()|ConvertTo-SecureString;$b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s);try{[Console]::Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($b))}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)}"
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
    binary=str(bundled) if bundled.is_file() else 'powershell.exe'
    child_env=dict(os.environ);child_env.pop('PSModulePath',None)
    done = subprocess.run([binary,'-NoProfile','-NonInteractive','-Command',ps],env=child_env,
                          capture_output=True, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=15)
    if done.returncode: raise ValueError('本机加密 key 无法解密，请手动填入；未输出密钥。')
    return done.stdout.decode('utf-8').strip()


def clean(value, key=''):
    if key.lower() in ('api_key','authorization','key','token','access_token'): return '[redacted]'
    if key in ('api_base','base_url') and isinstance(value,str):
        u=urllib.parse.urlsplit(value)
        return value if u.scheme in ('https','http') and not u.username and not u.password and not u.query else '[redacted URL]'
    if isinstance(value, dict): return {k:clean(v,k) for k,v in value.items()}
    if isinstance(value,list): return [clean(v) for v in value]
    return adapter.safe(value)


def dump(path, data):
    Path(path).write_text(json.dumps(clean(data),ensure_ascii=False,indent=2),'utf-8')


def redact_secret(value, secret):
    if isinstance(value,dict):return {k:redact_secret(v,secret) for k,v in value.items()}
    if isinstance(value,list):return [redact_secret(v,secret) for v in value]
    return value.replace(secret,'[redacted]') if isinstance(value,str) and secret else value


def validate(data):
    d = dict(data)
    prompt = d.get('prompt','')
    if not isinstance(prompt,str) or not prompt.strip(): raise ValueError('提示词不能为空。')
    refs = d.get('references',[])
    if not isinstance(refs,list) or len(refs)>16: raise ValueError('参考图片最多16张。')
    d['references'] = []
    for ref in refs:
        p = Path(ref).expanduser().resolve()
        if not p.is_file(): raise ValueError('参考图不存在：'+str(p))
        if p.stat().st_size>50*1024*1024: raise ValueError('单张参考图超过50MiB。')
        with Image.open(p) as im: im.verify()
        d['references'].append(str(p))
    if d.get('mode') not in ('generate','edit'): raise ValueError('请选择文生图或参考编辑。')
    if d['mode']=='edit' and not refs: raise ValueError('参考编辑至少需要一张参考图。')
    if d['mode']=='generate' and refs: raise ValueError('文生图不携带参考图，请清空参考或改用参考编辑。')
    if d.get('mask'):
        if d['mode']!='edit': raise ValueError('蒙版只适用于参考编辑。')
        mask=Path(d['mask']).resolve()
        with Image.open(mask) as im, Image.open(d['references'][0]) as first:
            if im.format!='PNG' or 'A' not in im.getbands(): raise ValueError('蒙版需为带透明通道的PNG。')
            if im.size!=first.size: raise ValueError('蒙版尺寸须与第一张参考图一致。')
            if im.getchannel('A').getextrema()[0]==255: raise ValueError('蒙版没有透明待修改区域。')
        d['mask']=str(mask)
    import codex_channel
    subscription=d.get('profile')==codex_channel.PROFILE
    if subscription and d.get('mask'):raise ValueError('Codex 订阅渠道暂不支持精确透明蒙版参数，请移除蒙版使用参考编辑，或选择支持蒙版的 API 渠道。')
    if subscription and d.get('model')!='gpt-image-2':raise ValueError('Codex 订阅渠道使用内置 gpt-image-2；代理模型由本机 Codex 配置决定。')
    base = d.get('api_base','').rstrip('/')
    url = urllib.parse.urlsplit(base)
    if not subscription and (url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment):
        raise ValueError('API base需为HTTPS地址，不能含账号、查询参数或片段。')
    d['api_base']=base
    profile=d.get('profile','sraiapi-upscale')
    stored=settings.get(profile)
    if stored is None: raise ValueError('未知凭据配置。')
    if profile!='custom' and base!=stored['base']:
        raise ValueError('已保存key只能发送到对应接口；自定义地址请使用自定义key。')
    if not d.get('model'): raise ValueError('模型不能为空。')
    d['request_protocol']='codex' if subscription else settings.request_protocol(profile,d['model'])
    if d['request_protocol']=='grok-json' and d.get('mask'):
        raise ValueError('Grok JSON接口尚未验证蒙版支持，不能发送蒙版。请移除蒙版使用参考编辑，或选择支持蒙版的GPT接口。')
    if d.get('quality') not in ('auto','low','medium','high'): raise ValueError('质量参数无效。')
    size=d.get('size','auto')
    if size!='auto':
        try: w,h=map(int,size.split('x'))
        except (ValueError,AttributeError): raise ValueError('尺寸请填 WIDTHxHEIGHT 或 auto。')
        if w<=0 or h<=0 or w%16 or h%16 or not 1/3<=w/h<=3: raise ValueError('边长需为16的倍数，宽高比在1:3至3:1之间。')
    parts=[(RULES/MODULES[k]).read_text('utf-8-sig').strip() for k in d.get('negative_modules',[]) if k in MODULES]
    custom=d.get('custom_negative','').strip()
    if custom:parts.append(custom)
    d['effective_prompt']=prompt + ('\n\nQuality constraints (things to preserve and avoid):\n'+'\n\n'.join(parts) if parts else '')
    if len(d['effective_prompt'])>32000: raise ValueError('完整请求提示词超过32000字符。')
    output=Path(d.get('output_dir') or ROOT/'手动生图程序/outputs').expanduser().resolve()
    name=d.get('filename') or '手动-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.png'
    if Path(name).name!=name or any(c in name for c in '<>:"/\\|?*') or name.rstrip('. ')!=name:
        raise ValueError('文件名不能包含目录或Windows特殊字符。')
    if not name.lower().endswith('.png'): name+='.png'
    if Path(name).stem.upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:raise ValueError('不能使用Windows保留文件名。')
    target=output/name
    if target.exists(): raise ValueError('目标已存在，请更换名称。程序不会直接覆盖原图。')
    d.update(output_dir=str(output),filename=name,target=str(target))
    return d


def image_bytes(payload, allow_conversion=False):
    choices=adapter.images(payload)
    if not choices: raise RuntimeError('响应没有可用图片数据或URL；已保存脱敏响应。')
    kind,value=choices[0]
    if kind=='b64': raw=base64.b64decode(value,validate=True)
    elif value.startswith('data:image/'): raw=base64.b64decode(value.split(',',1)[1],validate=True)
    else:
        # Artifact hosts receive no API key; reject private-network URLs and redirects there.
        class PublicRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                check_public(newurl)
                return super().redirect_request(req,fp,code,msg,headers,newurl)
        check_public(value)
        proxy=settings.active_proxy();handlers=[PublicRedirect()]
        if proxy:handlers.insert(0,urllib.request.ProxyHandler({'http':proxy,'https':proxy}))
        with urllib.request.build_opener(*handlers).open(urllib.request.Request(value,headers={'User-Agent':'LocalImageWorkbench/1.0'}),timeout=120) as res:
            raw=res.read(50*1024*1024+1)
    if len(raw)>50*1024*1024: raise RuntimeError('返回图片超过50MiB。')
    with Image.open(io.BytesIO(raw)) as im:
        if im.format!='PNG':
            if not allow_conversion or im.format not in ('JPEG','WEBP'):
                raise RuntimeError('要求PNG，但接口返回其他格式；未伪装后缀。')
            im.load();converted=io.BytesIO();im.convert('RGB').save(converted,format='PNG')
            raw=converted.getvalue()
            if len(raw)>50*1024*1024:raise RuntimeError('转换后的PNG超过50MiB。')
            return raw
        im.verify()
    return raw


def grok_payload(model,prompt,refs):
    """The third-party Grok Images endpoint accepts ordered data URLs, not multipart."""
    payload=dict(model=model,prompt=prompt)
    if refs:
        images=[]
        for path in refs:
            with Image.open(path) as image:
                mime=Image.MIME.get(image.format)
            if mime not in ('image/png','image/jpeg','image/webp'):
                raise ValueError('Grok参考图需为PNG、JPEG或WEBP；请转换后再提交。')
            images.append(dict(type='image_url',url='data:'+mime+';base64,'+base64.b64encode(Path(path).read_bytes()).decode('ascii')))
        payload['images']=images
    return payload


def check_public(url):
    u=urllib.parse.urlsplit(url)
    if u.scheme!='https' or not u.hostname or u.username or u.password: raise ValueError('返回图片URL需为公开HTTPS地址。')
    for info in socket.getaddrinfo(u.hostname,u.port or 443):
        if not ipaddress.ip_address(info[4][0]).is_global:raise ValueError('拒绝下载内网图片URL。')


def execute(data, client_factory=OpenAI, json_client_factory=None):
    d=validate(data)
    import codex_channel
    subscription=d['profile']==codex_channel.PROFILE
    grok=d['request_protocol']=='grok-json'
    supplied_key=d.pop('api_key','')
    key='' if subscription else (supplied_key or decrypt_key(d['profile']))
    # No automatic retry or fallback; each explicit submit issues one image request.
    client=None
    if not subscription:
        options=dict(api_key=key,base_url=d['api_base'],timeout=300,max_retries=0)
        proxy=settings.active_proxy()
        if not grok and proxy:options['http_client']=DefaultHttpxClient(proxy=proxy,trust_env=False,timeout=300)
        try:
            if grok:
                transport=dict(timeout=300,follow_redirects=False)
                if proxy:transport.update(proxy=proxy,trust_env=False)
                client=(json_client_factory or DefaultHttpxClient)(**transport)
            else:client=client_factory(**options)
        except Exception:
            if options.get('http_client'):options['http_client'].close()
            raise
    target=Path(d['target']);target.parent.mkdir(parents=True,exist_ok=True)
    jobdir=Path(d['job_dir']) if d.get('job_dir') else ROOT/'手动生图程序/history'/('请求-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    jobdir.mkdir(parents=True)
    promptfile=None
    if not d.get('inline_metadata'):
        promptfile=jobdir/'实际请求.prompt.txt';promptfile.write_text(d['effective_prompt'],'utf-8')
        (jobdir/'原始输入.prompt.txt').write_text(d['prompt'],'utf-8')
    assets=jobdir/'参考图快照';assets.mkdir()
    refs=[];frozen=[]
    for i,p in enumerate(d['references'],1):
        snapshot=assets/(f'{i:02d}'+Path(p).suffix.lower());shutil.copyfile(p,snapshot)
        frozen.append(str(snapshot));refs.append(dict(path=p,sha256=sha(snapshot),snapshot_path=str(snapshot)))
    mask_snapshot=None
    if d.get('mask'):
        mask_snapshot=assets/'蒙版.png';shutil.copyfile(d['mask'],mask_snapshot)
    meta=dict(model=d['model'],api_base=d['api_base'],credential_label=d['profile'],
              size_requested=d['size'],quality=d['quality'],mode=d['mode'],
              prompt_file=str(promptfile) if promptfile else None,prompt_sha256=sha(promptfile) if promptfile else hashlib.sha256(d['effective_prompt'].encode()).hexdigest(),references=refs,
              mask=dict(path=d['mask'],sha256=sha(mask_snapshot),snapshot_path=str(mask_snapshot)) if mask_snapshot else None,
              negative_modules=d.get('negative_modules',[]),effective_prompt=d['effective_prompt'],
              output_format='png',provenance=d.get('provenance'),agent=d.get('agent'),saved_path=str(target),prompt=d['effective_prompt'],modules=d.get('modules'),original_input=d['prompt'],network_proxy=settings.active_proxy() or 'environment')
    if subscription:meta.update(channel='codex-subscription',billing='ChatGPT订阅/Codex额度',codex_home=str(codex_channel.home()),size_quality_mode='prompt_preferences')
    if grok:
        endpoint=d['api_base']+('/images/edits' if d['mode']=='edit' else '/images/generations')
        meta.update(channel='grok-json',request_protocol='grok-json',endpoint=endpoint,
                    size_quality_mode='not_sent',quality=None,request_fields=['model','prompt']+(['images'] if frozen else []),
                    size_quality_note='界面尺寸与质量未作为Grok参数发送，输出以实际返回为准；未自动改写提示词。')
    dump(jobdir/'request.json',meta)
    started=time.monotonic()
    try:
        if subscription:
            raw,details=codex_channel.generate(dict(d,references=frozen,job_dir=str(jobdir)))
            meta.update(codex=details,elapsed_seconds=round(time.monotonic()-started,2))
        elif grok:
            body=grok_payload(d['model'],d['effective_prompt'],frozen)
            response=client.post(endpoint,headers={'Authorization':'Bearer '+key},json=body)
            meta.update(http_status=response.status_code,elapsed_seconds=round(time.monotonic()-started,2))
            try:payload=response.json()
            except ValueError:payload={'error':{'message':response.text[:2500]}}
            meta['response']=redact_secret(clean(payload),key)
            if response.status_code>=400 or isinstance(payload,dict) and payload.get('error'):
                raise RuntimeError('Grok生图失败（HTTP '+str(response.status_code)+'）：'+json.dumps(meta['response'],ensure_ascii=False)[:2500])
            raw=image_bytes(payload,allow_conversion=True)
            with Image.open(io.BytesIO(raw)) as im:meta['actual_size']=[im.width,im.height]
            meta['storage_format']='png'
        else:
            with ExitStack() as stack:
                args=dict(model=d['model'],prompt=d['effective_prompt'],size=d['size'],quality=d['quality'],output_format='png')
                if d['mode']=='edit':
                    args['image']=[stack.enter_context(open(p,'rb')) for p in frozen]
                    if mask_snapshot:args['mask']=stack.enter_context(open(mask_snapshot,'rb'))
                    response=client.images.with_raw_response.edit(**args)
                else:response=client.images.with_raw_response.generate(**args)
            payload=adapter.response_payload(response)
            meta.update(response=redact_secret(clean(payload),key),http_status=response.status_code,elapsed_seconds=round(time.monotonic()-started,2))
            raw=image_bytes(payload)
        # Exclusive write prevents a simultaneous request from replacing this destination.
        with target.open('xb') as f:f.write(raw)
        caption=target.with_suffix('.txt')
        if not d.get('inline_metadata') and not caption.exists():caption.write_text(d['effective_prompt'],'utf-8')
        meta.update(status='saved',sha256=sha(target),bytes=len(raw))
        dump(target.with_suffix('.response.json'),meta)
        dump(jobdir/'response.json',meta)
        return dict(status='saved',path=str(target),record=str(jobdir/'response.json'),elapsed=meta['elapsed_seconds'])
    except Exception as e:
        err=codex_channel.safe_error(e) if subscription else (clean(str(e)).replace(key,'[redacted]') if key else clean(str(e)))
        refusal=any(s in err.lower() for s in ('safety','content_policy','moderation','拒绝','安全系统','安全策略','防护限制'))
        meta.update(status='safety_rejected' if refusal else 'request_failed',error=err,
                    http_status=getattr(e,'status_code',meta.get('http_status')),
                    elapsed_seconds=round(time.monotonic()-started,2))
        # Store structured error bodies when available, never the full API response object.
        if getattr(e,'body',None):meta['error_body']=redact_secret(clean(e.body),key)
        dump(jobdir/'response.json',meta)
        return dict(status=meta['status'],error=err,record=str(jobdir/'response.json'))
    finally:
        if client:client.close()
