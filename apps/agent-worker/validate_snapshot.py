"""Validate snapshot hashes and local import structure without importing services."""
import ast
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
legacy = root / 'legacy'
manifest = json.loads((root / 'source-manifest.json').read_text('utf-8'))
declared = {row['file'] for row in manifest['sources']}
issues = []
for row in manifest['sources']:
    path = legacy / row['file']
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
        issues.append({'file': row['file'], 'reason': 'missing source or hash mismatch'})
        continue
    for node in ast.walk(ast.parse(path.read_text('utf-8-sig'))):
        modules = []
        if isinstance(node, ast.Import):
            modules = [item.name for item in node.names if item.name.startswith('agent.')]
        elif isinstance(node, ast.ImportFrom) and node.module == 'agent':
            modules = ['agent.' + item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('agent.'):
            modules = [node.module]
        for module in modules:
            local = module.replace('.', '/') + '.py'
            if local not in declared:
                issues.append({'file': row['file'], 'reason': 'missing local module: ' + module})
actual = {str(p.relative_to(legacy)).replace('\\', '/') for p in legacy.rglob('*.py')}
if actual != declared:
    issues.append({'reason': 'manifest file set mismatch'})
print(json.dumps({'files': len(declared), 'issues': issues, 'api_calls': 0,
                  'service_imports': 0}, ensure_ascii=False))
raise SystemExit(1 if issues else 0)
