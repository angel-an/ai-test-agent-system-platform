"""Read-only local audit, then optional real HTTP generation into new folders."""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

import httpx
from app.api.v2.ai import _build_minimal_cases
from app.services.requirement_structure import parse_requirement_document

DOCUMENTS = [
    ('PR-9', 'YL-主数据管理9', 'YL-主数据管理.pdf', Path('C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf')),
    ('PR-2', '订单评价(问卷调查)-复验3', '小杨生煎-订单评价(问卷调查)功能 (1).pdf', Path('D:/360Downloads/小杨生煎-订单评价(问卷调查)功能 (1).pdf')),
]

def audit(cases):
    problems = []
    for c in cases:
        name = c['name']
        cf = c.get('custom_fields') or {}
        if re.match(r'^\s*[abcABC][.、．]', name) or re.search(r'xxx|XX字段', name, re.I):
            problems.append(name)
        if not c.get('module') or not c.get('tags'):
            problems.append('missing metadata: ' + name)
    ids = Counter((c.get('custom_fields') or {}).get('requirement_rule_id') for c in cases)
    duplicate = {k: v for k, v in ids.items() if k and v > 1 and k.startswith(('IMP', 'LST'))}
    assert not problems, problems
    assert not duplicate, duplicate
    return {'count': len(cases), 'modules': dict(Counter(c['module'] for c in cases)),
            'pending': sum((c.get('custom_fields') or {}).get('confirmation_status') == 'pending' for c in cases)}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--http', action='store_true')
    args = parser.parse_args()
    results = []
    with httpx.Client(timeout=180) as client:
        for project, folder_name, filename, path in DOCUMENTS:
            content = path.read_bytes()
            structure = parse_requirement_document(filename, 'application/pdf', content)
            cases, gaps = _build_minimal_cases(structure.full_text, filename, structure=structure)
            result = {'document': filename, 'local': audit([c.model_dump(mode='json') for c in cases])}
            if project == 'PR-2':
                assert not any((c.custom_fields.get('requirement_rule_id') or '').startswith('IMP') for c in cases)
            if args.http:
                base = f'http://localhost:3000/api/v2/projects/{project}'
                response = client.post(base + '/folders', json={'name': folder_name, 'description': '统一质量修复后的真实文档复验，旧批次保留'})
                response.raise_for_status()
                folder = response.json()['data']
                folder_id = folder.get('id') or folder.get('identifier')
                print('created folder', project, folder_name, folder_id, flush=True)
                response = client.post(base + '/ai/generate-from-document', data={'folder_id': str(folder_id)}, files={'file': (filename, content, 'application/pdf')})
                response.raise_for_status()
                payload = response.json()
                assert payload.get('success'), payload.get('message')
                response = client.get(base + f'/folders/{folder_id}/test-cases', params={'page_size': 200})
                response.raise_for_status()
                stored = response.json()['data']
                result.update(folder_id=str(folder_id), folder=folder_name, persisted=audit(stored))
                assert len(stored) == len(payload['test_cases'])
                assert result['persisted'] == result['local'], 'Service output differs from latest local generator'
                result['pending_items'] = [{'name': c['name'], 'reason': c['custom_fields'].get('ambiguity_reason')} for c in stored if (c.get('custom_fields') or {}).get('confirmation_status') == 'pending']
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    Path('quality_final_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
