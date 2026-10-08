"""Project-scoped catalog, experience retrieval and exact input snapshots."""
import hashlib, json, re, shutil
from pathlib import Path
from PIL import Image
from provenance import sha
import workbench as wb
from agent.store import Store

def digest(value): return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
class Context:
    def __init__(self,root,bind=False):
        self.root=Path(root).resolve();self.store=Store(self.root)
        self.cfg=dict(gallery=str(self.root/'角色图集'),references=str(self.root/'角色特征及参考图'),poses=str(self.root/'姿势'),scenes=str(self.root/'场景'),rules=str(self.root/'质量约束nagetive'),intermediate='other/workbench',archive='{role_dir}/other/workbench/archive',replace_policy='archive')
        local=self.store.folder/'storage.json'
        if local.is_file(): self.cfg.update(json.loads(local.read_text('utf-8')))
        for k in ('gallery','references','poses','scenes','rules'):
            if not Path(self.cfg[k]).resolve().is_relative_to(self.root): raise ValueError('Agent 项目目录配置不得指向项目之外。')
        if bind:
            # Each worker process owns one immutable root; the UI's globals remain untouched.
            wb.ROOT=self.root;wb.config=lambda:dict(self.cfg)
        self.entries={};self.assets={}
    def catalog(self,kind):
        result=[]
        for x in wb.catalog('姿势' if kind=='pose' else '场景'):
            ident=kind+':'+digest([x['category'],x['name'],x['source']])[:16]
            item=dict(x,id=ident,kind=kind);self.entries[ident]=item;result.append(item)
        return result
    def entry(self,ident,kind):
        self.catalog(kind)
        if ident not in self.entries or self.entries[ident]['kind']!=kind: raise ValueError('姿势/场景 ID 不存在；请从当前目录选择。')
        return self.entries[ident]
    def asset(self,path,role,kind,**extra):
        p=Path(path).resolve()
        if not p.is_file() or not p.is_relative_to(self.root) or p.suffix.lower() not in wb.SUFFIXES: raise ValueError('图片不在当前项目或文件不存在。')
        # Managed image identity must survive pending/repair/pass folder moves.
        key=[role,extra['image_id'],extra.get('candidate_id')] if extra.get('image_id') else [str(p).lower(),role,None,None]
        ident=kind+':'+digest(key)[:20]
        item=dict(id=ident,path=str(p),name=p.name,role=role,kind=kind,**extra);self.assets[ident]=item;return item
    def refs(self,role):
        if role not in wb.roles(): raise ValueError('角色不在当前图集。')
        folder=Path(self.cfg['references'])/role
        result=[]
        for p in sorted(folder.glob('*')):
            if p.is_file() and p.suffix.lower() in wb.SUFFIXES:
                purpose='face' if any(s in p.stem for s in ('脸','面部','近景','表情')) else 'back' if '背' in p.stem else 'side' if '侧' in p.stem else 'costume'
                result.append(self.asset(p,role,'reference',purpose=purpose))
        return result
    def images(self,role=None):
        result=[]
        for d in wb.scan():
            if role and d['role']!=role: continue
            if d.get('path') and Path(d['path']).is_file(): result.append(self.asset(d['path'],d['role'],'original',image_id=d['id'],candidate_id=None,label=d['label'],reason=d.get('repair_reason','')))
            for c in d.get('candidates',[]):
                if Path(c['path']).is_file(): result.append(self.asset(c['path'],d['role'],'candidate',image_id=d['id'],candidate_id=c['id'],label=c['label'],reason=c.get('repair_reason','')))
        return result
    def get_asset(self,ident):
        if ident not in self.assets:
            self.images()
            for role in wb.roles(): self.refs(role)
        item=self.assets.get(ident)
        if not item: raise ValueError('图片 ID 不存在，请重新读取图库。')
        # Follow status moves and reject stale identity, never reinterpret a changed file.
        if item.get('image_id'):
            d=wb.load(item['role'],item['image_id']);item=dict(item,**{k:v for k,v in (d if not item.get('candidate_id') else next(c for c in d['candidates'] if c['id']==item['candidate_id'])).items() if k in ('path','label','repair_reason')})
            self.assets[ident]=item
        p=Path(item['path']).resolve()
        if not p.is_file() or not p.is_relative_to(self.root): raise ValueError('图片已移动或不属于当前项目。')
        return item
    def knowledge(self,query='',role='',limit=8):
        files=[self.root/'生图经验.md',self.root/'AGENTS.md']
        rules=Path(self.cfg['rules']);files+=sorted(rules.glob('*.txt'))+sorted(rules.glob('*.md'))
        if role: files+=sorted((Path(self.cfg['references'])/role/'other').glob('character/*'))
        result=[];terms=[s.lower() for s in re.split(r'[\s,，]+',query) if s]
        for p in files:
            if not p.is_file(): continue
            if role and (('白鹤' in p.name and '白鹤' not in role) or ('泳装小南' in p.name and role!='泳装小南')): continue
            text=p.read_text('utf-8-sig');blocks=re.split(r'(?m)(?=^#{1,3} )',text) if p.suffix=='.md' else [text]
            for n,block in enumerate(blocks):
                if not block.strip(): continue
                score=sum(1 for t in terms if t in (p.name+' '+block).lower())
                if terms and not score: continue
                result.append(dict(source=str(p),section=n,version=sha(p),score=score,text=block[:6000]))
        return sorted(result,key=lambda x:-x['score'])[:min(max(limit,1),10)]
    def constraints(self,role):
        names=['通用人体与手指.txt','腿脚结构与左右脚.txt','真人cosplay质量约束.txt']
        if '白鹤' in role: names+=['白鹤白丝袜与Y形鞋.txt']
        if role=='泳装小南': names+=['泳装小南纸翼物理方向.txt']
        folder=Path(self.cfg['rules']);sources=[]
        for p in sorted(folder.glob('*')):
            if p.is_file() and (p.name in names or ('腿脚' in p.name and '通用' in p.name) or (role=='泳装小南' and '纸翼' in p.name)):
                sources.append(dict(path=str(p),sha256=sha(p),text=p.read_text('utf-8-sig')[:6500]))
        general='Real adult cosplay photography, natural skin and believable light. Correct continuous anatomy, two arms and two legs. Five fingers on each visible hand, correct handedness and toe direction. Natural occlusion is allowed; do not invent hidden limbs. No excessive grain, plastic skin or glamour face smoothing.'
        return general+'\n'+'\n'.join(x['text'] for x in sources),sources
    def snapshot(self,task,call,request,refs):
        folder=wb.role_dir(request['role'])/'other'/'agent'/'tasks'/task/'requests'/call;folder.mkdir(parents=True,exist_ok=True)
        rows=[]
        for n,item in enumerate(refs,1):
            src=Path(item['path']);dest=folder/'references'/f'{n:02d}{src.suffix.lower()}';dest.parent.mkdir(exist_ok=True)
            before=sha(src);shutil.copy2(src,dest)
            if sha(dest)!=before: raise ValueError('参考图快照校验失败。')
            with Image.open(dest) as im: im.verify()
            rows.append(dict(item,sha256=before,snapshot_path=str(dest)))
        record=dict(request,references=rows)
        (folder/'request.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf-8')
        (folder/'最终提示词.txt').write_text(request.get('prompt',''),'utf-8')
        return record
