"""真实美心三文件冒烟：直接调用 ApiBundleService（不经 HTTP）。

用法：
    python scripts/mx_bundle_smoke.py            # 只解析，不确认导入
    python scripts/mx_bundle_smoke.py --confirm  # 解析 + 确认导入
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

MX_DIR = Path(r"D:\测试管理\项目管理\美心项目\美心POS\API")
EXCEL = MX_DIR / "YX CRM API Spec For Non POS (1).xlsx"
COLLECTION = MX_DIR / "美心.postman_collection.json"
ENVIRONMENT = MX_DIR / "UAT_eatizen HK CPO.postman_environment.json"

BUNDLE_NAME = "美心 CRM Non POS（冒烟）"


async def main(do_confirm: bool) -> int:
    from sqlalchemy import select

    from app.config.database import async_session_factory
    from app.models.project import Project
    from app.services.api_bundle_service import ApiBundleService, SourceInput

    for path in (EXCEL, COLLECTION, ENVIRONMENT):
        if not path.exists():
            print(f"缺少样本文件: {path}")
            return 2

    async with async_session_factory() as session:
        project = (await session.execute(
            select(Project).where(Project.identifier == "PR-1")
        )).scalar_one_or_none()
        if project is None:
            print("项目 PR-1 不存在")
            return 2

        service = ApiBundleService(session)
        bundle = await service.create_bundle(
            project_id=project.id, name=BUNDLE_NAME, system_type="crm_non_pos",
            created_by="smoke",
        )
        print(f"bundle: {bundle.id} (status={bundle.status})")

        sources = [
            SourceInput("excel_mx", EXCEL.name, EXCEL.read_bytes()),
            SourceInput("postman_collection", COLLECTION.name, COLLECTION.read_bytes()),
            SourceInput("postman_environment", ENVIRONMENT.name, ENVIRONMENT.read_bytes()),
        ]
        outcome = await service.parse_bundle(bundle, sources)
        await session.commit()

        print("\n=== parse-bundle ===")
        print("status:", outcome.status)
        print("stats:", outcome.stats)
        print("sources:", len(outcome.sources))
        print("contracts:", len(outcome.contracts))
        print("conflicts:", len(outcome.conflicts))
        if outcome.errors:
            print("errors:", outcome.errors)
        if outcome.warnings:
            print("warnings:", outcome.warnings)

        sev: dict[str, int] = {}
        for c in outcome.conflicts:
            sev[c["severity"]] = sev.get(c["severity"], 0) + 1
        print("conflicts by severity:", sev)

        print("\n样例契约:")
        for c in outcome.contracts[:5]:
            print(f"  {c['method']} {c['normalized_path']} | {c['name']} | sources={c['source_types']} | pending={c['pending_count']}")

        if not do_confirm:
            return 0

        print("\n=== confirm ===")
        from app.services.api_bundle_service import ConflictNotConfirmedError

        try:
            result = await service.confirm(bundle)
            await session.commit()
        except ConflictNotConfirmedError as exc:
            print(f"存在 {len(exc.conflicts)} 个未确认高等级冲突，先确认后再导入：")
            for c in exc.conflicts[:5]:
                print(f"  [{c['severity']}] {c['conflict_type']}: {c['message']}")
            print("\n自动确认这些冲突以便冒烟继续 ...")
            await service.confirm_conflicts(
                bundle,
                [{"conflict_id": c["id"], "resolution": "accept", "note": "冒烟自动确认"}
                 for c in exc.conflicts],
            )
            await session.commit()
            result = await service.confirm(bundle)
            await session.commit()

        print("imported:", len(result.imported))
        print("skipped:", len(result.skipped))
        print("folder_ids:", result.folder_ids)

        # 幂等复跑
        print("\n=== confirm 二次执行（幂等校验）===")
        again = await service.confirm(bundle)
        await session.commit()
        print("imported:", len(again.imported), "skipped:", len(again.skipped))
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.confirm)))
