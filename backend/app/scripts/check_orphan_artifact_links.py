# -*- coding: utf-8 -*-
"""检测「成果物 → 实体」关联失效（孤儿成果物），并可生成重挂方案。

背景（真实案例 PR-12）
----------------------
成果物用 ``attachment.entity_id`` 指向端点 UUID。端点被**重新导入**（统一导入会创建
新行 → 新 UUID）或删除后，历史成果物的 ``entity_id`` 就指向了不存在的端点。
前端端点成果物面板按「当前端点 id」查询，于是这些成果物**在 UI 上完全不可见**，
且没有任何提示——表现为"这一页只有测试脚本，没有测试用例和测试报告"。

实测 PR-12：11 个端点中 9 个无成果物；成果物 49 条里 **29 条**（script 22 + plan 3 +
case 3 + report 1）指向 6 个**全库都不存在**的端点 id，只有 20 条 script 指向现存端点。
路径为 ``api_memLogin`` / ``api_memRegister`` 的端点确实还在，只是 id 换了
（旧 ``f317bb45…`` → 新 ``c5a1f7a9…``）。

本工具只读；修复方案用 ``--plan`` 输出，写入需显式 ``--apply``。
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter, defaultdict

from sqlalchemy import select

from app.config.database import async_session_factory
from app.models.api_endpoint import APIEndpoint
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.project import Project
from app.models.test_scenario import TestScenario

#: 需要检查关联的 API 成果物类型
API_ARTIFACT_TYPES = (
    AttachmentEntityType.API_TEST_PLAN,
    AttachmentEntityType.API_TEST_CASE,
    AttachmentEntityType.API_TEST_SCRIPT,
    AttachmentEntityType.API_TEST_RESULT,
    AttachmentEntityType.API_TEST_REPORT,
)

#: 从描述里解析"API 端点 <METHOD> <PATH> 的..."，用于重挂
_DESC_RE = re.compile(r"API\s+端点\s+(?P<method>[A-Z]+)\s+(?P<path>\S+)")


def _type_value(attachment: Attachment) -> str:
    t = attachment.entity_type
    return t.value if hasattr(t, "value") else str(t)


def _parse_target(attachment: Attachment) -> tuple[str | None, str | None]:
    """从描述里解析 (method, path)。"""
    desc = str(attachment.description or "")
    m = _DESC_RE.search(desc)
    if m:
        return m.group("method").upper(), m.group("path")
    return None, None


#: 从文件名解析 "api_<key>.spec.ts"，用于描述缺失时的兜底重挂
_FILENAME_RE = re.compile(r"^api_(?P<key>\w+)\.spec\.ts$")


def _propose_relink(
    attachment: Attachment,
    ep_by_project_path: dict[tuple, APIEndpoint],
    eps_by_project: dict,
) -> APIEndpoint | None:
    """为单条孤儿成果物推断可重挂的当前端点。

    依次尝试三条规则，命中即返回：
    1. 描述解析 METHOD+PATH，精确匹配当前端点；
    2. 文件名 ``api_<key>.spec.ts`` 在当前项目端点 path 中唯一包含 ``<key>``；
       唯一性必须校验——同名 key 命中多个端点（如 checkMobileDuplicate 同时有
       ``?key=xxxxx`` 和 ``.json`` 两个端点）时放弃，避免挂错；
    3. 报告类文件名/描述含端点 key 且唯一匹配（如 chart-result 报告）。
    """
    method, path = _parse_target(attachment)
    if method and path:
        ep = ep_by_project_path.get((attachment.project_id, method, path))
        if ep is not None:
            return ep

    project_eps = eps_by_project.get(attachment.project_id, [])
    m = _FILENAME_RE.match(attachment.file_name or "")
    if m:
        key = m.group("key")
        matches = [ep for ep in project_eps if key in ep.path]
        if len(matches) == 1:
            return matches[0]

    if (attachment.file_name or "").startswith("chart-result"):
        desc = str(attachment.description or "")
        token_match = re.search(r"(api_\w+)", desc)
        if token_match:
            token = token_match.group(1)
            token_hits = [e for e in project_eps if token in e.path]
            if len(token_hits) == 1:
                return token_hits[0]
    return None


async def scan(plan: bool, apply: bool) -> int:
    async with async_session_factory() as session:
        projects = {
            p.id: p.identifier
            for p in (await session.execute(select(Project))).scalars().all()
        }
        endpoints = (await session.execute(select(APIEndpoint))).scalars().all()
        scenarios = (await session.execute(select(TestScenario))).scalars().all()

        ep_by_id = {ep.id: ep for ep in endpoints}
        sc_by_id = {s.id: s for s in scenarios}
        ep_by_project_path: dict[tuple, APIEndpoint] = {}
        for ep in endpoints:
            ep_by_project_path.setdefault((ep.project_id, ep.method.upper(), ep.path), ep)

        rows = (
            await session.execute(
                select(Attachment).where(Attachment.entity_type.in_(API_ARTIFACT_TYPES))
            )
        ).scalars().all()

        orphan: list[Attachment] = []
        for a in rows:
            if a.entity_id in ep_by_id or a.entity_id in sc_by_id or a.entity_id == a.project_id:
                continue
            orphan.append(a)

        print(f"API 类成果物总数 = {len(rows)}")
        print(f"关联失效（entity_id 不是现存端点/场景/项目本身）= {len(orphan)}")
        if not orphan:
            print("\n未发现关联失效的成果物。")
            return 0

        by_proj = Counter(projects.get(a.project_id, str(a.project_id)) for a in orphan)
        print("\n=== 按项目分布 ===")
        for pid, c in by_proj.most_common():
            print(f"  {pid:16s} {c} 条")

        by_type = Counter(_type_value(a) for a in orphan)
        print("\n=== 按类型分布 ===")
        for t, c in by_type.most_common():
            print(f"  {t:20s} {c}")

        # 尝试给出重挂方案
        print("\n=== 可自动重挂的方案（描述 METHOD+PATH / 文件名 api_*.spec.ts 唯一匹配 / 报告 token 匹配）===")
        eps_by_project: dict = defaultdict(list)
        for ep in endpoints:
            eps_by_project[ep.project_id].append(ep)

        proposals: list[tuple[Attachment, APIEndpoint]] = []
        unmatched: list[Attachment] = []
        for a in orphan:
            ep = _propose_relink(a, ep_by_project_path, eps_by_project)
            if ep is None:
                unmatched.append(a)
                continue
            proposals.append((a, ep))

        grouped: dict[tuple, list[Attachment]] = defaultdict(list)
        for a, ep in proposals:
            grouped[(projects.get(a.project_id), ep.display_name, str(ep.id))].append(a)
        for (pid, disp, eid), items in sorted(grouped.items(), key=lambda x: -len(x[1])):
            print(f"  {pid} → {disp}  (新 id {eid[:8]}…)：{len(items)} 条")
            for a in items:
                print(f"      [{_type_value(a):18s}] {a.file_name}")

        print(f"\n  可重挂 = {len(proposals)} 条；无法匹配当前端点 = {len(unmatched)} 条")
        if unmatched:
            print("  --- 无法匹配的（端点可能已被彻底删除）---")
            for a in unmatched[:20]:
                print(f"      [{_type_value(a):18s}] {a.file_name}  desc={str(a.description)[:56]}")

        if not (plan or apply):
            print("\n（只读扫描。加 --plan 查看方案，加 --apply 执行重挂。）")
            return 0

        if apply:
            print(f"\n开始重挂 {len(proposals)} 条 …")
            for a, ep in proposals:
                a.entity_id = ep.id
            await session.commit()
            print("已提交。")
        else:
            print("\n（--plan：以上为方案，未写入。）")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检测并重挂关联失效的 API 成果物")
    parser.add_argument("--plan", action="store_true", help="输出重挂方案（不写入）")
    parser.add_argument("--apply", action="store_true", help="执行重挂（写库，默认不执行）")
    args = parser.parse_args(argv)
    return asyncio.run(scan(args.plan, args.apply))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
