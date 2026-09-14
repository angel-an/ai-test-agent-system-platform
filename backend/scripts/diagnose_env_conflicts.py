"""诊断：env_missing（high）冲突为何未触发。"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from app.services.api_asset_matcher import APIAssetMatcher
from app.services.api_spec_normalizer import APISpecNormalizer
from app.services.excel_api_parser import ExcelAPIParser
from app.services.postman_collection_parser import PostmanCollectionParser
from app.services.postman_environment_parser import PostmanEnvironmentParser

from tests.sample_paths import MX_SAMPLE_DIR

MX = MX_SAMPLE_DIR
EXCEL = MX / "YX CRM API Spec For Non POS (1).xlsx"
COLLECTION = MX / "美心.postman_collection.json"
ENVIRONMENT = MX / "UAT_eatizen HK CPO.postman_environment.json"


def parse_excel():
    parser = ExcelAPIParser().load(EXCEL.read_bytes())
    guide = {}
    guide_title = None
    for name in parser.sheet_names():
        ws = parser.workbook[name]
        if parser.is_guide_sheet(ws):
            guide_title = ws.title
            guide = parser.parse_guide(ws)
            break
    normalizer = APISpecNormalizer()
    specs = []
    for name in parser.sheet_names():
        ws = parser.workbook[name]
        if ws.title == guide_title or parser.is_guide_sheet(ws):
            continue
        iface = parser.parse_interface_sheet(ws)
        spec = normalizer.to_unified_spec(iface, guide)
        if spec.get("path"):
            specs.append(spec)
    return specs


def main() -> int:
    specs = parse_excel()
    coll = PostmanCollectionParser().parse(COLLECTION.read_bytes())
    env = PostmanEnvironmentParser().parse(ENVIRONMENT.read_bytes())
    env_keys = sorted({v.key for v in env.variables})
    print(f"excel specs={len(specs)}  collection={len(coll.requests)}  env={len(env_keys)}")

    matcher = APIAssetMatcher()
    result = matcher.match(excel_specs=specs, collection=coll, environment_vars=env_keys)

    paired = [c for c in result.contracts if c.get("excel") and c.get("collection")]
    print(f"契约总数={len(result.contracts)}  双向配对={len(paired)}")
    print(f"仅 Excel={sum(1 for c in result.contracts if c.get('excel') and not c.get('collection'))}")
    print(f"仅 Collection={sum(1 for c in result.contracts if not c.get('excel') and c.get('collection'))}")

    var_re = re.compile(r"\{\{(\w+)\}\}")

    def refs_of(cand) -> set[str]:
        raw = getattr(cand, "raw", None)
        if raw is None:
            return set()
        out: set[str] = set()
        if raw.url_raw:
            out.update(var_re.findall(raw.url_raw))
        for h in raw.headers or []:
            out.update(var_re.findall(h.get("value", "")))
        body_raw = (raw.body or {}).get("raw", "") if raw.body else ""
        if body_raw:
            out.update(var_re.findall(body_raw))
        return out

    print("\n双向配对契约中的变量引用情况（缺失即应触发 high env_missing）:")
    env_set = set(env_keys)
    missing_any = False
    for key, contract in [(f"{c['method']} {c['normalized_path']}", c) for c in paired][:15]:
        for cand in contract["collection"]:
            missing = refs_of(cand) - env_set
            flag = "  <== 缺失" if missing else ""
            print(f"  {key[:70]:<70} refs={sorted(refs_of(cand))} missing={sorted(missing)}{flag}")
            if missing:
                missing_any = True

    print(f"\n是否应存在 env_missing: {missing_any}")
    conflicts = matcher.match(
        excel_specs=specs, collection=coll, environment_vars=[]
    ).conflicts
    from collections import Counter
    print("把环境变量清空后的冲突分布:", Counter(c.severity for c in conflicts))
    print("  env_missing 数量:", sum(1 for c in conflicts if c.conflict_type == "env_missing"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
