"""Read-only source inventory. No API calls, model loading or asset changes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
CODE = {
    'manager': [
        'apps/desktop/package.json', 'apps/desktop/src/App.tsx',
        'apps/desktop/src-tauri/src/commands/comfy.rs',
        'apps/desktop/src-tauri/src/comfy/types.rs',
        'apps/desktop/src-tauri/src/commands/training.rs',
        'apps/desktop/src-tauri/src/database/schema.rs',
        'apps/ai-worker/src/ai_worker/similarity.py',
        'apps/ai-worker/src/ai_worker/personal_model.py',
        'apps/ai-worker/src/ai_worker/dataset.py', 'LICENSE',
    ],
    'agent': [
        '手动生图程序/desktop_qt.py', '手动生图程序/api_client.py',
        '手动生图程序/request_codec.py', '手动生图程序/workbench.py',
        '手动生图程序/status_storage.py', '手动生图程序/agent/context.py',
        '手动生图程序/agent/runtime.py', '手动生图程序/agent/tools.py',
        '手动生图程序/agent/store.py', '手动生图程序/agent/repair.py',
        '手动生图程序/agent/transfer.py', '生图经验.md',
    ],
}


def file_record(root: Path, name: str) -> dict:
    path = root / name
    if not path.is_file() or not path.resolve().is_relative_to(root):
        return {'relative_path': name, 'exists': False}
    return {'relative_path': name, 'exists': True, 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def role_counts(root: Path, folder: str) -> list[dict]:
    base = root / folder
    if not base.is_dir():
        return []
    rows = []
    for role in sorted(base.iterdir()):
        if not role.is_dir() or role.is_symlink() or role.name == 'other' or role.name.lower().startswith('temp'):
            continue
        rows.append({'role': role.name, 'top_level_images': sum(
            p.is_file() and not p.is_symlink() and p.suffix.lower() in IMAGE_SUFFIXES
            for p in role.iterdir())})
    return rows


def agent_counts(root: Path) -> dict:
    path = root / 'other/agent/agent.sqlite3'
    if not path.is_file():
        return {'exists': False}
    # Read-only SQLite URI, a single consistent read transaction. No schema changes.
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('BEGIN')
        present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tables = ('chats', 'tasks', 'events', 'calls', 'plans', 'reviews', 'repair_masks')
        return {'exists': True, 'counts': {
            name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
            for name in tables if name in present}}
    finally:
        db.close()


def inspect(manager: Path, agent: Path) -> dict:
    manager, agent = manager.resolve(), agent.resolve()
    if not manager.is_dir() or not agent.is_dir():
        raise ValueError('Both source directories must exist.')
    git = subprocess.run(['git', '-C', str(manager), 'rev-parse', 'HEAD'],
                         capture_output=True, text=True, timeout=10)
    return {
        'format': 'charaforge-readonly-source-inventory', 'version': 1,
        'created': datetime.now().astimezone().isoformat(),
        'mode': 'read_only', 'api_calls': 0, 'images_moved': 0,
        'sources': {
            'manager': {'root': str(manager), 'head': git.stdout.strip() if git.returncode == 0 else None,
                        'files': [file_record(manager, name) for name in CODE['manager']]},
            'agent': {'root': str(agent), 'files': [file_record(agent, name) for name in CODE['agent']]},
        },
        'gallery': role_counts(agent, '角色图集'),
        'references': role_counts(agent, '角色特征及参考图'),
        'modules': {name: sum(p.is_file() for p in (agent / name).glob('*.txt'))
                    for name in ('姿势', '场景', '面部表情', '质量约束nagetive')},
        'agent_database': agent_counts(agent),
        'limits': ['Counts cover role top-level images only, not candidates or all nested assets.',
                   'No credentials, API config, images or message bodies are read; selected source/experience files are hashed, not exported as raw content.',
                   'Source code hashes detect changes; this is not a full migration or backup.'],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager', type=Path, required=True)
    parser.add_argument('--agent', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    output = args.out.resolve()
    sources = (args.manager.resolve(), args.agent.resolve())
    if any(output.is_relative_to(source) for source in sources):
        raise ValueError('Write the inventory in the new project, outside both source projects.')
    result = inspect(*sources)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'gallery_roles': len(result['gallery']),
                      'gallery_top_level_images': sum(r['top_level_images'] for r in result['gallery']),
                      'api_calls': 0, 'images_moved': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
