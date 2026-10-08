"""Local Codex subscription bridge. Credentials remain owned by Codex."""
import base64,io,json,os,queue,re,shutil,subprocess,threading,time
from pathlib import Path
from PIL import Image

PROFILE='codex-subscription'
BASE='codex://local'

def home():
    return Path(os.environ.get('CODEX_HOME') or Path(os.environ.get('USERPROFILE') or Path.home())/'.codex').expanduser().resolve()

def command():
    found=shutil.which('codex.exe')
    if found:return [found]
    npm=Path(os.environ.get('APPDATA') or Path.home()/'AppData/Roaming')/'npm'
    binaries=list((npm/'node_modules/@openai/codex').glob('**/bin/codex.exe'))
    if binaries:return [str(binaries[0])]
    js=npm/'node_modules/@openai/codex/bin/codex.js';node=shutil.which('node.exe') or shutil.which('node')
    if node and js.is_file():return [node,str(js)]
    raise ValueError('未找到本机 Codex CLI，请先安装 Codex CLI 并使用 ChatGPT 登录。')

def profile():
    try:command();available=True
    except ValueError:available=False
    return dict(id=PROFILE,name='Codex · 订阅登录',base=BASE,model='gpt-image-2',mask_support='unsupported',channel='codex',available=available)

def safe_error(value):
    text=str(value)
    text=re.sub(r'(?i)Bearer\s+\S+','Bearer [redacted]',text)
    text=re.sub(r'\b(?:sk-[A-Za-z0-9_-]+|eyJ[A-Za-z0-9_.-]{30,})','[redacted]',text)
    return text[:3000]

class Server:
    def __init__(self):
        import settings
        env=settings.network_env()
        # Subscription channel must not silently fall back to an API credential.
        for key in ('OPENAI_API_KEY','CODEX_API_KEY','CODEX_ACCESS_TOKEN','OPENAI_BASE_URL'):env.pop(key,None)
        self.proc=subprocess.Popen(command()+['app-server','--listen','stdio://','-c','model_provider="openai"','-c','forced_login_method="chatgpt"','-c','features.image_generation=true'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.inbox=queue.Queue();self.sequence=0;self.events=[]
        def read():
            try:
                for line in self.proc.stdout:
                    try:self.inbox.put(json.loads(line))
                    except (ValueError,TypeError):continue
            finally:self.inbox.put(None)
        self.reader=threading.Thread(target=read,daemon=True);self.reader.start()
        try:self.call('initialize',dict(clientInfo=dict(name='character_image_workbench',title='角色生图工作台',version='1.1'),capabilities=dict(experimentalApi=True)));self.send(dict(method='initialized'))
        except Exception:self.close();raise
    def send(self,value):
        self.proc.stdin.write(json.dumps(value,ensure_ascii=False)+'\n');self.proc.stdin.flush()
    def receive(self,deadline):
        try:value=self.inbox.get(timeout=max(.01,deadline-time.monotonic()))
        except queue.Empty:raise TimeoutError('Codex 请求超时；不会自动重试或切换到付费 API。')
        if value is None:raise RuntimeError('本机 Codex 进程已退出，请检查登录状态或更新 Codex CLI。')
        if 'method' in value and 'id' in value:
            # No unattended approval of arbitrary agent commands or external tools.
            method=value['method']
            if method.endswith('requestApproval'):self.send(dict(id=value['id'],result=dict(decision='decline')))
            else:self.send(dict(id=value['id'],error=dict(code=-32601,message='此生图渠道仅支持内置图片工具，不执行交互式外部工具。')))
        return value
    def call(self,method,params,timeout=30):
        self.sequence+=1;ident=self.sequence;self.send(dict(id=ident,method=method,params=params));deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=self.receive(deadline)
            if value.get('id')==ident and 'method' not in value:
                if 'error' in value:raise RuntimeError(safe_error(value['error'].get('message',value['error'])))
                return value.get('result',{})
            self.events.append(value)
        raise TimeoutError('Codex 本地通信超时。')
    def close(self):
        if getattr(self,'proc',None):
            if self.proc.poll() is None:
                self.proc.terminate()
                try:self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=5)
            for stream in (self.proc.stdin,self.proc.stdout):
                if stream:stream.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def account(server,include_identity=False):
    result=server.call('account/read',dict(refreshToken=False));a=result.get('account') or {}
    if a.get('type')!='chatgpt':raise ValueError('Codex 当前未使用 ChatGPT 订阅登录，请运行 codex login 完成登录后重试。')
    info=dict(auth_mode='chatgpt',plan=a.get('planType','unknown'))
    if include_identity:
        # account/read exposes an email, not a ChatGPT profile nickname.
        email=a.get('email')
        info.update(account_name=email.strip() if isinstance(email,str) and email.strip() else '账号未提供邮箱',account_name_source='email' if isinstance(email,str) and email.strip() else 'unavailable')
    return info

def probe():
    with Server() as server:
        result=account(server,include_identity=True)
        return dict(**result,codex_home=str(home()),config_exists=(home()/'config.toml').is_file(),auth_file_exists=(home()/'auth.json').is_file(),credential_storage='由本机 Codex 管理（文件或系统凭据库）')

def generation_input(d):
    text=('Use the built-in image_gen tool exactly once to generate or edit ONE PNG image using the request below. '
          'Do not use shell, scripts, API keys, external services, or create additional variants. '
          'Use attached images in their original order as visual references. The first image is the edit target when editing. '
          'Return the tool-produced image; do not substitute or copy a reference image. '
          'Requested size and quality are preferences, not guaranteed API parameters: '+d['size']+', '+d['quality']+'.\n\n'+d['effective_prompt'])
    return [dict(type='text',text=text)]+[dict(type='localImage',path=p) for p in d['references']]

def output_bytes(item,started,refs):
    if item.get('failure') or item.get('error') or item.get('status') in ('failed','error','rejected'):raise RuntimeError('Codex 内置生图失败：'+safe_error(item.get('failure') or item.get('error') or item.get('status')))
    saved=item.get('savedPath')
    if saved:
        p=Path(saved).resolve()
        if p in {Path(x).resolve() for x in refs} or not p.is_relative_to(home()):raise RuntimeError('Codex 返回路径不属于本次内置生图产物，未导入。')
        if not p.is_file() or p.stat().st_mtime<started-3:raise RuntimeError('Codex 返回的图片不存在或不是本次生成，未导入。')
        if p.stat().st_size>50*1024*1024:raise RuntimeError('Codex 返回图片超过50MiB。')
        raw=p.read_bytes()
    else:
        data=item.get('result','')
        if data.startswith('data:image/'):data=data.split(',',1)[1]
        if len(data)>70*1024*1024:raise RuntimeError('Codex 图片结果过大。')
        try:raw=base64.b64decode(data,validate=True)
        except ValueError:raise RuntimeError('Codex 没有返回可用图片文件或图片数据。')
    with Image.open(io.BytesIO(raw)) as im:
        if im.format!='PNG':raise RuntimeError('Codex 返回的不是PNG，未伪装扩展名导入。')
        im.verify()
    return raw

def generate(d):
    started=time.time();items=[];agent=[]
    trace=Path(d['job_dir'])/'codex-events.jsonl';trace.parent.mkdir(parents=True,exist_ok=True)
    def record(event,**data):
        # Deliberately exclude auth responses, image bytes and request headers.
        row=dict(at=time.strftime('%Y-%m-%d %H:%M:%S'),event=event,**data)
        with trace.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    import settings
    record('start',codex_home=str(home()),proxy=settings.active_proxy() or 'environment')
    with Server() as server:
        auth=account(server)
        record('account_verified',**auth)
        thread=server.call('thread/start',dict(cwd=str(Path(d['job_dir']).resolve()),modelProvider='openai',sandbox='read-only',approvalPolicy='never',ephemeral=True,developerInstructions='This is an image generation client. Use only the built-in image_gen tool. No command execution, external APIs, agents, or unrelated file changes.'))
        tid=thread['thread']['id']
        record('thread_started',thread_id=tid)
        turn=server.call('turn/start',dict(threadId=tid,input=generation_input(d)))
        turn_id=turn['turn']['id'];deadline=time.monotonic()+600
        record('turn_started',thread_id=tid,turn_id=turn_id,reference_count=len(d['references']))
        while time.monotonic()<deadline:
            value=server.events.pop(0) if server.events else server.receive(deadline)
            params=value.get('params',{});method=value.get('method','')
            if params.get('threadId') not in (None,tid):continue
            if method=='error':record('server_error',error=safe_error(params.get('error',{})),will_retry=params.get('willRetry'))
            if method=='item/completed':
                item=params.get('item',{})
                if item.get('type')=='imageGeneration':
                    items.append(item)
                    result=item.get('result') or ''
                    # Some versions return a plain error in result rather than failure.
                    error_text=result if item.get('status')!='completed' and len(result)<2000 and not re.fullmatch(r'[A-Za-z0-9+/=\s]+',result) else ''
                    record('image_completed',status=item.get('status'),failure=safe_error(item.get('failure') or item.get('error') or ''),result_length=len(result),result_error=safe_error(error_text),saved_path=item.get('savedPath'))
                elif item.get('type')=='agentMessage':
                    agent.append(item.get('text',''));record('agent_message',text=safe_error(item.get('text','')))
            if method=='turn/completed' and params.get('turn',{}).get('id')==turn_id:
                completed=params['turn']
                record('turn_completed',status=completed.get('status'),error=safe_error(completed.get('error') or ''))
                if completed.get('status')!='completed':raise RuntimeError('Codex 任务未完成：'+safe_error(completed.get('error') or completed.get('status')))
                break
        else:
            server.send(dict(id=99999,method='turn/interrupt',params=dict(threadId=tid,turnId=turn_id)));raise TimeoutError('Codex 生图超过10分钟，已请求中断；不会自动重试。')
        if not items:raise RuntimeError('Codex 未返回内置生图产物。'+safe_error('\n'.join(agent))+' 可检查当前账号额度与生图工具可用性。')
        if len(items)!=1:raise RuntimeError('Codex 返回多张产物，与本次单张请求不符，未自动挑选。')
        try:raw=output_bytes(items[0],started,d['references'])
        except Exception as error:
            detail=safe_error('\n'.join(agent)).strip()
            record('output_rejected',error=safe_error(error),agent_explanation=detail)
            raise RuntimeError(safe_error(str(error)+('\nCodex说明：'+detail if detail else '\n工具未提供更具体的失败原因。'))) from error
        return raw,dict(**auth,thread_id=tid,turn_id=turn_id,image_tool_status=items[0].get('status'),submitted_prompt=generation_input(d)[0]['text'],revised_prompt=items[0].get('revisedPrompt'),saved_path=items[0].get('savedPath'),size_quality_mode='prompt_preferences')
