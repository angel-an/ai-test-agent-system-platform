import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.execution.runner import PlaywrightRunner


def test_parse_playwright_json_report_includes_test_and_assertion_steps(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "suites": [{
            "title": "API suite",
            "specs": [{
                "title": "POST /api/v1/recharge basic contract",
                "tests": [{
                    "results": [{
                        "status": "passed",
                        "duration": 42,
                        "errors": [],
                        "steps": [{
                            "title": "断言：HTTP 响应状态为 2xx（实际：200）",
                            "duration": 3,
                        }],
                    }],
                }],
            }],
        }],
    }), encoding="utf-8")

    result = PlaywrightRunner._parse_playwright_json_report(report_path)

    assert result["total"] == 1
    assert result["passed"] == 1
    assert result["tests"][0]["title"] == "POST /api/v1/recharge basic contract"
    assert result["tests"][0]["steps"][0]["title"].endswith("实际：200）")
