"""
IDP 缺陷登记验证脚本

使用 lianqiao.ad 账号验证缺陷登记流程
执行步骤：
1. 验证 Token 和项目访问
2. 模拟测试失败场景
3. 触发 IDP 缺陷登记（dry-run 模式）
4. 输出验证结果

用法:
    cd d:/code/Pyproject/ai-test-agent-system-platform
    PYTHONPATH=backend python backend/scripts/verify_idp_defect_creation.py
"""

import asyncio
import sys
from datetime import datetime, timezone
from uuid import uuid4

# 添加项目路径
sys.path.insert(0, "backend")

from app.config.settings import settings
from app.services.idp_auth_service import IDPAuthService
from app.services.idp_client import IDPClient
from app.services.idp_project_resolver import IDPProjectResolver
from app.services.defect_decision_service import DefectDecisionService, DefectDecision, DefectPriority
from app.services.defect_fingerprint import DefectFingerprintService


# 配置 Token（lianqiao.ad 账号）
settings.idp_token = "b5b30659-6139-46c1-bacc-8b55675b7aad"
settings.idp_dry_run = True  # 先使用 dry-run 验证
settings.idp_auto_create_enabled = True


async def verify_token():
    """验证 Token 有效性"""
    print("=" * 60)
    print("步骤 1: 验证 Token")
    print("=" * 60)

    try:
        token = await IDPAuthService.get_token()
        masked = token[:8] + "..." + token[-4:] if len(token) > 12 else "***"
        print(f"✅ Token 有效: {masked}")

        # 验证用户信息
        client = IDPClient()
        # 通过项目查询验证 Token 权限
        project = await client.get_project_info(1169)
        print(f"✅ 项目 1169 访问成功: {project.get('projectCode')}")
        return True
    except Exception as e:
        print(f"❌ Token 验证失败: {e}")
        return False


async def verify_project_mapping():
    """验证项目映射"""
    print()
    print("=" * 60)
    print("步骤 2: 验证项目映射")
    print("=" * 60)

    mapping = IDPProjectResolver.resolve("PR-2")
    if mapping:
        print(f"✅ 项目映射成功:")
        print(f"   本地项目: PR-2 → IDP 项目: {mapping.idp_project_id}")
        print(f"   IDP 项目名: {mapping.idp_project_name}")
        print(f"   问题类型: {mapping.issue_type_id} ({mapping.type_code})")
        print(f"   默认优先级: {mapping.default_priority_code}")
        print(f"   默认指派人: {mapping.default_assignee_id}")
        return True
    else:
        print("❌ 项目映射失败: PR-2 未找到或已禁用")
        return False


async def simulate_defect_creation():
    """模拟缺陷创建流程"""
    print()
    print("=" * 60)
    print("步骤 3: 模拟缺陷创建 (dry-run)")
    print("=" * 60)

    # 模拟测试失败场景
    test_scenarios = [
        {
            "name": "canRefund字段缺失",
            "endpoint": "/api/icdp/v1/memberCard/account/financeRecord/page",
            "method": "POST",
            "status_code": 200,
            "error": "响应中缺少 canRefund 字段",
            "priority": DefectPriority.MEDIUM,
        },
        {
            "name": "pageNum=9999返回500",
            "endpoint": "/api/icdp/v1/memberCard/account/financeRecord/page",
            "method": "POST",
            "status_code": 500,
            "error": "分页参数 pageNum=9999 返回 500 Internal Server Error",
            "priority": DefectPriority.MEDIUM,
        },
        {
            "name": "退款金额计算错误",
            "endpoint": "/api/icdp/v1/memberCard/storage-free-order/refund/calculate",
            "method": "POST",
            "status_code": 200,
            "error": "可退金额大于实付金额: refundableAmount(100) > actualPaidAmount(50)",
            "priority": DefectPriority.HIGH,
        },
    ]

    client = IDPClient()
    mapping = IDPProjectResolver.resolve("PR-2")

    for i, scenario in enumerate(test_scenarios, 1):
        print()
        print(f"--- 场景 {i}: {scenario['name']} ---")

        # 1. 缺陷决策
        decision = DefectDecisionService.decide(
            test_status="failed",
            error_message=scenario["error"],
            response_status_code=scenario["status_code"],
            expected_status_code=200,
        )
        print(f"决策结果: {decision.decision.value}")
        print(f"优先级: {decision.priority.value} ({decision.priority_code})")

        # 2. 生成指纹
        fingerprint = DefectFingerprintService.generate(
            source_project_key="PR-2",
            method=scenario["method"],
            url=scenario["endpoint"],
            error_type=decision.error_type,
            failure_summary=decision.failure_summary,
        )
        print(f"缺陷指纹: {fingerprint[:16]}...")

        # 3. 生成标题
        title = f"【自动化测试】【API】【{scenario['name'].split('字段')[0]}】{scenario['error'][:50]}"
        if len(title) > 200:
            title = title[:197] + "..."
        print(f"缺陷标题: {title}")

        # 4. 生成描述
        description = json.dumps({
            "ops": [
                {"insert": "问题概述\n"},
                {"insert": f"{scenario['error']}\n\n"},
                {"insert": "测试类型\n"},
                {"insert": "API 自动化测试\n\n"},
                {"insert": "业务项目\n"},
                {"insert": "PR-2 (小杨生煎)\n\n"},
                {"insert": "Method / URL\n"},
                {"insert": f"{scenario['method']} {scenario['endpoint']}\n\n"},
                {"insert": "响应状态码\n"},
                {"insert": f"{scenario['status_code']}\n\n"},
                {"insert": "reqid\n"},
                {"insert": f"dry-run-test-{i}\n\n"},
                {"insert": "优先级依据\n"},
                {"insert": f"{decision.reason}\n"},
            ]
        }, ensure_ascii=False)

        # 5. 构建创建请求
        issue_data = {
            "summary": title,
            "description": description,
            "typeCode": mapping.type_code,
            "issueTypeId": mapping.issue_type_id,
            "priorityCode": decision.priority_code,
            "priorityId": decision.priority_id,
            "sprintId": mapping.default_sprint_id,
            "epicId": mapping.default_epic_id,
            "assigneeId": mapping.default_assignee_id,
        }

        # 6. 执行 dry-run 创建
        result = await client.create_issue_dry_run(mapping.idp_project_id, issue_data)
        print(f"✅ Dry-run 结果: {result['message']}")
        print(f"   模拟 Issue ID: {result['issue_id']}")
        print(f"   模拟 Issue Key: {result['issue_key']}")

    return True


async def verify_real_creation():
    """验证真实创建（可选）"""
    print()
    print("=" * 60)
    print("步骤 4: 真实创建验证")
    print("=" * 60)

    if settings.idp_dry_run:
        print("⚠️ 当前为 dry-run 模式，未实际创建缺陷")
        print()
        print("要执行真实创建，请设置:")
        print("  export IDP_DRY_RUN=false")
        print("  export IDP_AUTO_CREATE_ENABLED=true")
        print()
        print("然后重新运行此脚本")
        return False

    # 真实创建逻辑
    print("🚀 执行真实创建...")
    # ... 真实创建代码
    return True


async def main():
    """主验证流程"""
    print()
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 12 + "IDP 缺陷登记验证 (lianqiao.ad)" + " " * 14 + "║")
    print("╚" + "=" * 58 + "╝")
    print()
    print(f"时间: {datetime.now(timezone.utc).isoformat()}")
    print(f"账号: lianqiao.ad (连翘)")
    print(f"项目: PR-2 → 1169 (小杨生煎)")
    print(f"模式: {'dry-run' if settings.idp_dry_run else '真实创建'}")
    print()

    # 执行验证步骤
    step1 = await verify_token()
    if not step1:
        print("\n❌ 验证失败: Token 无效")
        return 1

    step2 = await verify_project_mapping()
    if not step2:
        print("\n❌ 验证失败: 项目映射错误")
        return 1

    step3 = await simulate_defect_creation()
    if not step3:
        print("\n❌ 验证失败: 缺陷创建模拟错误")
        return 1

    # 输出总结
    print()
    print("=" * 60)
    print("验证结果总结")
    print("=" * 60)
    print("✅ Token 验证通过 (lianqiao.ad)")
    print("✅ 项目映射验证通过 (PR-2 → 1169)")
    print("✅ 缺陷创建流程验证通过 (dry-run)")
    print()
    print("系统已准备好使用 lianqiao.ad 账号登记缺陷")
    print()
    print("下一步操作:")
    print("  1. 执行真实测试: npx playwright test tests/PR-2/")
    print("  2. 关闭 dry-run : export IDP_DRY_RUN=false")
    print("  3. 查看缺陷记录: GET /api/v2/idp-defects/test-runs/{run_id}")
    print()

    return 0


if __name__ == "__main__":
    import json  # 脚本内导入
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
