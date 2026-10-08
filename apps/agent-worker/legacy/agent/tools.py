"""Whitelisted Agent operations. The model cannot supply keys, shell or destinations."""
import json, re, uuid
from pathlib import Path
from PIL import Image
import settings, workbench as wb, quality
from provenance import sha
from agent.context import digest
from agent.provider import Provider,image_content
from agent import repair

CHECKS=('identity','costume','hands','legs','feet','material','wings','pose','scene')
COMPARISON=('defect_fixed','no_new_defects','identity_preserved','costume_preserved','pose_preserved')
def schema(name,description,properties,required=()):
    return {'type':'function','function':{'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}}
S={'type':'string'};I={'type':'integer','minimum':0,'maximum':1000};LIST={'type':'array','items':S,'maxItems':6}
TOOLS=[
    schema('project_overview','Read roles, task limits and project rules. No network.',{}),
    schema('search_catalog','Find modular poses or scenes. Return stable IDs; no scene words added to poses.',{'kind':{'type':'string','enum':['pose','scene']},'query':S,'offset':I},['kind']),
    schema('read_character','Read character description and available identity/costume reference images.',{'role':S},['role']),
    schema('search_knowledge','Retrieve sourced experience sections and quality constraints.',{'query':S,'role':S},[]),
    schema('list_images','List original and linked candidate image IDs and labels for a role.',{'role':S,'offset':I,'label':S},['role']),
    schema('save_plan','Save exact generation/repair plan before execution; items must use real IDs.',{'summary':S,'items':{'type':'array','maxItems':50,'items':{'type':'object','properties':{'role':S,'pose_id':S,'scene_id':S,'reference_ids':LIST,'target_asset':S,'notes':S},'required':['role','pose_id','scene_id','reference_ids'],'additionalProperties':False}}},['summary','items']),
    schema('generate_image','Generate one planned image or associated repair candidate. For local repair pass an approved mask_id and keep target as first reference. Execution only; never auto applies.',{'plan_index':I,'expression':S,'extra':S,'mask_id':S},['plan_index']),
    schema('inspect_image','Decode and crop an image. Technical inspection does not judge anatomy.',{'asset_id':S},['asset_id']),
    schema('review_image','Send actual photo, crops and identity/material references to configured vision model. Evidence-backed verdict.',{'asset_id':S,'notes':S},['asset_id']),
    schema('create_repair_mask','Create a local repair mask from confident bounding boxes in the latest actual failing visual review. No guessed coordinates; default waits for human confirmation.',{'asset_id':S,'issue_indices':{'type':'array','items':{'type':'integer'},'maxItems':8}},['asset_id']),
    schema('list_repair_masks','List locally created masks and their confirmation status for the managed image.',{'asset_id':S},['asset_id']),
    schema('apply_image','Apply a candidate with hash-matched passing visual review and explicit auto-apply permission, archive old image.',{'asset_id':S,'plan_index':I},['asset_id','plan_index']),
]

class ToolHost:
    def __init__(self,ctx,task,vision_factory=Provider,generator=None):
        self.ctx=ctx;self.store=ctx.store;self.task=task;self.cfg=self.store.task(task)['config'];self.vision_factory=vision_factory;self.generator=generator or wb.generate
    def event(self,kind,payload): self.store.event(self.task,kind,payload)
    def check_stop(self):
        if (self.store.folder/'tasks'/self.task/'stop').is_file(): raise InterruptedError('已请求停止，当前请求结束后不再提交新请求。')
    def execute(self,name,args):
        spec=next((s['function']['parameters'] for s in TOOLS if s['function']['name']==name),None)
        if not spec or not isinstance(args,dict): raise ValueError('未知工具或参数不是对象。')
        if set(args)-set(spec['properties']) or any(k not in args for k in spec['required']): raise ValueError('工具参数缺失或包含未授权字段。')
        for k,v in args.items():
            p=spec['properties'][k];typ=p['type']
            if typ=='string' and (not isinstance(v,str) or len(v)>18000): raise ValueError('字符串参数无效。')
            if typ=='integer' and (type(v) is not int or v<0 or v>1000): raise ValueError('序号参数无效。')
            if typ=='array' and (not isinstance(v,list) or len(v)>p.get('maxItems',50)): raise ValueError('列表参数无效。')
            if 'enum' in p and v not in p['enum']: raise ValueError('枚举参数无效。')
        self.check_stop()
        return getattr(self,name)(**args)
    def project_overview(self):
        rules=self.ctx.root/'AGENTS.md'
        profile=settings.get(self.cfg.get('repair_profile') or self.cfg.get('image_profile')) if self.cfg.get('image_profile') else None
        restored=(self.store.folder/'restore-map.json').is_file()
        return dict(root=str(self.ctx.root),roles=wb.roles(),restored_project=restored,history_policy='Restored historical tasks are read-only; reread current catalog/reference IDs and save a fresh plan before any new execution.' if restored else '',mode=self.cfg.get('mode','discuss'),limits={k:self.cfg.get(k) for k in ('image_limit','vision_limit','repair_limit','planner_limit')},auto_mask=self.cfg.get('auto_mask',False),repair_profile=profile['id'] if profile else None,mask_support=profile.get('mask_support','unknown') if profile else 'unknown',auto_review=self.cfg.get('auto_review',False),auto_apply=self.cfg.get('auto_apply',False),rules=rules.read_text('utf-8-sig')[:7000] if rules.is_file() else '',workflow='read character/experiences -> save plan -> generate -> inspect/review -> localize defects -> create mask -> confirm mask -> associated repair -> compare before/after -> optional archive/apply')
    def search_catalog(self,kind,query='',offset=0):
        rows=self.ctx.catalog(kind);rows=[r for r in rows if not query or query.lower() in json.dumps(r,ensure_ascii=False).lower()]
        return dict(total=len(rows),items=rows[offset:offset+25],next_offset=offset+25 if len(rows)>offset+25 else None)
    def read_character(self,role):
        if role not in wb.roles(): raise ValueError('角色不存在。')
        try: description=wb.character(role)
        except FileNotFoundError: description=role+'；身份和衣装以选定参考图为准。'
        return dict(role=role,description=description,references=self.ctx.refs(role),constraints=self.ctx.constraints(role)[0])
    def search_knowledge(self,query='',role=''): return {'sections':self.ctx.knowledge(query,role)}
    def list_images(self,role,offset=0,label=''):
        if role not in wb.roles(): raise ValueError('角色不存在。')
        rows=self.ctx.images(role)
        if label: rows=[r for r in rows if r.get('label')==label]
        return dict(total=len(rows),items=rows[offset:offset+40],next_offset=offset+40 if len(rows)>offset+40 else None)
    def save_plan(self,summary,items):
        if not items: raise ValueError('计划为空。')
        # Cannot change already-executing plan: stable plan indices are audit identifiers.
        with self.store.connect() as db:
            if db.execute("SELECT id FROM calls WHERE task=? AND kind='image'",(self.task,)).fetchone(): raise ValueError('已开始生图的计划不可改写；请开下一轮任务。')
        for row in items:
            if not isinstance(row,dict) or set(row)-{'role','pose_id','scene_id','reference_ids','target_asset','notes'}: raise ValueError('计划条目结构无效。')
            if any(not isinstance(row.get(k),str) for k in ('role','pose_id','scene_id')) or row['role'] not in wb.roles(): raise ValueError('计划角色或分类 ID 无效。')
            self.ctx.entry(row['pose_id'],'pose');self.ctx.entry(row['scene_id'],'scene')
            if not isinstance(row.get('reference_ids'),list) or not 1<=len(row['reference_ids'])<=6 or any(not isinstance(x,str) for x in row['reference_ids']): raise ValueError('每项需要 1–6 张明确参考图。')
            for ident in row['reference_ids']:
                a=self.ctx.get_asset(ident)
                if a['role']!=row['role'] or a['kind']!='reference': raise ValueError('参考图必须为该角色参考图库中的图片。')
            if row.get('target_asset'):
                a=self.ctx.get_asset(row['target_asset'])
                if a['role']!=row['role'] or not a.get('image_id'): raise ValueError('返修原图须属于该角色图集。')
            if not isinstance(row.get('notes',''),str): raise ValueError('单项备注必须为文本。')
            if len(str(row.get('notes','')))>5000: raise ValueError('单项备注过长。')
        display=[dict(role=x['role'],pose=self.ctx.entry(x['pose_id'],'pose')['name'],scene=self.ctx.entry(x['scene_id'],'scene')['name']) for x in items]
        value=dict(summary=summary,items=items,display_items=display);self.store.plan(self.task,value);self.event('plan',value);return value
    def plan_item(self,index):
        plan=self.store.plan(self.task)
        if not plan or type(index) is not int or index<0 or index>=len(plan['items']): raise ValueError('请先保存计划并使用其零起始序号。')
        return plan['items'][index]
    def require_execute(self):
        if self.cfg.get('mode')!='execute': raise ValueError('当前是讨论模式，生图、视觉 API 检查和入库未启用；可先讨论计划。')
    def generate_image(self,plan_index,extra='',expression='',mask_id=''):
        self.require_execute();row=self.plan_item(plan_index);role=row['role']
        with self.store.connect() as db:
            blocked=[json.loads(x[0]) for x in db.execute("SELECT request FROM calls WHERE task=? AND kind='image' AND status IN ('started','unknown','safety_rejected')",(self.task,))]
        if any(x.get('plan_index')==plan_index for x in blocked): raise ValueError('本计划项已被安全拒绝或结果未知，本轮不改词重试、不切换接口。')
        pose=self.ctx.entry(row['pose_id'],'pose');scene=self.ctx.entry(row['scene_id'],'scene')
        refs=[self.ctx.get_asset(i) for i in row['reference_ids']];target=None
        if row.get('target_asset'):
            target=self.ctx.get_asset(row['target_asset']);d=wb.load(role,target['image_id'])
            reason=target.get('repair_reason') or target.get('reason')
            if target.get('label')!='待返修' or not reason: raise ValueError('返修须先有待返修标签和明确问题记录。')
            # Repair count persists across chat turns, not just this run.
            with self.store.connect() as db:
                repairs=[json.loads(x[0]) for x in db.execute("SELECT request FROM calls WHERE kind='image' AND status IN ('started','saved','unknown','request_failed','safety_rejected')")]
            if sum(1 for x in repairs if x.get('repair_target')==target['image_id'])>=self.cfg.get('repair_limit',2): raise ValueError('该原图的自动返修轮数已达上限，需人工处理。')
            refs=[target]+refs;ident=d['id']
        else: ident=None
        if mask_id and not target: raise ValueError('局部蒙版只用于有关联原图的返修计划。')
        mask_record=None
        if target and not mask_id:
            outstanding=[m for m in self.store.masks(target['id']) if m['body']['status'] in ('pending','approved') and m['body']['source']['sha256']==sha(target['path'])]
            if outstanding: raise ValueError('该图片已有局部蒙版，请先确认并传入 mask_id；不会忽略蒙版改成整图返修。')
        if mask_id:
            mask_record,_=repair.validate(self.store,mask_id,require_approved=True)
            source=mask_record['body']['source']
            if source['role']!=role or source['image_id']!=target['image_id'] or source.get('candidate_id')!=target.get('candidate_id'): raise ValueError('蒙版不属于本计划指定的待修图片。')
        pid=(self.cfg.get('repair_profile') or self.cfg.get('image_profile')) if target else self.cfg.get('image_profile')
        profile=settings.get(pid)
        if not profile or not profile.get('available'): raise ValueError('生图接口未配置或凭据不可用。')
        if not (target and self.cfg.get('repair_profile')) and self.cfg.get('image_model') and self.cfg['image_model']!=profile['model']: raise ValueError('任务模型与保存接口不一致，请重新选择接口。')
        if mask_record and (profile.get('mask_support')!='supported' or profile.get('channel')=='codex' or settings.resolve_protocol(profile,profile['model'])!='openai-images'): raise ValueError('所选返修接口未明确支持透明蒙版，或其协议不支持蒙版；未发请求。请在接口设置确认能力，或显式选择整图返修。')
        constraints,sources=self.ctx.constraints(role)
        modules=dict(character=self.read_character(role)['description'],pose=pose['text'],scene=scene['text'],reference_notes='\n'.join(f'{i+1}: {r["name"]} ({r.get("purpose") or r["kind"]})' for i,r in enumerate(refs)),constraints=constraints,repair=reason if target else '',extra='')
        modules['expression']=expression
        if mask_record:
            w,h=mask_record['body']['source']['size']
            modules['repair']=mask_record['body']['reason']+f'\nEdit only the transparent mask region. Keep identity, costume, intended pose and all normal anatomy unchanged. The first reference is the exact image to repair. Preserve its exact {w}x{h} canvas; do not crop, resize or reframe.'
        if extra or row.get('notes'): modules['constraints']+='\n'+row.get('notes','')+'\n'+extra
        prompt=wb.compose(modules)
        request=dict(role=role,plan_index=plan_index,pose=pose,scene=scene,prompt=prompt,modules=modules,sources=sources,profile=profile['id'],model=profile['model'],size=self.cfg.get('size','1024x1536'),quality=self.cfg.get('quality','high'),repair_target=target['image_id'] if target else None)
        if mask_record:
            # Local compositing requires the native canvas, not the new-image preset.
            request['size']=f'{w}x{h}' if not w%16 and not h%16 and 1/3<=w/h<=3 else 'auto'
        request['mask']=dict(id=mask_id,path=mask_record['body']['mask_path'],sha256=mask_record['body']['mask_sha256'],source_sha256=mask_record['body']['source']['sha256'],edit_fraction=mask_record['body']['edit_fraction']) if mask_record else None
        fp=digest(dict(prompt=prompt,refs=[sha(x['path']) for x in refs],profile=profile['id'],model=profile['model'],size=request['size'],quality=request['quality'],target=request['repair_target'],mask=request['mask']['sha256'] if request['mask'] else None))
        # Reservation is committed before snapshots and network; uncertain submissions cannot repeat.
        call=self.store.reserve(self.task,'image',fp,request,self.cfg.get('image_limit',10))
        try:
            snap=self.ctx.snapshot(self.task,call,request,refs)
            if not ident: ident=wb.create_new(role)['id']
            mask_path=''
            if mask_record:
                import shutil
                original_mask=Path(mask_record['body']['mask_path']);mask_copy=Path(snap['references'][0]['snapshot_path']).parent.parent/'mask.png';shutil.copy2(original_mask,mask_copy)
                if sha(mask_copy)!=mask_record['body']['mask_sha256']: raise ValueError('蒙版快照校验失败，未提交请求。')
                mask_path=str(mask_copy);snap['mask']=dict(request['mask'],snapshot_path=mask_path)
                (mask_copy.parent/'request.json').write_text(json.dumps(snap,ensure_ascii=False,indent=2),'utf-8')
            data=wb.prepare(role,ident,modules,[r['snapshot_path'] for r in snap['references']],profile['id'],profile['model'],request['size'],request['quality'],mask_path)
            data['preserve_outside']=bool(mask_record)
            data['agent']=dict(task=self.task,call=call,plan_index=plan_index,repair_source=dict(path=snap['references'][0]['snapshot_path'],sha256=snap['references'][0]['sha256'],reason=modules['repair']) if target else None,mask_id=mask_id or None,preserve_outside=bool(mask_record))
            self.event('activity',{'text':f'正在生成：{role} · {pose["name"]} · {scene["name"]}'})
            result=self.generator(data)
            status=result.get('status','request_failed')
            if status!='saved' and any(s in str(result.get('error','')).lower() for s in ('timed out','timeout','connection reset','connection error','connection aborted','remote protocol','连接中断','超时')): status='unknown'
            self.store.finish_call(call,status,result)
            if status!='saved':
                self.event('generation_failed',dict(result,status=status,call=call));return dict(result,status=status,action='停止本项；安全拒绝不绕过，未知状态不重发。')
            c=result['candidate'];asset=self.ctx.asset(c['path'],role,'candidate',image_id=ident,candidate_id=c['id'],label='待审核',plan_index=plan_index,task=self.task)
            if result.get('composite_warning'): self.event('repair_warning',{'error':result['composite_warning'],'asset':asset,'action':'API 已返回，蒙版外合成失败；候选保留待审核，不自动通过。'})
            self.event('image',asset)
            return dict(asset=asset,record=result.get('record'),status='待审核')
        except Exception as e:
            # Exceptions after reservation may have reached provider; conservative outcome.
            self.store.finish_call(call,'unknown',{'error':str(e)});raise
    def create_repair_mask(self,asset_id,issue_indices=None):
        a=self.ctx.get_asset(asset_id)
        if not a.get('image_id') or a.get('label')!='待返修': raise ValueError('只有有明确问题的待返修图片能生成局部蒙版。')
        review=self.store.latest_review(asset_id)
        if not review: raise ValueError('未找到当前图片的视觉定位记录，请先重新视觉检查。')
        record=repair.create(self.ctx,self.task,a,review,issue_indices,automatic=bool(self.cfg.get('auto_mask')))
        self.event('mask',dict(mask_id=record['id'],asset_id=asset_id,status=record['body']['status'],edit_fraction=record['body']['edit_fraction']))
        return dict(mask_id=record['id'],status=record['body']['status'],edit_fraction=record['body']['edit_fraction'],issues=record['body']['issues'],mask_path=record['body']['mask_path'],next_step='可用于当前图片的关联返修。' if record['body']['status']=='approved' else '等待用户在局部返修蒙版窗口确认；本轮先结束，不能自动批准。')
    def list_repair_masks(self,asset_id):
        a=self.ctx.get_asset(asset_id)
        return {'items':[dict(id=x['id'],status=x['body']['status'],edit_fraction=x['body']['edit_fraction'],reason=x['body']['reason'],source_sha256=x['body']['source']['sha256']) for x in self.store.masks(a['id'])]}
    def inspect_image(self,asset_id):
        a=self.ctx.get_asset(asset_id);report=quality.technical(a['path'])
        folder=Path(wb.inspection_pack(a['role'],a['image_id'],a.get('candidate_id'))) if a.get('image_id') else None
        return dict(report=report,crops=[str(p) for p in folder.glob('*.png')] if folder else [],note='技术检查不能判定手指和人体结构合格；下一步实际视觉检查。')
    def review_image(self,asset_id,notes=''):
        self.require_execute();a=self.ctx.get_asset(asset_id)
        if not a.get('image_id'): raise ValueError('当前审核仅针对图库原图或生成备选。')
        digest_before=sha(a['path']);technical=quality.technical(a['path'])
        if not technical['technical_pass']: return dict(verdict='fail',technical=technical)
        inspection=self.inspect_image(asset_id)
        references=self.ctx.refs(a['role']);chosen=sorted(references,key=lambda r:r.get('purpose')!='face')[:1]
        content=[{'type':'text','text':'Review the FIRST image (the candidate/original) using the following crops and identity references. Check correct visible fingers, handedness, legs, feet, costume/material, wing attachment and perspective, identity, pose and scene. Return {"verdict":"pass|fail|uncertain","summary":"...","checks":{"identity":"pass|fail|uncertain|not_visible", "costume":"...","hands":"...","legs":"...","feet":"...","material":"...","wings":"...","pose":"...","scene":"..."},"issues":[{"region":"...","observation":"specific visible evidence","confidence":0.9,"bbox":[left,top,right,bottom]}]}. All nine checks required. Bounding boxes are OPTIONAL and must use normalized 0..1 coordinates of the FIRST FULL image, never a crop. Omit a box when you cannot confidently localize the exact defect; never guess. Not-visible means normal occlusion or out of frame, not a guessed pass. A pass requires confident verification of all applicable visible features.\nRole: '+a['role']+'\nRequested inspection: '+notes+'\nQuality constraints: '+self.ctx.constraints(a['role'])[0]}]
        content.append(image_content(a['path']))
        for p in inspection['crops']:
            if Path(p).stem in ('上半部','下半部'): content.extend([{'type':'text','text':'Crop of the same image: '+Path(p).stem},image_content(p)])
        if chosen: content.extend([{'type':'text','text':'Identity/costume reference (not the image being reviewed).'},image_content(chosen[0]['path'])])
        standard=Path(self.ctx.cfg['rules'])/'白鹤白丝-标准足部.png'
        if '白鹤' in a['role'] and standard.is_file(): content.extend([{'type':'text','text':'Material standard: continuous white hosiery, separate big toe, shallow other-toe grooves.'},image_content(standard)])
        # Send recorded modules alongside actual pixels; paths alone are not visual evidence.
        meta=Path(a['path']).with_suffix('.response.json')
        record={};comparison_source=None;outside=None
        if meta.is_file():
            record=json.loads(meta.read_text('utf-8'));content[0]['text']+='\nOriginal requested modules: '+json.dumps(record.get('prompt_modules') or record.get('modules') or {},ensure_ascii=False)[:12000]
        agent_meta=record.get('agent') or {};source=agent_meta.get('repair_source')
        with self.store.connect() as db:
            generated=[(dict(x),json.loads(x['request']),json.loads(x['result'])) for x in db.execute("SELECT * FROM calls WHERE kind='image' AND status='saved'")]
        matching=next(((call,req) for call,req,result in generated if req.get('repair_target') and a.get('candidate_id') and a['candidate_id'] in (result.get('candidate',{}).get('id'),result.get('raw_candidate',{}).get('id'))),None)
        if matching:
            call,req=matching
            if not source or agent_meta.get('call')!=call['id'] or agent_meta.get('task')!=call['task']: raise ValueError('关联返修缺少匹配的实际请求记录，无法跳过前后对照。')
            if bool(agent_meta.get('preserve_outside'))!=bool(req.get('mask')): raise ValueError('返修范围设置与实际请求不一致，保留待审核。')
        if source:
            p=Path(source['path']).resolve();role_other=wb.role_dir(a['role'])/'other'
            if not p.is_relative_to(role_other.resolve()) or not p.is_file() or sha(p)!=source.get('sha256'): raise ValueError('返修前图快照缺失或变化，无法核验返修效果。')
            comparison_source=str(p)
            if matching:
                actual=p.parent.parent/'request.json';request_snapshot=json.loads(actual.read_text('utf-8'))
                first=request_snapshot['references'][0]
                if Path(first['snapshot_path']).resolve()!=p or first['sha256']!=source['sha256']: raise ValueError('返修前图与实际请求的第一参考图不一致。')
            content.extend([{'type':'text','text':'BEFORE REPAIR: compare the FIRST image with this exact source. Defect to fix: '+source.get('reason','')},image_content(p)])
            content[0]['text']+='\nThis is a REPAIR candidate. Also return comparison with ALL five fields '+json.dumps(COMPARISON)+', each pass|fail|uncertain. Verify the stated defect is fixed, no new defects, and visible identity, costume and intended pose are preserved. Use uncertain if the evidence cannot establish this.'
            if agent_meta.get('preserve_outside'):
                mask=record.get('mask') or {};mp=Path(mask.get('snapshot_path') or mask.get('path') or '').resolve()
                if not mp.is_relative_to(role_other.resolve()) or not mp.is_file() or sha(mp)!=mask.get('sha256'): raise ValueError('返修蒙版快照缺失或变化，保留待审核。')
                if matching and mask['sha256']!=req['mask']['sha256']: raise ValueError('返回记录中的蒙版与本次实际请求不一致。')
                outside=repair.outside_check(p,a['path'],mp)
        provider_id=self.cfg.get('vision_provider');fp=digest([asset_id,digest_before,provider_id,notes,self.task])
        request=dict(asset_id=asset_id,sha256=digest_before,provider=provider_id,criteria=content[0]['text'],image_paths=[a['path'],*inspection['crops'],*[x['path'] for x in chosen],*([comparison_source] if comparison_source else [])],outside_check=outside)
        call=self.store.reserve(self.task,'vision',fp,request,self.cfg.get('vision_limit',10));client=None
        try:
            client=self.vision_factory(provider_id);body,usage=client.vision(content)
            if sha(self.ctx.get_asset(asset_id)['path'])!=digest_before: raise ValueError('视觉检查期间图片发生变化。')
            checks=body.get('checks');issues=body.get('issues')
            if body.get('verdict') not in ('pass','fail','uncertain') or not isinstance(checks,dict) or any(checks.get(k) not in ('pass','fail','uncertain','not_visible') for k in CHECKS) or not isinstance(issues,list) or not isinstance(body.get('summary'),str): raise ValueError('视觉模型响应缺少完整检查与证据，保留待审核。')
            if any(not isinstance(x,dict) or not isinstance(x.get('observation'),str) or not x['observation'].strip() or not isinstance(x.get('region'),str) or type(x.get('confidence')) not in (int,float) or not 0<=x['confidence']<=1 for x in issues): raise ValueError('视觉问题证据无效，保留待审核。')
            if body['verdict']=='pass' and (issues or 'fail' in checks.values() or 'uncertain' in checks.values() or not any(x=='pass' for x in checks.values())): body['verdict']='uncertain'
            if body['verdict']=='fail' and (not issues or max(x['confidence'] for x in issues)<.85 or 'fail' not in checks.values()): body['verdict']='uncertain'
            for issue in issues:
                if 'bbox' in issue:
                    try: issue['bbox']=repair.box(issue['bbox'])
                    except ValueError: issue.pop('bbox',None);issue['localization_error']='无效定位框，需人工定位，不能猜测蒙版。'
            if comparison_source:
                comparison=body.get('comparison')
                complete=isinstance(comparison,dict) and all(comparison.get(k) in ('pass','fail','uncertain') for k in COMPARISON)
                if body['verdict']=='pass' and (not complete or any(comparison[k]!='pass' for k in COMPARISON)): body['verdict']='uncertain'
                body['comparison_source']=dict(path=comparison_source,sha256=source['sha256'])
            if outside:
                body['outside_check']=outside
                if outside['verdict']!='pass':
                    if outside['verdict']=='fail':
                        body['verdict']='fail';checks['material']='fail';issues.append(dict(region='蒙版外',observation='逐像素对照发现蒙版外发生变化；不能自动入库。',confidence=1.0))
                    elif body['verdict']=='pass': body['verdict']='uncertain'
            if technical.get('warnings') and body['verdict']=='pass': body['verdict']='uncertain'
            body.update(image_sha256=digest_before,asset_id=asset_id,provider=provider_id,usage=usage,technical=technical)
            review_id=self.store.review(self.task,asset_id,digest_before,body)
            note=body['summary']+'\n'+'\n'.join(x['region']+': '+x['observation'] for x in issues)
            label=a.get('label','待审核')
            if body['verdict']=='fail': label='待返修'
            elif a['kind']=='candidate': label='已通过' if body['verdict']=='pass' and self.cfg.get('auto_review') else '待审核'
            if label=='待返修' or a['kind']=='candidate': wb.review(a['role'],a['image_id'],a.get('candidate_id'),label,note or '无法核验，需人工审核。',reviewer='agent-vision:'+str(provider_id))
            body.update(label=label,review_id=review_id);self.store.finish_call(call,'saved',body);self.event('review',body)
            self.event('image',self.ctx.get_asset(asset_id));return body
        except Exception as e: self.store.finish_call(call,'request_failed',{'error':str(e)});raise
        finally:
            if client: client.close()
    def apply_image(self,asset_id,plan_index):
        self.require_execute()
        if not self.cfg.get('auto_apply') or not self.cfg.get('auto_review'): raise ValueError('自动通过和自动入库未同时启用；请在工作台手动审核应用。')
        a=self.ctx.get_asset(asset_id);row=self.plan_item(plan_index)
        if a['kind']!='candidate' or a['role']!=row['role']: raise ValueError('只能应用计划角色的备选。')
        # Image association must match the actual reserved request, not a model-supplied index.
        with self.store.connect() as db:
            records=[(json.loads(x[0]),json.loads(x[1])) for x in db.execute("SELECT request,result FROM calls WHERE task=? AND kind='image' AND status='saved'",(self.task,))]
        if not any(x.get('candidate',{}).get('id')==a['candidate_id'] and request.get('plan_index')==plan_index for request,x in records): raise ValueError('此备选不是当前计划项的产物。')
        evidence=self.store.latest_review(asset_id)
        if not evidence or evidence['sha']!=sha(a['path']) or evidence['body']['verdict']!='pass' or a.get('label')!='已通过': raise ValueError('没有当前实际图片的通过证据。')
        d=wb.load(a['role'],a['image_id']);name=None
        if not d.get('path'):
            pose=self.ctx.entry(row['pose_id'],'pose');scene=self.ctx.entry(row['scene_id'],'scene')
            nums=[int(m.group(1)) for p in wb.formal_images(wb.role_dir(a['role'])) if (m:=re.search(r'_(\d+)$',p.stem))]
            name=wb.semantic_name(pose['category'],pose['name'],a['role'],scene['category'],scene['name'],max(nums,default=0)+1)
        result=wb.apply_candidate(a['role'],a['image_id'],a['candidate_id'],name=name,policy='archive');self.event('applied',result);return result
