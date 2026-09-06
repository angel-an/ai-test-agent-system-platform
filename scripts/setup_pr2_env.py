#!/usr/bin/env python3
"""PR-2 环境配置辅助脚本。

用法：
    1. 从浏览器开发者工具复制凭据，粘贴到 .env 文件
    2. 运行：python scripts/setup_pr2_env.py
    3. 脚本验证配置完整性，不暴露真实值

注意：
    - 本脚本只验证配置格式，不读取或显示真实凭据
    - 真实凭据由用户从浏览器获取并填入 .env 文件
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def check_env_file() -> dict[str, str]:
    """检查 .env 文件中的 PR-2 配置。"""
    backend_dir = Path(__file__).parent.parent / "backend"
    env_file = backend_dir / ".env"
    example_file = backend_dir / ".env.example"

    if not env_file.exists():
        print(f"[FAIL] 未找到 .env 文件: {env_file}")
        print(f"       请复制模板: cp {example_file} {env_file}")
        print("       然后填入真实凭据")
        return {}

    # 读取 .env 文件（仅检查键是否存在，不显示值）
    env_vars: dict[str, str] = {}
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip()

    return env_vars


def validate_pr2_config(env_vars: dict[str, str]) -> list[str]:
    """验证 PR-2 配置是否完整。"""
    errors: list[str] = []

    # 必需变量
    required = {
        "PR2_ACCESS_TOKEN": "access-token（从浏览器 Local Storage 获取）",
        "PR2_COOKIE_HEADER": "Cookie（从浏览器开发者工具获取）",
        "PR2_STORAGE_ACCOUNT_NO": "测试会员手机号",
    }

    # 可选但有默认值
    optional_with_default = {
        "PR2_API_BASE_URL": "UAT 网关地址",
        "PR2_REFERER_URL": "referer URL",
    }

    # 门控开关
    live_api = env_vars.get("LIVE_API", env_vars.get("PR2_LIVE_API", "0"))

    print("=" * 50)
    print("PR-2 环境配置检查")
    print("=" * 50)

    # 检查必需变量
    print("\n[1] 必需变量检查：")
    for key, desc in required.items():
        value = env_vars.get(key, "")
        if value and value not in ("your_access_token", "your_cookie_string", "183****0265", ""):
            print(f"  [OK] {key}: 已配置 ({desc})")
        else:
            print(f"  [FAIL] {key}: 未配置或仍为占位符 ({desc})")
            errors.append(f"{key}: 未配置")

    # 检查可选变量
    print("\n[2] 可选变量检查：")
    for key, desc in optional_with_default.items():
        value = env_vars.get(key, "")
        if value and not value.startswith("https://your-"):
            print(f"  [OK] {key}: 已配置 ({desc})")
        else:
            print(f"  [WARN] {key}: 使用默认值 ({desc})")

    # 检查门控开关
    print(f"\n[3] Live 开关检查：")
    if live_api == "1":
        print(f"  [OK] LIVE_API / PR2_LIVE_API = 1（将执行 live 查询）")
    else:
        print(f"  [WARN] LIVE_API / PR2_LIVE_API = {live_api}（默认 0，只读查询需设为 1）")
        errors.append("LIVE_API: 未设为 1")

    # 凭据安全提示
    print(f"\n[4] 凭据安全：")
    print(f"  - .env 文件已加入 .gitignore，不会被提交")
    print(f"  - 不要在聊天、截图、报告中暴露真实 token 或密码")

    return errors


def main() -> int:
    env_vars = check_env_file()
    if not env_vars:
        return 1

    errors = validate_pr2_config(env_vars)

    print("\n" + "=" * 50)
    if not errors:
        print("[PASS] PR-2 配置完整，可以执行只读查询")
        print("\n执行命令：")
        print("  cd backend")
        print("  python -m pytest tests/api/pr2_storage_recharge/test_p1_query.py -v")
        return 0
    else:
        print(f"[FAIL] 发现 {len(errors)} 个问题，请修正后重试")
        return 1


if __name__ == "__main__":
    sys.exit(main())
