"""Role-scoped three-label review, linked candidates and transactional application."""
from contextlib import contextmanager
import hashlib,json,os,re,shutil,threading,time,uuid
from pathlib import Path
from PIL import Image
from paths import APP,ROOT
from provenance import sha,read_json
import api_client,quality
import status_storage

LABELS=('已通过','待审核','待返修')
SUFFIXES={'.png','.jpg','.jpeg','.webp'}
LOCK=threading.RLock()
SCAN_HASHES={}

def atomic(path,data):quality.atomic(path,api_client.clean(data))
def now():return time.strftime('%Y-%m-%d %H:%M:%S')
def defaults():return dict(gallery=str(ROOT/'角色图集'),references=str(ROOT/'角色特征及参考图'),poses=str(ROOT/'姿势'),scenes=str(ROOT/'场景'),rules=str(ROOT/'质量约束nagetive'),intermediate='other/workbench',archive='{role_dir}/other/workbench/archive',replace_policy='archive')
def config():return defaults()|(read_json(APP/'config/storage.json',{}) or {})
def save_config(d):
    value=defaults()|d
    for k in ('gallery','references','poses','scenes','rules'):
        p=Path(value[k]).expanduser().resolve()
        if not p.is_dir():raise ValueError('目录不存在：'+str(p))
        value[k]=str(p)
    part=Path(value['intermediate'])
    if part.is_absolute() or not part.parts or part.parts[0]!='other' or '..' in part.parts:raise ValueError('角色中间文件必须保存在other下，例如other/workbench。')
    if value['replace_policy'] not in ('archive','overwrite'):raise ValueError('原图处理方式无效。')
    if not value['archive'].strip():raise ValueError('请设置旧图存档目录。')
    previous=config();value['intermediate_history']=list(dict.fromkeys(previous.get('intermediate_history',[])+[previous['intermediate']]))
    atomic(APP/'config/storage.json',value);return value
def gallery():return Path(config()['gallery'])
def roles():return sorted(p.name for p in gallery().iterdir() if p.is_dir() and p.name!='other' and not p.name.lower().startswith('temp'))
def formal_images(folder):
    folder=Path(folder)
    files=[]
    for current,dirs,names in os.walk(folder):
        dirs[:]=[d for d in dirs if d!='other' and not d.lower().startswith('temp')]
        files.extend(Path(current)/n for n in names if Path(n).suffix.lower() in SUFFIXES)
    return sorted(files)

def scan_sha(path):
    """Reuse unchanged scan digests only; review/application always hash actual bytes."""
    p=Path(path);s=p.stat();key=str(p.resolve()).lower();signature=(s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_ino)
    cached=SCAN_HASHES.get(key)
    if cached and cached[0]==signature:return cached[1]
    digest=sha(p)
    if len(SCAN_HASHES)>4096:SCAN_HASHES.clear()
    SCAN_HASHES[key]=(signature,digest);return digest
def semantic_name(pose_class,pose_name,role,scene_class,scene_name,sequence,suffix='.png'):
    parts=[pose_class,pose_name,role,scene_class,scene_name,str(sequence)]
    if any(not str(s).strip() or re.search(r'[<>:"/\\|?*]',str(s)) for s in parts):raise ValueError('命名字段为空或包含非法字符。')
    return '_'.join(str(s).strip().replace('_','-') for s in parts)+suffix
def filename_fields(path,role):
    parts=Path(path).stem.split('_')
    if len(parts)!=6:return {}
    if parts[2]==role:return dict(pose_class=parts[0],pose_name=parts[1],scene_class=parts[3],scene_name=parts[4],sequence=parts[5])
    return dict(pose_class=parts[0],pose_name=parts[3],scene_class=parts[1],scene_name=parts[4],sequence=parts[5])
def role_dir(role):
    if role not in roles():raise ValueError('请选择已有角色。')
    return gallery()/role
def base(role):
    target=(role_dir(role)/config()['intermediate']).resolve()
    if not target.is_relative_to((role_dir(role)/'other').resolve()):raise ValueError('中间文件路径越界。')
    return target
def metadata_dirs(role):
    cfg=config();parts=list(dict.fromkeys([cfg['intermediate']]+cfg.get('intermediate_history',[])+['other/workbench']))
    return [role_dir(role)/p/'images' for p in parts if not Path(p).is_absolute() and '..' not in Path(p).parts and Path(p).parts[0]=='other']
@contextmanager
def transaction():
    import msvcrt
    folder=APP/'runtime';folder.mkdir(parents=True,exist_ok=True)
    with LOCK,(folder/'workbench.lock').open('a+b') as f:
        f.seek(0)
        if not f.read(1):f.write(b'0');f.flush()
        f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
        try:yield
        finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
def statefile(role,i):
    if not re.fullmatch('[a-f0-9]{32}',i):raise ValueError('图片ID无效。')
    return next((p/i/'metadata.json' for p in metadata_dirs(role) if (p/i/'metadata.json').is_file()),base(role)/'images'/i/'metadata.json')
def load(role,i):
    d=read_json(statefile(role,i))
    if not d:raise ValueError('未找到图片记录。')
    return d
def store(d):atomic(statefile(d['role'],d['id']),d)
def relocate_record(d,candidate=None):
    change=status_storage.relocate(d,role_dir(d['role']),atomic,candidate)
    if change:
        d['events'].append(dict(event='status_storage_move',candidate_id=candidate['id'] if candidate else None,at=now(),**change))
        if candidate is None:
            items=read_json(manifest(),[]) or []
            for row in items:
                if row.get('image')==change['old']:row.update(image=change['new'],gallery_path=d['gallery_path'])
            atomic(manifest(),items)
        aliases=read_json(APP/'config/path-aliases.json',{}) or {};aliases[change['old'].lower()]=change['new'];atomic(APP/'config/path-aliases.json',aliases)
    return change
def manifest():return gallery()/'other/manifests/当前图集清单.json'
def scan():
    records=read_json(manifest(),[]) or [];lookup={str(Path(x['image']).resolve()).lower():x for x in records if x.get('image')}
    result=[]
    with transaction():
        for role in roles():
            files=[p for folder in metadata_dirs(role) for p in folder.glob('*/metadata.json')]
            known={str(Path(d['path']).resolve()).lower():d for p in files if (d:=read_json(p)) and d.get('path')}
            for p in formal_images(role_dir(role)):
                key=str(p.resolve()).lower();row=lookup.get(key,{})
                d=known.get(key)
                digest=scan_sha(p)
                if not d:
                    ident=uuid.uuid5(uuid.NAMESPACE_URL,str(role_dir(role).resolve())+'::'+str(row.get('id') or p.relative_to(role_dir(role)))).hex
                    label=row['quality_label'] if row.get('quality_label') in LABELS else '待返修' if row.get('anatomy_review_status')=='needs_prompt_revision' else '已通过' if row.get('review_notes') or row.get('anatomy_review_status')=='passed' else '待审核'
                    d=dict(id=ident,role=role,path=str(p.resolve()),sha256=digest,label=label,kind='original',candidates=[],events=[],generation=None,created_at=now(),repair_reason=row.get('review_notes','') if label=='待返修' else '')
                    if label!='已通过':relocate_record(d)
                    store(d)
                elif d['sha256']!=digest:
                    d.update(sha256=digest,label='待审核',review=None);d['events'].append(dict(event='external_change',at=now()));relocate_record(d);store(d)
                elif d['label']!='已通过':relocate_record(d);store(d)
                result.append(d)
            for p in files:
                d=read_json(p)
                if not d or d['id'] in {x['id'] for x in result}:continue
                managed=d.get('gallery_path') and d.get('path') and Path(d['path']).is_file()
                if managed:
                    digest=scan_sha(d['path'])
                    if digest!=d['sha256']:
                        d.update(sha256=digest,label='待审核',review=None);d['events'].append(dict(event='external_change',at=now()));relocate_record(d);store(d)
                    result.append(d)
                elif d.get('candidates') and (not d.get('path') or not Path(d['path']).is_file()):result.append(d)
    return result
def create_new(role):
    with transaction():
        d=dict(id=uuid.uuid4().hex,role=role,path=None,sha256=None,label='待审核',kind='new',candidates=[],events=[],generation=None,created_at=now(),repair_reason='')
        store(d);return d
def add_candidate(role,i,path,record=None):
    with transaction():
        d=load(role,i);p=Path(path).resolve();folder=statefile(role,i).parent
        if not p.is_relative_to(folder):
            # Composited candidates start in the review folder. Carry their
            # actual request evidence when importing back into managed storage.
            record=record or read_json(p.with_suffix('.response.json'))
            cid=uuid.uuid4().hex;dest=folder/'candidates'/cid/'image.png';dest.parent.mkdir(parents=True)
            with Image.open(p) as im:im.convert('RGB').save(dest,'PNG')
            p=dest
            if record:atomic(p.with_suffix('.response.json'),dict(record,saved_path=str(p),sha256=sha(p)))
        cid=uuid.uuid4().hex
        c=dict(id=cid,path=str(p),sha256=sha(p),label='待审核',created_at=now(),repair_reason='',review=None)
        d['candidates'].append(c);relocate_record(d,c);store(d);return c
def review(role,i,cid,decision,notes,reviewer='manual'):
    if decision not in LABELS:raise ValueError('只能使用已通过、待审核、待返修。')
    if decision=='待返修' and len(notes.strip())<3:raise ValueError('必须填写具体返修原因。')
    with transaction():
        d=load(role,i)
        if cid is None:
            if decision!='待返修' and reviewer!='codex':raise ValueError('手动操作中已有图库图片只能重新标记为待返修。')
            target=d
        else:target=next(x for x in d['candidates'] if x['id']==cid)
        with Image.open(target['path']) as im:im.verify()
        if len(notes.strip())<3:raise ValueError('请写明实际检查结论。')
        digest=sha(target['path'])
        target.update(label=decision,sha256=digest,repair_reason=notes.strip() if decision=='待返修' else '',review=dict(reviewer=reviewer,notes=notes.strip(),image_sha256=digest,at=now()))
        relocate_record(d,target if cid else None)
        d['events'].append(dict(event='review',candidate_id=cid,label=decision,notes=notes,reviewer=reviewer,at=now()));store(d)
        if cid is None:
            items=read_json(manifest(),[]) or []
            for row in items:
                if row.get('image')==target['path']:row.update(anatomy_review_status={'已通过':'passed','待审核':'uncertain','待返修':'needs_prompt_revision'}[decision],review_notes=notes.strip(),quality_label=decision,quality_review=target['review'])
            atomic(manifest(),items)
        return target

def repair_entries(records=None):
    entries=[]
    for d in records if records is not None else scan():
        if d['label']=='待返修' and d.get('path') and Path(d['path']).is_file():entries.append(dict(record=d,candidate_id=None,path=d['path'],reason=d.get('repair_reason','')))
        for c in d.get('candidates',[]):
            if c['label']=='待返修' and Path(c['path']).is_file():entries.append(dict(record=d,candidate_id=c['id'],path=c['path'],reason=c.get('repair_reason','')))
    return entries
def catalog(kind):
    folder=Path(config()['poses' if kind=='姿势' else 'scenes']);result=[]
    for p in folder.glob('*.txt'):
        text=p.read_text('utf-8-sig');category=p.stem.split('姿势_')[0].split('_')[0] if kind=='姿势' else re.sub(r'_\d+条$','',p.stem)
        for b in re.split(r'\n(?=\[[A-Z]\d+\]|\d+[.、]\s)',text):
            name=re.search(r'^(?:姿势名称|场景名称)：(.+)$',b,re.M)
            if not name:name=re.search(r'^\d+[.、]\s*(.+)$',b,re.M)
            if not name:continue
            def field(label):
                m=re.search(r'^'+label+r'：([^\n]*)',b,re.M);return m.group(1).strip() if m else ''
            result.append(dict(name=name.group(1).strip(),category=category,text=field('通用英文') or field('英文提示词参考') or field(kind),description=field(kind),reference=field('参考图片'),source=str(p)))
    return result
def character(role):
    folder=Path(config()['references'])/role
    for f in ('角色特征.txt','参考特征.txt'):
        path=next((p for p in [folder/'other/character'/f,folder/f,folder/'other/history/legacy-top-level'/f] if p.is_file()),None)
        if path:
            text=path.read_text('utf-8-sig')
            if '本组固定特征：' in text:text=text.split('本组固定特征：',1)[1].split('默认原始参考图：')[0]
            return text.strip()
    text=(Path(config()['references'])/'总特征提示词.txt').read_text('utf-8-sig')
    m=re.search('【'+re.escape(role)+r'】\s*([\s\S]*?)(?=\n【|\n可复用请求模板|\Z)',text)
    return m.group(1).strip() if m else role+'，人物身份、衣装和材质以同组参考图为准。'
def reference_paths(role):
    folder=Path(config()['references'])/role
    files=[p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES] if folder.is_dir() else []
    files.sort(key=lambda p:(not p.name.startswith('精选参考'),p.name));return [str(p) for p in files[:3]]
def compose(modules):
    if modules.get('legacy'):
        text=modules['legacy'].strip()
    else:
        if not modules.get('character','').strip():raise ValueError('请填写角色描述。')
        text='\n\n'.join(title+'\n'+modules[k].strip() for title,k in [('Character identity and costume / 角色描述','character'),('Pose / 姿势','pose'),('Expression / 表情','expression'),('Scene and lighting / 场景','scene')] if modules.get(k,'').strip())
    extras=[]
    if modules.get('reference_notes','').strip():extras.append('Reference image roles / 参考图用途\n'+modules['reference_notes'].strip())
    if modules.get('repair','').strip():extras.append('Repair request / 返修要求\nPreserve the identity, costume, intended pose and scene; correct the stated visible defects.\n'+modules['repair'].strip())
    if modules.get('constraints','').strip():extras.append('Quality constraints / 质量约束\n'+modules['constraints'].strip())
    text+=''.join('\n\n'+x for x in extras)
    if not text.strip() or len(text)>32000:raise ValueError('最终提示词为空或超过32000字符。')
    return text
def prepare(role,i,modules,refs,profile,model,size,quality_value,mask=''):
    d=load(role,i);folder=statefile(role,i).parent/'candidates'/uuid.uuid4().hex
    return dict(profile=profile,api_base=__import__('settings').get(profile)['base'],model=model,size=size,quality=quality_value,prompt=compose(modules),modules=modules,references=refs,negative_modules=[],mode='edit' if refs else 'generate',mask=mask,output_dir=str(folder),filename='image.png',job_dir=str(folder/'request'),inline_metadata=True,provenance=dict(role=role,image_id=i,original_path=d.get('path'),original_sha256=d.get('sha256')))
def generate(data,client_factory=None):
    try:r=api_client.execute(data,**({'client_factory':client_factory} if client_factory else {}))
    except Exception as e:r=dict(status='request_failed',error=api_client.clean(str(e)))
    origin=data['provenance']
    if r['status']!='saved':
        with transaction():
            d=load(origin['role'],origin['image_id']);failure=dict(status=r['status'],error=r.get('error','未知错误'),record=r.get('record'),at=now())
            d.setdefault('failures',[]).append(failure);d['last_failure']=failure;d['events'].append(dict(event='generation_failed',**failure));store(d)
        return r
    if r['status']=='saved':
        r['candidate']=add_candidate(origin['role'],origin['image_id'],r['path']);r['path']=r['candidate']['path']
        if data.get('preserve_outside') and data.get('mask'):
            from masking import keep_outside
            try:
                meta=read_json(Path(r['path']).with_suffix('.response.json'));original=meta['references'][0]['snapshot_path'];mask=meta['mask']['snapshot_path']
                target=statefile(origin['role'],origin['image_id']).parent/'candidates'/uuid.uuid4().hex/'image-composite.png';target.parent.mkdir(parents=True,exist_ok=False);keep_outside(original,r['path'],mask,target)
                meta.update(saved_path=str(target),sha256=sha(target),postprocessing=dict(operation='mask_alpha_composite',raw_output=r['path'],original=original,mask=mask));atomic(target.with_suffix('.response.json'),meta);r['raw_candidate']=r['candidate'];r['candidate']=add_candidate(origin['role'],origin['image_id'],target);r['path']=r['candidate']['path']
            except Exception as e:r['composite_warning']=str(e)
    return r

def apply_candidate(role,i,cid,name=None,policy=None):
    """Journal + original byte backup allow rollback without losing an existing image."""
    with transaction():
        d=load(role,i);c=next(x for x in d['candidates'] if x['id']==cid);source=Path(c['path'])
        if c['label']!='已通过' or not c.get('review') or c['review']['image_sha256']!=sha(source):raise ValueError('候选须实际审核通过，且审核后图片内容未改变。')
        with Image.open(source) as im:
            if im.format!='PNG':raise ValueError('候选必须为PNG。')
        old=Path(d['path']) if d.get('path') else None
        if old:
            if not old.is_file() or sha(old)!=d['sha256']:raise ValueError('原图在返修期间改变，请重新检查。')
            if old.suffix.lower()!='.png':raise ValueError('原图不是PNG，请另存新PNG；不可伪装格式覆盖。')
            target=Path(d.get('gallery_path') or old)
            if target!=old and target.exists():raise ValueError('正式图库位置已被其他图片占用。')
        else:
            if not name or Path(name).name!=name or any(ch in name for ch in '<>:"/\\|?*') or not name.endswith('.png'):raise ValueError('请填写有效的PNG入库名称。')
            target=role_dir(role)/name
            if target.exists():raise ValueError('目标名称已存在，请换一个序号。')
        mf=manifest();items=read_json(mf,[]) or [];mfhash=sha(mf) if mf.exists() else None
        folder=statefile(role,i).parent/'applications'/uuid.uuid4().hex;folder.mkdir(parents=True)
        atomic(folder/'state-before.json',d);atomic(folder/'manifest-before.json',items)
        backup=folder/'original.png'
        if old:shutil.copy2(old,backup)
        policy=policy or config()['replace_policy']
        if policy not in ('archive','overwrite'):raise ValueError('原图处理方式无效。')
        archived=None
        if old and policy=='archive':
            dest=Path(config()['archive'].format(role_dir=str(role_dir(role)),role=role)).expanduser().resolve()
            if dest==role_dir(role) or dest==gallery():raise ValueError('旧图存档不能放在正式图库顶层。')
            dest.mkdir(parents=True,exist_ok=True);archived=dest/(old.stem+'-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+old.suffix)
            with backup.open('rb') as a,archived.open('xb') as b:shutil.copyfileobj(a,b)
        atomic(folder/'journal.json',dict(status='prepared',target=str(target),backup=str(backup) if old else None,candidate=str(source),archive=str(archived) if archived else None))
        temp=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.tmp');shutil.copyfile(source,temp)
        try:
            if sha(temp)!=c['sha256']:raise ValueError('候选在复制期间改变。')
            if mfhash is not None and sha(mf)!=mfhash:raise ValueError('图集清单被外部修改，请刷新。')
            if old and sha(old)!=d['sha256']:raise ValueError('原图在应用期间改变。')
            if (not old or target!=old) and target.exists():raise ValueError('目标已被占用。')
            os.replace(temp,target)
            generation=read_json(source.with_suffix('.response.json'),{}) or {};generation.update(saved_path=str(target),sha256=sha(target))
            new=json.loads(json.dumps(d));new.update(path=str(target),gallery_path=str(target),sha256=sha(target),label='已通过',kind='original',generation=generation,repair_reason='',review=c['review'],current_candidate=cid)
            new['events'].append(dict(event='applied',candidate_id=cid,policy=policy,archive=str(archived) if archived else None,at=now()))
            record=next((x for x in items if x.get('image') in (str(target),str(old))),None)
            if record is None:record=dict(id='wb-'+i,group_name=role,image=str(target));items.append(record)
            record.update(image=str(target),gallery_path=str(target),sha256=new['sha256'],anatomy_review_status='passed',quality_label='已通过',quality_review=c['review'],review_notes=c['review']['notes'],generation_record=str(statefile(role,i)),model=generation.get('model'),api_base=generation.get('api_base'),reference_images=[r.get('path') for r in generation.get('references',[])],caption_file=None,prompt=generation.get('effective_prompt') or generation.get('prompt'),prompt_file=None,prompt_file_sha256=generation.get('prompt_sha256'))
            atomic(mf,items);store(new)
            atomic(folder/'journal.json',dict(status='committed',target=str(target),archive=str(archived) if archived else None,candidate_id=cid))
        except Exception:
            if old and backup.is_file():
                if target==old:shutil.copyfile(backup,target)
                elif target.is_file() and sha(target)==c['sha256']:target.unlink()
            elif target.is_file() and sha(target)==c['sha256']:target.unlink()
            atomic(mf,read_json(folder/'manifest-before.json'));store(read_json(folder/'state-before.json'))
            atomic(folder/'journal.json',dict(status='rolled_back',target=str(target)));raise
        finally:
            if temp.exists():temp.unlink()
        # Direct overwrite keeps no old image; archive mode retains configured copy.
        if backup.exists():backup.unlink()
        if old and target!=old:
            old.unlink()
            aliases=read_json(APP/'config/path-aliases.json',{}) or {};aliases[str(old.resolve()).lower()]=str(target.resolve());atomic(APP/'config/path-aliases.json',aliases)
        return dict(target=str(target),archive=str(archived) if archived else None,record=str(statefile(role,i)))

def repair_prompt(role,i,cid=None):
    d=load(role,i);item=d if cid is None else next(c for c in d['candidates'] if c['id']==cid)
    reason=item.get('repair_reason') or d.get('repair_reason')
    if not reason:raise ValueError('请先填写返修原因。')
    parts=['请对附图进行局部返修，保留原人物身份、面部、发型、衣装、姿势、场景、光线及构图。只修复下列明确问题，不增加或减少肢体，不改变无问题区域。','返修原因与目标：'+reason]
    if role=='漂泊小南白鹤':parts.append((Path(config()['rules'])/'白鹤白丝袜与Y形鞋.txt').read_text('utf-8-sig'))
    return '\n\n'.join(parts)

def migrate_status_storage():
    changes=[]
    for d in scan():
        with transaction():
            for c in d['candidates']:
                change=relocate_record(d,c)
                if change:changes.append(change)
            if d.get('path') and Path(d['path']).is_file():
                change=relocate_record(d)
                if change:changes.append(change)
            store(d)
    for role in roles():status_storage.directories(role_dir(role))
    return changes

def migrate_captions():
    """Reversible move of only top-level companion TXT/response JSON, never images."""
    with transaction():
        items=read_json(manifest(),[]) or [];moved=[]
        for role in roles():
            for p in role_dir(role).iterdir():
                if not p.is_file() or not (p.suffix.lower()=='.txt' or p.name.endswith('.response.json')):continue
                dest=role_dir(role)/'other/history/legacy-top-level'/p.name;dest.parent.mkdir(parents=True,exist_ok=True)
                if dest.exists():
                    if sha(dest)==sha(p):dest=dest.with_name(dest.stem+'-'+uuid.uuid4().hex[:6]+dest.suffix)
                    else:dest=dest.with_name(dest.stem+'-'+uuid.uuid4().hex[:6]+dest.suffix)
                shutil.move(str(p),str(dest));moved.append(dict(old=str(p),new=str(dest),sha256=sha(dest)))
                for row in items:
                    for key in ('caption_file','prompt_file'):
                        if row.get(key)==str(p):row[key]=str(dest)
        if moved:
            atomic(APP/'other/reports'/('顶层资料迁移-'+time.strftime('%Y%m%d-%H%M%S')+'.json'),moved);atomic(manifest(),items)
        return moved

def inspection_pack(role,i,cid=None):
    d=load(role,i);c=d if cid is None else next(x for x in d['candidates'] if x['id']==cid)
    folder=statefile(role,i).parent/'inspection'/(cid or 'original');folder.mkdir(parents=True,exist_ok=True)
    with Image.open(c['path']) as im:
        im=im.convert('RGB');w,h=im.size
        for name,box in [('全图',(0,0,w,h)),('上半部',(0,0,w,int(.62*h))),('下半部',(0,int(.38*h),w,h)),('左侧',(0,0,int(.62*w),h)),('右侧',(int(.38*w),0,w,h))]:im.crop(box).save(folder/(name+'.png'))
    atomic(folder/'检查信息.json',dict(role=role,image_id=i,candidate_id=cid,image=c['path'],sha256=sha(c['path']),label=c['label'],anatomy='requires_visual_review',checks=list(quality.CHECKS)))
    return str(folder)
