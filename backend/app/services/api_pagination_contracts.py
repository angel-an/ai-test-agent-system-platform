"""P5 分页契约配置（DEFECT-002）。

## 这个模块解决什么

平台生成的 API 执行脚本原先只断言 HTTP 2xx。小杨生煎的 SUT 是**信封式响应**：
业务成功/失败 HTTP 恒为 200，靠响应体 ``resultCode`` 区分。因此那个断言对业务结果
**恒真**，等于没有断言 —— DEFECT-002（分页越界返回 ``data:null``）在平台自动执行下
必然漏检。

本模块把"哪个接口、怎么调、越界页期望什么"写成**数据表**，供 ``api_case_link_service``
生成契约断言。数据来源是手跑矩阵 ``workspace/api/tests/p5_pagination_contract_matrix.spec.ts``
（2026-09-30 14:54 实测，9 个接口）。

## 为什么不把逻辑写在这里

判定逻辑**唯一权威副本**是 ``workspace/api/_lib/response_contract.ts`` 的
``RESPONSE_CONTRACT_CORE`` 段（已被 ``test_api_response_contract_node.py`` 覆盖）。
本模块只负责**按标记抽取**该段，供生成器内联到脚本里。

这样做是因为：手跑矩阵与平台生成脚本必须用**同一份**判定逻辑，否则"平台结论与手跑一致"
这个验收标准无法成立 —— 两份实现迟早漂移，而且漂移时会表现为"某边悄悄变宽/变严"，
正是 DEFECT-002 这类缺陷最容易藏身的地方。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 契约判定逻辑的权威来源（TS）。Python 侧**不重写**，只按标记抽取。
CONTRACT_CORE_TS = (
    Path(__file__).resolve().parents[2]
    / "workspace" / "api" / "_lib" / "response_contract.ts"
)
CONTRACT_CORE_BEGIN = "// >>> RESPONSE_CONTRACT_CORE_BEGIN"
CONTRACT_CORE_END = "// <<< RESPONSE_CONTRACT_CORE_END"

#: 参数出处。``unverified`` 表示参数未确认 —— 该行**显式 skip**，绝不静默通过。
PROVENANCE_VALUES = frozenset({"ui_observed", "generic_params", "unverified"})
#: 越界页期望。``structure`` = 越界也必须是完整空结构；``known_issue`` = 现状为 data:null，
#: 绑定缺陷单（失败但不谎报，缺陷修复后自动转绿）。
OUT_OF_RANGE_VALUES = frozenset({"structure", "known_issue"})


@dataclass(frozen=True)
class PaginationContract:
    """一个接口的分页契约声明。

    ★ ``provenance="unverified"`` 时**不生成契约断言**，只生成显式 ``test.skip``：
    参数没确认过的接口，"测出来通过"是没有意义的结论，比不跑更危险。
    """

    endpoint_path: str
    provenance: str
    expect_out_of_range: str
    known_issue_id: str = ""
    known_issue_until: str = ""
    #: 该接口所属 SUT 的响应信封字段名（本表 9 条都是数盈/小杨生煎系 → resultCode）。
    #: 按接口声明而不是写死在渲染里：换 SUT（如 MX/Aigens 系用 `Code`）时只改这一处数据。
    envelope_field: str = "resultCode"
    page_num_param: str = "pageNum"
    page_size_param: str = "pageSize"
    page_size: int = 10
    out_of_range_page: int = 999999
    note: str = ""

    def __post_init__(self) -> None:
        if self.provenance not in PROVENANCE_VALUES:
            raise ValueError(
                f"未知 provenance={self.provenance!r}（可选：{sorted(PROVENANCE_VALUES)}）"
            )
        if self.expect_out_of_range not in OUT_OF_RANGE_VALUES:
            raise ValueError(
                f"未知 expect_out_of_range={self.expect_out_of_range!r}"
                f"（可选：{sorted(OUT_OF_RANGE_VALUES)}）"
            )
        if self.expect_out_of_range == "known_issue":
            # 绑不到缺陷单的 known_issue 会变成"永久豁免"：没人知道该在什么修完之后撤掉它。
            # 设计文档把这条列为风险项，这里在**配置期**就拦住（fail-loud）。
            if not self.known_issue_id.strip():
                raise ValueError(
                    f"{self.endpoint_path}: expect_out_of_range=known_issue 必须提供 known_issue_id"
                )
            if not self.known_issue_until.strip():
                raise ValueError(
                    f"{self.endpoint_path}: known_issue 必须提供 known_issue_until"
                    "（否则例外永远有效，缺陷修复后不会转绿）"
                )
        if self.page_size <= 0:
            raise ValueError(f"{self.endpoint_path}: page_size 必须为正整数")
        if self.out_of_range_page <= 0:
            raise ValueError(f"{self.endpoint_path}: out_of_range_page 必须为正整数")

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_path": self.endpoint_path,
            "provenance": self.provenance,
            "expect_out_of_range": self.expect_out_of_range,
            "known_issue_id": self.known_issue_id,
            "known_issue_until": self.known_issue_until,
            "envelope_field": self.envelope_field,
            "page_num_param": self.page_num_param,
            "page_size_param": self.page_size_param,
            "page_size": self.page_size,
            "out_of_range_page": self.out_of_range_page,
            "note": self.note,
        }


#: 2026-09-30 14:54 实测矩阵。新增接口 = 加一行；测不到的接口不写进来（不是写进来 skip）。
PAGINATION_CONTRACTS: tuple[PaginationContract, ...] = (
    # ---- DEFECT-002 主体：5 个接口确认越界返回 data:null ----
    PaginationContract(
        endpoint_path="/v1/purchaseLimit/list", provenance="ui_observed",
        expect_out_of_range="known_issue", known_issue_id="DEFECT-002",
        known_issue_until="2026-12-31",
        note="正常页 list=9/total=9/pages=1；越界返回 data:null",
    ),
    PaginationContract(
        endpoint_path="/v1/purchase/list", provenance="ui_observed",
        expect_out_of_range="known_issue", known_issue_id="DEFECT-002",
        known_issue_until="2026-12-31",
        note="正常页 list=8/total=8/pages=1；越界返回 data:null",
    ),
    PaginationContract(
        endpoint_path="/v1/common/getCloudGoods", provenance="generic_params",
        expect_out_of_range="known_issue", known_issue_id="DEFECT-002",
        known_issue_until="2026-12-31",
        note="正常页 list=10/total=134/pages=14；越界返回 data:null",
    ),
    PaginationContract(
        endpoint_path="/v1/common/getPayMember", provenance="generic_params",
        expect_out_of_range="known_issue", known_issue_id="DEFECT-002",
        known_issue_until="2026-12-31",
        note="正常页 list=2/total=2/pages=1；越界返回 data:null",
    ),
    PaginationContract(
        endpoint_path="/v1/common/getRecommendGoods", provenance="generic_params",
        expect_out_of_range="known_issue", known_issue_id="DEFECT-002",
        known_issue_until="2026-12-31",
        note="正常页 list=10/total=123/pages=13；越界返回 data:null",
    ),
    # ---- 参照组：行为正确，作为"是 SUT 不一致而非平台能力不足"的反向证据 ----
    PaginationContract(
        endpoint_path="/v1/store/binding/list", provenance="generic_params",
        expect_out_of_range="structure",
        note="参照组：越界仍返回 list=[] 且 total=413/pages=42 保持正确",
    ),
    PaginationContract(
        endpoint_path="/v1/notice/page", provenance="generic_params",
        expect_out_of_range="structure",
        note="参照组：越界仍返回 list=[]（pages 字段存在）",
    ),
    # ---- 参数未生效：比 data:null 更隐蔽，"测不到"必须显式呈现 ----
    PaginationContract(
        endpoint_path="/v1/recommend/list", provenance="unverified",
        expect_out_of_range="structure",
        note="pageNum/pageSize 未生效（响应 pageNum=0/pageSize=0），参数待确认",
    ),
    PaginationContract(
        endpoint_path="/v1/slide/page", provenance="unverified",
        expect_out_of_range="structure",
        note="pageNum/pageSize 未生效（响应 pageNum=0/pageSize=0），参数待确认",
    ),
)


def _validate_contracts(contracts: tuple[PaginationContract, ...]) -> None:
    """配置期自检：重复路径、以及互为后缀造成的匹配歧义，都要在导入时就暴露。"""
    paths = [c.endpoint_path for c in contracts]
    duplicates = {p for p in paths if paths.count(p) > 1}
    if duplicates:
        raise ValueError(f"分页契约配置存在重复路径：{sorted(duplicates)}")
    for outer in paths:
        for inner in paths:
            if outer != inner and outer.endswith(inner):
                raise ValueError(
                    f"分页契约路径互为后缀，匹配将产生歧义：{outer!r} 以 {inner!r} 结尾"
                    "（无法确定该用哪一条）"
                )


_validate_contracts(PAGINATION_CONTRACTS)


def resolve_pagination_contract(path: str | None) -> PaginationContract | None:
    """按接口路径解析分页契约。

    匹配规则：**精确相等，或路径以契约路径结尾**。

    为什么要支持后缀匹配：OpenAPI 里同一个接口的 path 可能带网关注册前缀
    （实测形态如 ``/yundt-saas-gateway/api/mini-mgmt-app/v1/purchaseLimit/list``），
    而契约表按业务路径声明。只做精确匹配会漏掉一整类真实接口 —— 漏掉的表现是
    "安安静静地只跑 HTTP 断言"，正是本次要消灭的漏检形态。

    配置期已保证不存在互为后缀的路径（见 :func:`_validate_contracts`），
    因此后缀匹配结果唯一。多条同时命中时**抛错而不是猜**：猜错会把结论张冠李戴。
    """
    if not path:
        return None
    normalized = path.strip()
    if not normalized:
        return None

    exact = [c for c in PAGINATION_CONTRACTS if c.endpoint_path == normalized]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:  # pragma: no cover - 被 _validate_contracts 挡住
        raise ValueError(f"路径 {normalized!r} 精确命中多条分页契约：{[c.endpoint_path for c in exact]}")

    suffix = [c for c in PAGINATION_CONTRACTS if normalized.endswith(c.endpoint_path)]
    if not suffix:
        return None
    if len(suffix) > 1:  # pragma: no cover - 被 _validate_contracts 挡住
        raise ValueError(
            f"路径 {normalized!r} 后缀命中多条分页契约：{[c.endpoint_path for c in suffix]}"
        )
    return suffix[0]


@lru_cache(maxsize=1)
def load_contract_core() -> str | None:
    """抽取 ``response_contract.ts`` 的核心判定段（去掉行首 ``export``）。

    抽取而非重写：判定逻辑必须与手跑矩阵**同源**，否则两处会各自演化。

    返回 ``None`` 表示拿不到核心段（文件缺失/标记丢失）。调用方据此**降级**为
    仅信封断言 —— 不抛异常，因为这是"少做一层断言"，不是"得出错误结论"。
    但降级会记 ``error`` 日志，且生成脚本里会留下显式注解，避免静默消失。
    """
    try:
        text = CONTRACT_CORE_TS.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("分页契约核心段读取失败，将降级为仅信封断言：%s", exc)
        return None
    if CONTRACT_CORE_BEGIN not in text or CONTRACT_CORE_END not in text:
        logger.error(
            "分页契约核心段标记缺失（%s / %s），将降级为仅信封断言：%s",
            CONTRACT_CORE_BEGIN, CONTRACT_CORE_END, CONTRACT_CORE_TS,
        )
        return None
    core = text.split(CONTRACT_CORE_BEGIN, 1)[1].split(CONTRACT_CORE_END, 1)[0]
    # 内联到 spec 文件后不需要 export（且 export 会让脚本语义变成模块，徒增歧义）
    stripped = re.sub(r"(?m)^export\s+", "", core).strip("\n")
    if not stripped:
        logger.error("分页契约核心段为空，将降级为仅信封断言：%s", CONTRACT_CORE_TS)
        return None
    return stripped


#: 生成脚本里用到的核心段符号（用于自检：抽取结果必须真的含这些）
REQUIRED_CORE_SYMBOLS: tuple[str, ...] = (
    "function checkPaginationContract",
    "function reportContractResult",
)
