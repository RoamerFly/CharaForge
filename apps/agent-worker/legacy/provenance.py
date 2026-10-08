"""Read-only reconstruction of project image provenance; never guess missing history."""
import hashlib
import json
from pathlib import Path

from paths import ROOT,APP


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text('utf-8-sig'))
    except (OSError, ValueError):
        return default


def excluded(path):
    return any(s.lower().startswith('temp') for s in Path(path).parts)

def resolve_alias(path,aliases):
    p=Path(path);seen=set()
    while not p.is_file():
        key=str(p.resolve()).lower()
        if key in seen or key not in aliases:break
        seen.add(key);p=Path(aliases[key])
    return p


class Library:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.aliases=read_json(APP/'config/path-aliases.json',{}) or {}
        self.by_hash = {}
        self.by_path = {}
        self.paths_by_hash = {}
        self.known_paths_by_hash = {}
        self.all_image_hashes = None
        self.gallery = []
        self.failed = []
        self.scan()

    def add(self, row, source, priority):
        row = dict(row, _record=str(source), _priority=priority)
        h = row.get('sha256') or row.get('image_sha256')
        if h:
            self.by_hash.setdefault(h, []).append(row)
            for key in ('image','target','saved_path','accepted_candidate','source_image'):
                if row.get(key):self.known_paths_by_hash.setdefault(h,[]).append(row[key])
        for key in ('image', 'target', 'saved_path', 'accepted_candidate'):
            if row.get(key):
                self.by_path.setdefault(str(Path(row[key]).resolve()).lower(), []).append(row)

    def scan(self):
        main = self.root / '角色图集/other/manifests/当前图集清单.json'
        for row in read_json(main, []) or []:
            self.add(row, main, 20)
            if Path(row.get('image', '')).is_file():
                self.gallery.append({'path': row['image'], 'name': Path(row['image']).name,
                                     'role': row.get('group_name', ''),
                                     'review': row.get('anatomy_review_status', '')})
        # Role records contain historical reference remaps that the current inventory lacks.
        role_records=list((self.root/'角色图集').glob('*/other/generate/生成记录.json'))+list((self.root/'角色图集').glob('*/other/历史资料/generate/生成记录.json'))
        for p in role_records:
            for row in (read_json(p, {}) or {}).get('records', []):
                self.add(row, p, 30)
        for p in (self.root / 'other/reports').glob('*/任务.json'):
            data = read_json(p, {}) or {}
            for row in data.get('tasks', []):
                self.add(dict(row, model=row.get('model') or data.get('model'), api_base=row.get('api_base') or data.get('provider', ''),
                              credential_label=row.get('credential_label') or data.get('credential_label', '')),
                         p, 40)
                if row.get('status') == 'safety_rejected':
                    self.failed.append({'id': row.get('id'), 'role': row.get('role'),
                                        'pose': row.get('pose_name'), 'record': str(p),
                                        'status': row['status'], 'prompt_file': str(resolve_alias(row['prompt_file'],self.aliases)) if row.get('prompt_file') else None})
        # Exact response sidecars are preferred only when their saved output hash matches.
        # Grok comparison jobs store their actual prompt and uploaded snapshots in plans,
        # rather than the older GPT .response.json format.
        import settings
        grok_profiles=[x for x in settings.profiles() if x.get('protocol')=='grok-json' or x.get('model','').startswith('grok-')]
        for p in (self.root/'other/reports').glob('*/计划与实际请求.json'):
            data=read_json(p,{}) or {}
            for job in data.get('jobs',[]):
                if not job.get('model','').startswith('grok-') or job.get('status')!='completed' or not job.get('prompt') or not job.get('sha256'):continue
                base=data.get('base_url','');cfg=next((x for x in grok_profiles if x['base']==base),None)
                refs=[dict(path=x.get('original'),snapshot_path=x.get('snapshot'),sha256=x.get('sha256')) for x in job.get('actual_references',[])]
                self.add(dict(image=job.get('archived_path') or job.get('path'),sha256=job['sha256'],
                              prompt=job['prompt'],effective_prompt=job['prompt'],modules=job.get('modules'),
                              references=refs,model=job['model'],api_base=base,
                              credential_label=cfg['id'] if cfg else '',mode='edit' if refs else 'generate',
                              request_protocol='grok-json',size_quality_mode='not_sent'),p,70)
        for p in self.root.rglob('*.response.json'):
            if excluded(p):
                continue
            row = read_json(p, {}) or {}
            if row.get('status') == 'saved' and (row.get('prompt_file') or row.get('prompt')):
                self.add(row, p, 60)
        for p in (self.root/'角色图集').glob('*/other/**/images/*/metadata.json'):
            data=read_json(p,{}) or {}
            for row in [data.get('generation')]+[read_json(Path(c['path']).with_suffix('.response.json'),{}) for c in data.get('candidates',[])]:
                if row and (row.get('prompt') or row.get('effective_prompt')):
                    self.add(dict(row,prompt=row.get('prompt') or row.get('effective_prompt')),p,75)
        for base in ('角色特征及参考图', '角色图集', '姿势/姿势参考图', '场景/场景参考图'):
            for p in (self.root / base).rglob('*'):
                if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') and not excluded(p) and 'other' not in p.parts:
                    # Lazily hash reference files; import needs no expensive full-library hashing.
                    self.by_path.setdefault(str(p.resolve()).lower(), [])

    def resolve_reference(self, ref):
        if isinstance(ref, str):
            ref = {'path': ref}
        path = ref.get('path') or ref.get('current_path') or ref.get('historical_path', '')
        expected = ref.get('sha256') or ref.get('historical_sha256')
        p = resolve_alias(path,self.aliases)
        result = {'path': str(p), 'historical_path': ref.get('historical_path', path),
                  'expected_sha256': expected, 'exists': p.is_file()}
        mismatch = p.is_file() and expected and sha(p)!=expected
        snapshot=resolve_alias(ref['snapshot_path'],self.aliases) if ref.get('snapshot_path') else None
        if (not p.is_file() or mismatch) and snapshot and snapshot.is_file() and (not expected or sha(snapshot)==expected):
            p=snapshot;result.update(path=str(p),exists=True,remapped=True)
            mismatch=False
        if (not p.is_file() or mismatch) and expected:
            if expected not in self.paths_by_hash:
                found = []
                for name in self.known_paths_by_hash.get(expected,[]):
                    q = Path(name)
                    if q.is_file() and not excluded(q) and sha(q)==expected:found.append(str(q))
                if not found:
                    if self.all_image_hashes is None:
                        self.all_image_hashes={}
                        for q in self.root.rglob('*'):
                            if q.is_file() and not excluded(q) and q.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
                                self.all_image_hashes.setdefault(sha(q),[]).append(str(q))
                    found=self.all_image_hashes.get(expected,[])
                self.paths_by_hash[expected] = found
            if self.paths_by_hash[expected]:
                p = Path(self.paths_by_hash[expected][0])
                result.update(path=str(p), exists=True, remapped=True)
        if p.is_file():
            result['actual_sha256'] = sha(p)
            result['hash_matches'] = None if not expected else expected == result['actual_sha256']
        return result

    def unpack(self, rows, image=None):
        rows = sorted(rows, key=lambda x: x.get('_priority', 0), reverse=True)
        primary = rows[0] if rows else {}
        prompt = ''
        prompt_source = ''
        warning = []
        prompt_verified = None
        selected = primary
        for row in rows:
            f = row.get('prompt_file')
            if f:f=str(resolve_alias(f,self.aliases))
            if f and Path(f).is_file():
                prompt = Path(f).read_text('utf-8-sig')
                prompt_source = f
                expected = row.get('prompt_sha256') or row.get('prompt_file_sha256')
                # Old records may hash Unicode prompt text rather than file bytes.
                hashes = {sha(f), hashlib.sha256(prompt.encode()).hexdigest()}
                prompt_verified = None if not expected else expected in hashes
                selected = row
                if prompt_verified is False:
                    if row.get('prompt') and hashlib.sha256(row['prompt'].encode()).hexdigest() == expected:
                        prompt = row['prompt']; prompt_source = row['_record'] + ' :: prompt'; prompt_verified = True
                    else:
                        warning.append('历史提示词文件哈希已变化，当前文本不能确认为当时实际请求。')
                break
            if row.get('prompt'):
                prompt = row['prompt']; prompt_source = row['_record'] + ' :: prompt'; selected = row
                expected = row.get('prompt_sha256')
                prompt_verified = None if not expected else hashlib.sha256(prompt.encode()).hexdigest() == expected
                break
        def field(*keys):
            for row in [selected] + rows:
                for key in keys:
                    if row.get(key) is not None and row.get(key) != '':
                        return row[key]
            return None
        refs = selected.get('references')
        if refs is None:
            refs = selected.get('reference_images') or selected.get('reference_provenance')
        if refs is None:
            refs = field('references', 'reference_images', 'reference_provenance')
        if refs is None:
            refs = []; warning.append('历史参考图顺序缺失；未用推荐参考图冒充实际参考图。')
        hashes = selected.get('reference_sha256', [])
        if hashes and refs and isinstance(refs[0], str):
            refs = [dict(path=p, sha256=hashes[i] if i < len(hashes) else None) for i, p in enumerate(refs)]
        refs = [self.resolve_reference(x) for x in refs]
        if any(not x['exists'] for x in refs):
            warning.append('存在缺失参考图，请补齐后再提交。')
        if any(x.get('hash_matches') is False for x in refs):
            warning.append('参考图内容与当时哈希不一致；沿用路径不代表沿用原图。')
        if not prompt and image:
            txt = Path(image).with_suffix('.txt')
            if txt.is_file():
                prompt = txt.read_text('utf-8-sig'); prompt_source = str(txt)
                warning.append('仅找到配套 TXT；它可能是图片描述，不是实际 API 提示词。')
        model = field('model'); base = field('api_base', 'base_url')
        historical_parameters={'model':model,'api_base':base,'size':field('size_requested','size'),'quality':field('quality','quality_requested'),'mode':field('mode')}
        builtin = str(base).startswith('built-in:') or str(model).startswith('built-in ')
        if builtin:
            warning.append('该版本最后由内置图像工具返修，没有可调用的第三方API地址和已知模型。界面改用老接口/gpt-image-2作为新请求默认值，不能复现内置工具服务。')
            model = None; base = None
        if base and not str(base).startswith(('https://', 'http://')):
            base = None
        size = field('size_requested', 'size'); quality = field('quality', 'quality_requested')
        missing = [n for n, v in [('模型',model), ('接口',base), ('尺寸',size), ('质量',quality)] if not v]
        if missing:
            warning.append('历史参数缺失：' + '、'.join(missing) + '。界面显示的值为可修改默认值。')
        review = field('anatomy_review_status')
        if review == 'needs_prompt_revision':
            warning.append('该图在最近人体复核中仍待返修；导入不代表质量通过。')
        profile = field('credential_label') or ''
        if profile.startswith('user-'):
            import settings
            profile=profile if settings.get(profile) else 'custom'
        elif 'native' in profile or '原生' in profile: profile = 'sraiapi-native'
        elif 'newtransfer' in str(base): profile = 'newtransfer'
        elif 'upscale' in profile or '超分' in profile: profile = 'sraiapi-upscale'
        else:
            profile = 'sraiapi-upscale' if base and 'sraiapi.com' in base else 'custom'
            warning.append('未记录凭据类型；当前凭据选择只是默认建议。')
        mask = field('mask')
        if isinstance(mask,dict):
            maskinfo=self.resolve_reference(mask)
            mask=maskinfo['path']
            if not maskinfo['exists'] or maskinfo.get('hash_matches') is False:warning.append('原始蒙版缺失或内容改变，请核对后再提交。')
        result = dict(image=image, prompt=prompt, prompt_source=prompt_source,
                      prompt_verified=prompt_verified, references=refs, mask=mask or '',
                      model=model or 'gpt-image-2', api_base=base or 'https://sraiapi.com/v1',
                      size=size or '1024x1536', quality=quality or 'high', profile=profile,
                      mode='edit' if refs or mask else 'generate', warnings=warning,
                      records=[x['_record'] for x in rows], review=review,
                      historical_parameters=historical_parameters,
                      modules=field('modules') or {}, negative_modules=[],
                      restoration='已找到历史请求' if prompt_source and rows else '记录不完整')
        if image and Path(image).is_file(): result['image_sha256'] = sha(image)
        return result

    def import_image(self, path=None, content_hash=None):
        p = Path(path) if path else None
        if p and not p.is_file(): raise ValueError('导入图片不存在。')
        h = content_hash or (sha(p) if p else None)
        rows = list(self.by_hash.get(h, []))
        # Older role records have no output hash, but are linked to an inventory image
        # whose hash does match. Preserve that link for renamed/uploaded copies too.
        for row in list(rows):
            for key in ('image','target','saved_path'):
                if row.get(key):
                    extra=self.by_path.get(str(Path(row[key]).resolve()).lower(),[])
                    rows += [x for x in extra if x not in rows and (not x.get('sha256') or x.get('sha256')==h)]
        # A copied image with matching bytes can restore provenance independent of its filename.
        if p:
            extra = self.by_path.get(str(p.resolve()).lower(), [])
            extra = [x for x in extra if not x.get('sha256') or x.get('sha256') == h]
            rows += [x for x in extra if x not in rows]
        return self.unpack(rows, str(p) if p else None)

    def import_task(self, record, task_id):
        p = Path(record).resolve()
        if not p.is_relative_to(self.root) or p.name != '任务.json': raise ValueError('任务记录路径无效。')
        data = read_json(p, {})
        row = next((x for x in data.get('tasks', []) if x.get('id') == task_id), None)
        if not row: raise ValueError('未找到该任务。')
        row = dict(row, _record=str(p), _priority=40,
                   model=row.get('model') or data.get('model'),
                   api_base=data.get('provider', 'https://sraiapi.com/v1'),
                   credential_label=data.get('credential_label', ''))
        return self.unpack([row])
