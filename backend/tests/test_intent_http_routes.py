from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.v2.api_tests import router
from app.api.deps import get_current_active_user, get_db

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix='/api/v2')
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: SimpleNamespace(id='project-id'))))
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id='user-id')
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c, db

def test_compile_and_preview_over_http(client):
    c, _ = client
    response = c.post('/api/v2/projects/PR-9/api-tests/compile-intent', json={
        'natural_language': '测试登录接口 POST /auth/login，期望返回 200 和 token',
    })
    assert response.status_code == 200, response.text
    compiled = response.json()['data']
    assert compiled['success'] is True
    assert compiled['intent']['assertions']
    preview = c.post('/api/v2/projects/PR-9/api-tests/preview-config', json={'intent': compiled['intent']})
    assert preview.status_code == 200, preview.text
    assert preview.json()['data']['assertion_preview']

def test_blank_intent_rejected(client):
    c, _ = client
    assert c.post('/api/v2/projects/PR-9/api-tests/compile-intent', json={'natural_language':'  '}).status_code == 422

def test_missing_project_rejected(client):
    c, db = client
    db.execute.return_value.scalar_one_or_none = lambda: None
    assert c.post('/api/v2/projects/missing/api-tests/compile-intent', json={'natural_language':'测试登录'}).status_code == 404

def test_wrong_project_endpoint_rejected(client):
    c, db = client
    db.execute.side_effect = [SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id='p')), SimpleNamespace(scalar_one_or_none=lambda: None)]
    assert c.post('/api/v2/projects/PR-9/api-tests/compile-intent', json={'natural_language':'测试登录', 'endpoint_id':'00000000-0000-0000-0000-000000000001'}).status_code == 404
