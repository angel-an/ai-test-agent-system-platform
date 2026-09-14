# -*- coding: utf-8 -*-
"""P0 测试基线清单（唯一权威口径）。

为什么需要它
------------
历史上同时存在多个"P0 基线数字"，来源不同、口径不同，容易误用：

- 638 / 642 / 649：不同轮次、不同命令、含或不含某些专项；
- 41：clean clone 内的**核心子集**（证明提交快照可运行）；
- 28 / 19：P1 与长期能力专项。

本清单把"P0 基线"固定为**一条命令**，并把 P1 归属的测试文件显式排除，
使报告不再出现口径混淆。`scripts/gen_p0_baseline.py` 会读取本清单、
执行命令、把结果（**含 commit 与数字**）写入 `P0-测试基线.md`。

维护约定
--------
1. 新增属于 P0 的测试文件 → 若在 `P0_ROOTS` 覆盖范围内则自动纳入；
   若在根目录新增，需加入 `P0_ROOT_FILES`；
2. 新增属于 P1 的测试文件 → 必须加入 `P1_EXCLUDED`，并说明原因；
3. **不要**在本文件或生成结果里写死测试数量 —— 数量由实际执行决定。
"""

#: P0 基线覆盖的测试根（目录/文件）
P0_ROOTS = [
    "tests/platform",
    "tests/api/test_api_bundle_import.py",
    "tests/test_api_asset_matcher.py",
    "tests/test_excel_api_parser.py",
]

#: 根目录下属于 P0 的测试文件（按交付关注点挑选，不含其它工作流）
P0_ROOT_FILES = [
    "tests/test_api_asset_models.py",
    "tests/test_postman_parsers.py",
    "tests/test_api_spec_generator.py",
]

#: P1 归属的测试文件（**必须排除**，等 P1 单独验收后另立基线）
P1_EXCLUDED = {
    "tests/api/test_contract_driven_p1.py":
        "P1 契约驱动生成 + 执行前环境门禁（未提交，留在工作区）",
    "tests/platform/test_collection_assertions_and_preflight.py":
        "P1/长期能力：Collection 断言提取 + 启动迁移预检（依赖 P1 的 settings 配置）",
}

#: clean clone 核心子集（证明"提交快照可运行"，**不与 P0 基线相加**）
CLONE_SUBSET = [
    "tests/platform/test_api_endpoint_security_shape.py",
    "tests/api/test_api_bundle_import.py",
]
