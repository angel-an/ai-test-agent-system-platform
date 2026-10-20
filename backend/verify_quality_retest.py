"""Reconcile only this task's YL verification batch with current generator output."""
from pathlib import Path
import httpx
from app.services.requirement_structure import parse_requirement_document
from app.api.v2.ai import _build_minimal_cases

p = Path('C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf')
s = parse_requirement_document('YL-主数据管理.pdf', 'application/pdf', p.read_bytes())
cases, _ = _build_minimal_cases(s.full_text, 'YL-主数据管理.pdf', structure=s)
def identity(c):
    cf = c.custom_fields
    return (cf.get('requirement_rule_id'), c.keyword,
            str(cf.get('assertions', [])), str((cf.get('requirement_rule') or {}).get('source', {})))

expected = {identity(c): c for c in cases}
url = 'http://localhost:3000/api/v2/projects/PR-9'
with httpx.Client(timeout=60) as client:
    response = client.get(url + '/folders/c7edf16f-a05e-4a71-ab93-1e5d3137f751/test-cases', params={'page_size': 200})
    response.raise_for_status()
    actual = response.json()['data']
    assert len(actual) == len(cases)
    for item in actual:
        from app.schemas.test_case import TestCaseCreate
        key = identity(TestCaseCreate.model_validate(item))
        if key not in expected:
            raise RuntimeError('Source identity changed; regenerate into a new folder instead of overwriting')
        c = expected[key]
        body = c.model_dump(mode='json', include={'name', 'description', 'module', 'tags', 'custom_fields', 'test_case_steps'})
        result = client.patch(url + '/test-cases/' + item['identifier'], json=body)
        result.raise_for_status()
    verified = client.get(url + '/folders/c7edf16f-a05e-4a71-ab93-1e5d3137f751/test-cases', params={'page_size': 200}).json()['data']
    assert all(c['module'] and c['tags'] for c in verified)
    print('YL verified', len(verified), 'pending', sum(c['custom_fields'].get('confirmation_status') == 'pending' for c in verified))
