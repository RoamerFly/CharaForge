"""Managed image locations, leaving historical request snapshots intact."""
import os,shutil,uuid
from pathlib import Path
from provenance import read_json,sha

FOLDERS={'已通过':'备选','待审核':'待审核','待返修':'待返修'}

def directories(role_dir):
    for folder in FOLDERS.values():(Path(role_dir)/'other'/folder).mkdir(parents=True,exist_ok=True)

def move_image(source,target,atomic):
    source=Path(source).resolve();target=Path(target).resolve()
    if source==target:return str(source)
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():raise ValueError('审核文件目标已存在：'+str(target))
    digest=sha(source);source.rename(target)
    try:
        side=source.with_suffix('.response.json')
        if side.is_file():
            meta=read_json(side,{}) or {};meta.update(saved_path=str(target),sha256=digest)
            atomic(target.with_suffix('.response.json'),meta);side.unlink()
        if sha(target)!=digest:raise ValueError('审核文件迁移后内容发生变化。')
    except Exception:
        target.rename(source);raise
    return str(target)

def relocate(record,role_dir,atomic,candidate=None):
    directories(role_dir);item=candidate or record
    if not item.get('path') or not Path(item['path']).is_file():return None
    old=Path(item['path']).resolve();root=Path(role_dir).resolve()
    if not old.is_relative_to(root):raise ValueError('审核文件必须位于当前角色目录内。')
    if candidate is not None:
        stem=Path(record.get('gallery_path') or record.get('path') or '新图').stem
        name=stem+'-备选-'+item['id'][:8]+'.png'
        target=root/'other'/FOLDERS[item['label']]/name
    else:
        formal=Path(record.get('gallery_path') or record['path']).resolve()
        if not formal.is_relative_to(root) or 'other' in formal.relative_to(root).parts:raise ValueError('正式入库位置必须在角色的图片目录中。')
        record['gallery_path']=str(formal)
        target=formal if item['label']=='已通过' else root/'other'/FOLDERS[item['label']]/formal.name
    new=move_image(old,target,atomic);item['path']=new
    if record.get('generation') and candidate is None:record['generation']['saved_path']=new
    return dict(old=str(old),new=new) if str(old)!=new else None
