"""Evidence-scoped masks and exact outside-pixel verification; never edits originals."""
import json,math,uuid
from pathlib import Path
from PIL import Image,ImageDraw,ImageFilter,ImageChops
from provenance import sha
from agent.store import now

MAX_EDIT_FRACTION=.25

def box(value):
    if not isinstance(value,list) or len(value)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in value): raise ValueError('缺陷区域需要有限数值 [左,上,右,下]，坐标归一化到原图。')
    x0,y0,x1,y1=value
    if not 0<=x0<x1<=1 or not 0<=y0<y1<=1: raise ValueError('缺陷区域坐标越界、反向或为空。')
    return value

def mask_info(path, size=None):
    with Image.open(path) as im:
        im.load()
        if im.format!='PNG' or 'A' not in im.getbands() or (size and im.size!=tuple(size)): raise ValueError('蒙版需为与原图同尺寸的透明 PNG。')
        histogram=im.getchannel('A').histogram();pixels=im.width*im.height
        fraction=(pixels-histogram[255])/pixels
        if not 0<fraction<=MAX_EDIT_FRACTION: raise ValueError('局部蒙版为空或修改范围超过 25%；请缩小蒙版或显式选择整图返修。')
        bounds=Image.eval(im.getchannel('A'),lambda p:255-p).getbbox()
        return dict(size=list(im.size),edit_fraction=fraction,bounds=list(bounds),sha256=sha(path))

def source_path(store, body):
    source=body['source'];state=Path(source['state_path']).resolve()
    role_root=(store.root/'角色图集'/source['role']).resolve()
    if not role_root.is_relative_to(store.root/'角色图集') or not state.is_relative_to(role_root/'other'): raise ValueError('蒙版来源记录越出当前角色目录。')
    record=json.loads(state.read_text('utf-8'))
    if record.get('role')!=source['role'] or record.get('id')!=source['image_id']: raise ValueError('蒙版来源图片身份不一致。')
    target=record if source.get('candidate_id') is None else next((c for c in record['candidates'] if c['id']==source['candidate_id']),None)
    if not target or not target.get('path'): raise ValueError('蒙版来源图片已删除。')
    p=Path(target['path']).resolve()
    if not p.is_relative_to(role_root) or sha(p)!=source['sha256']: raise ValueError('原图内容已变化，请重新检查与生成蒙版。')
    return str(p)

def validate(store, ident, require_approved=False):
    record=store.mask(ident);body=record['body'];src=source_path(store,body);p=Path(body['mask_path']).resolve()
    role_root=(store.root/'角色图集'/body['source']['role']/'other').resolve()
    if not p.is_relative_to(role_root) or not p.is_file() or sha(p)!=body['mask_sha256']: raise ValueError('蒙版已变化、缺失或不在该角色 other 内。')
    mask_info(p,body['source']['size'])
    if body['status']=='superseded': raise ValueError('此蒙版已被新版本替代。')
    if require_approved and body['status']!='approved': raise ValueError('蒙版尚未确认。请在“局部返修蒙版”窗口确认范围后，再继续此聊天。')
    return record,src

def create(ctx, task, asset, review, issue_indices=None, automatic=False):
    body=review['body']
    if body.get('verdict')!='fail' or review['sha']!=sha(asset['path']): raise ValueError('需要当前原图的明确不合格视觉记录，不能凭旧检查或猜测区域生成蒙版。')
    issues=body.get('issues',[])
    indices=list(range(len(issues))) if issue_indices is None else issue_indices
    if not isinstance(indices,list) or not 1<=len(indices)<=8 or any(type(i) is not int or i<0 or i>=len(issues) for i in indices) or len(set(indices))!=len(indices): raise ValueError('请选择真实缺陷序号，最多 8 项，不能重复。')
    selected=[]
    for i in indices:
        issue=issues[i]
        if issue.get('confidence',0)<.85: raise ValueError('缺陷置信度不足，不能自动定位局部修复范围。')
        box(issue.get('bbox'));selected.append(issue)
    with Image.open(asset['path']) as im:size=im.size
    alpha=Image.new('L',size,255);draw=ImageDraw.Draw(alpha);w,h=size
    pixels=[]
    for issue in selected:
        x0,y0,x1,y1=issue['bbox'];px=max(4,round(w*.015));py=max(4,round(h*.015))
        bounds=(max(0,math.floor(x0*w)-px),max(0,math.floor(y0*h)-py),min(w,math.ceil(x1*w)+px),min(h,math.ceil(y1*h)+py))
        draw.rectangle((bounds[0],bounds[1],bounds[2]-1,bounds[3]-1),fill=0);pixels.append(list(bounds))
    feather=max(1,min(6,round(min(size)*.003)));alpha=alpha.filter(ImageFilter.GaussianBlur(feather))
    ident=uuid.uuid4().hex;folder=Path(__import__('workbench').statefile(asset['role'],asset['image_id'])).parent/'agent-masks'/ident;folder.mkdir(parents=True,exist_ok=False)
    path=folder/'mask.png';mask=Image.new('RGBA',size,'white');mask.putalpha(alpha);mask.save(path)
    info=mask_info(path,size)
    source=dict(role=asset['role'],image_id=asset['image_id'],candidate_id=asset.get('candidate_id'),state_path=str(__import__('workbench').statefile(asset['role'],asset['image_id'])),sha256=sha(asset['path']),size=list(size),path=asset['path'])
    record=dict(source=source,review_id=review['id'],issues=selected,issue_indices=indices,pixel_bounds=pixels,feather=feather,mask_path=str(path),mask_sha256=info['sha256'],edit_fraction=info['edit_fraction'],status='approved' if automatic else 'pending',approval=dict(by='task-policy',at=now()) if automatic else None,reason='\n'.join(x['region']+': '+x['observation'] for x in selected),created=now())
    ctx.store.save_mask(task,asset['id'],record,ident)
    (folder/'蒙版记录.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf-8')
    return ctx.store.mask(ident)

def approve(store, ident):
    record,src=validate(store,ident);body=record['body']
    body.update(status='approved',approval=dict(by='manual',at=now(),source_sha256=sha(src),mask_sha256=sha(body['mask_path'])))
    store.save_mask(record['task'],record['asset'],body,ident)
    store.event(record['task'],'mask_approved',dict(mask_id=ident,asset_id=record['asset']))
    return store.mask(ident)

def revise(store, ident, new_path, reason):
    record,_=validate(store,ident);old=record['body'];new=Path(new_path).resolve()
    if not new.is_relative_to(Path(old['mask_path']).parent): raise ValueError('编辑后的蒙版需保存在当前蒙版记录目录内。')
    info=mask_info(new,old['source']['size'])
    if not isinstance(reason,str) or len(reason.strip())<3: raise ValueError('请保留明确的局部修复要求。')
    new_id=uuid.uuid4().hex;body=dict(old,mask_path=str(new),mask_sha256=info['sha256'],edit_fraction=info['edit_fraction'],status='pending',approval=None,parent_mask=ident,reason=reason.strip(),manual_revision=now())
    store.save_mask(record['task'],record['asset'],body,new_id);old.update(status='superseded',replaced_by=new_id);store.save_mask(record['task'],record['asset'],old,ident)
    store.event(record['task'],'mask_revised',dict(mask_id=new_id,previous_mask_id=ident))
    return store.mask(new_id)

def outside_check(original,result,mask):
    """Strict pixel comparison only where mask alpha is exactly opaque."""
    with Image.open(original) as a,Image.open(result) as b,Image.open(mask) as m:
        if a.size!=b.size or a.size!=m.size or 'A' not in m.getbands(): return dict(verdict='uncertain',reason='原图、候选或蒙版尺寸/通道不匹配，不能保证局部范围。')
        exterior=m.getchannel('A').point(lambda p:255 if p==255 else 0)
        diff=ImageChops.multiply(ImageChops.difference(a.convert('RGB'),b.convert('RGB')),exterior.convert('RGB'))
        bounds=diff.getbbox()
        return dict(verdict='pass' if bounds is None else 'fail',changed_bounds=list(bounds) if bounds else None,method='exact_opaque_outside_pixels')
