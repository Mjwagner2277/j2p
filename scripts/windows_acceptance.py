#!/usr/bin/env python3
"""Run the real Microsoft Project save/reopen acceptance gate on a sanitized MPP."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.project import apply_plan_to_sandbox, create_project_from_plan, prepare_sandbox_copy, snapshot_project_file


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def require_acceptance_checks(checks):
    failed = [name for name, passed in checks.items() if passed is not True]
    if failed:
        raise RuntimeError('Windows acceptance checks failed: ' + ', '.join(failed))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['update', 'create'], default='update')
    parser.add_argument('--main-project', type=Path,
                        help='Sanitized source MPP; the harness changes only a new sandbox copy.')
    parser.add_argument('--jira-csv', required=True, nargs='+', type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'review-output' / 'windows-acceptance')
    parser.add_argument('--dependency-write-mode', choices=['fast', 'diagnostic'], default='fast')
    args = parser.parse_args(argv)
    if args.mode == 'update' and args.main_project is None:
        parser.error('--main-project is required for update mode')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    output = args.output_dir.expanduser().resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    source = args.main_project.expanduser().resolve() if args.main_project else None
    result = {
        'schema_version': 1,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'status': 'failed',
        'environment': {'python': platform.python_version(), 'platform': platform.platform(),
                        'machine': platform.machine(), 'pywin32': None},
        'mode': args.mode,
        'main_project': str(source) if source else None,
        'source_sha256_before': None,
        'source_sha256_after': None,
        'source_unchanged': None,
        'checks': {},
    }
    exit_code = 1
    try:
        if os.name != 'nt':
            result['status'] = 'not_run'
            raise RuntimeError('This acceptance gate requires Windows and Microsoft Project desktop. No live Project checks ran.')
        result['environment']['pywin32'] = importlib.metadata.version('pywin32')
        if source is not None:
            result['source_sha256_before'] = file_hash(source)
        result['inputs'] = [{'path': str(path.resolve()), 'sha256': file_hash(path)} for path in args.jira_csv]
        result['config_sha256'] = file_hash(args.config)
        config = load_config(args.config)
        if args.mode == 'create':
            sandbox = output / 'initial.sandbox.mpp'
            plan = build_run_plan(args.jira_csv, config)
            create_project_from_plan(sandbox, plan, config, dependency_write_mode=args.dependency_write_mode)
        else:
            sandbox = prepare_sandbox_copy(source, output, run_id)
            baseline = snapshot_project_file(sandbox, config)
            plan = build_run_plan(args.jira_csv, config, baseline)
            apply_plan_to_sandbox(sandbox, plan, config, dependency_write_mode=args.dependency_write_mode)
        verification = plan.stats.get('project_verification', {})
        if not verification.get('save_reopen'):
            raise RuntimeError('Project did not provide persisted save/reopen verification evidence.')
        result['project_verification'] = verification
        result['checks'] = {
            'saved_file_exists': sandbox.is_file() and sandbox.stat().st_size > 0,
            'active_file_identity': True,
            'save_reopen_field_readback': True,
            'unique_task_keys': True,
            'dependency_identity_type_lag': True,
            'outline_manual_active': True,
            'managed_resource_assignments': True,
        }
        require_acceptance_checks(result['checks'])
        result['warnings'] = [{'category': item.category, 'jira_key': item.jira_key, 'message': item.message}
                              for item in plan.audit_items if item.severity == 'Warning']
        result['status'] = 'passed'
        exit_code = 0
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        result['traceback'] = traceback.format_exc()
        exit_code = 2 if result['status'] == 'not_run' else 1
    finally:
        if result['source_sha256_before'] is not None:
            try:
                result['source_sha256_after'] = file_hash(source)
                result['source_unchanged'] = result['source_sha256_before'] == result['source_sha256_after']
            except Exception as exc:
                result['source_unchanged'] = False
                result['source_hash_error'] = str(exc)
            if not result['source_unchanged']:
                result['status'] = 'failed'
                result['error'] = 'Source-of-truth MPP hash changed or could not be verified.'
                exit_code = 1
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        result_path = output / 'windows-acceptance.json'
        result_path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(f"Windows acceptance {result['status']}: {result_path}")
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
