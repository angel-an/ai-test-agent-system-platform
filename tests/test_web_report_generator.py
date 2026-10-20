import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.utils.web_test_report_generator import generate_web_test_report_html


def test_generate_web_report_uses_self_reflect_result(tmp_path):
    result_data = {
        "summary": {"PASS": 7, "WARN": 0, "FAIL": 0},
        "results": [
            {"cp": "LOGIN", "name": "登录成功", "status": "PASS", "detail": ""},
            {
                "cp": "NAV",
                "name": "导航到 Membership Day Management",
                "status": "PASS",
                "detail": "#/customer/membershipdaylist",
            },
            {"cp": "CREATE", "name": "创建并保存自建记录", "status": "PASS", "detail": ""},
            {"cp": "VERIFY_CREATE", "name": "搜索命中自建记录", "status": "PASS", "detail": ""},
            {"cp": "EDIT", "name": "编辑自建记录", "status": "PASS", "detail": ""},
            {"cp": "DELETE", "name": "删除自建记录", "status": "PASS", "detail": ""},
            {"cp": "NO_TOUCH", "name": "未触碰他人数据", "status": "PASS", "detail": "仅操作自建记录"},
        ],
    }
    (tmp_path / "self_reflect_result.json").write_text(
        json.dumps(result_data, ensure_ascii=False),
        encoding="utf-8",
    )

    html = generate_web_test_report_html(
        sub_function_name="会员日管理 (Membership Day Management)",
        sub_function_id="38f4c40c-a555-4071-b69b-ce31aa4794b4",
        execution_result={
            "success": True,
            "return_code": 0,
            "duration": 55.62,
            "stdout": "",
            "stderr": "",
        },
        project_identifier="PR-9",
        report_dir=str(tmp_path),
    )

    assert "测试用例 (7 个)" in html
    assert "7 个" in html
    assert "全部通过" in html
    assert "CREATE" in html
    assert "EDIT" in html
    assert "DELETE" in html
    assert "无结构化测试数据" not in html
