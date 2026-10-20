# -*- coding: utf-8 -*-
"""上传校验用的**夹具**（在 Python 侧构造，浏览器只负责按目标字节数拼装）。

复核意见：第一版用 ``Buffer.alloc(2048, 0x41)`` 命名成 ``.xlsx``，那只是"带扩展名的字节"，
不是有效工作簿，不能作为"合法文件被接受"的证据。因此：

- "合法文件"样本 = 真正的最小 XLSX（ZIP 包 + 必需部件），由 Python 用 ``zipfile`` 构造，
  可在单元测试里用 ``zipfile``/``ElementTree`` 校验结构；
- 浏览器侧拿到各部件的 base64 与**目标总字节数**，用 STORED（不压缩）方式拼装出**字节精确**的
  有效工作簿（多出来的字节放在一个 padding 部件里），从而支持按阈值做 N−1/N/N+1 样本；
- 扩展名不合法的样本（``.exe``）单独提供，"格式错误样本"与"合法样本"不混用。
"""

from __future__ import annotations

import base64
import io
import struct
import zipfile
from typing import Any

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"

#: 最小 XLSX 的必需部件（顺序稳定，便于测试与复用）
XLSX_PARTS: dict[str, bytes] = {
    "[Content_Types].xml": (
        _DECL
        + f'<Types xmlns="{_NS_CT}">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        + "</Types>"
    ).encode("utf-8"),
    "_rels/.rels": (
        _DECL
        + f'<Relationships xmlns="{_NS_PKG_REL}">'
        + f'<Relationship Id="rId1" Type="{_NS_REL}/officeDocument" Target="xl/workbook.xml"/>'
        + "</Relationships>"
    ).encode("utf-8"),
    "xl/workbook.xml": (
        _DECL
        + f'<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}">'
        + '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    ).encode("utf-8"),
    "xl/_rels/workbook.xml.rels": (
        _DECL
        + f'<Relationships xmlns="{_NS_PKG_REL}">'
        + f'<Relationship Id="rId1" Type="{_NS_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
        + "</Relationships>"
    ).encode("utf-8"),
    "xl/worksheets/sheet1.xml": (
        _DECL
        + f'<worksheet xmlns="{_NS_MAIN}"><sheetData>'
        + '<row r="1"><c r="A1" t="inlineStr"><is><t>手机号</t></is></c>'
        + '<c r="B1" t="inlineStr"><is><t>金额</t></is></c></row>'
        + '<row r="2"><c r="A2" t="inlineStr"><is><t>18320101265</t></is></c>'
        + '<c r="B2"><v>100</v></c></row>'
        + "</sheetData></worksheet>"
    ).encode("utf-8"),
}


def minimal_xlsx_bytes() -> bytes:
    """真正的最小 XLSX（ZIP 压缩）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in XLSX_PARTS.items():
            zf.writestr(name, data)
    return buf.getvalue()


def xlsx_parts_base64() -> dict[str, str]:
    """各部件的 base64（浏览器侧用 STORED 方式拼装出字节精确的工作簿）。"""
    return {name: base64.b64encode(data).decode("ascii") for name, data in XLSX_PARTS.items()}


def xlsx_stored_overhead() -> int:
    """浏览器按 STORED 拼装时，除 padding 数据外的固定开销（字节）。

    口径必须与 ``web_case_compiler`` 里注入的 JS 拼装函数**完全一致**：
    每个部件的本地头 30 + 名称长度 + 数据长度，中央目录 46 + 名称长度，
    最后加 22 字节 EOCD。
    """
    total = 0
    for name, data in XLSX_PARTS.items():
        name_len = len(name.encode("utf-8"))
        total += 30 + name_len + len(data)
        total += 46 + name_len
    total += 22
    return total


def bad_extension_bytes(size: int = 256) -> bytes:
    """扩展名不在允许列表里的样本（``.exe``）：MZ 头 + 填充字节。"""
    payload = (b"MZ" + bytes(range(256)) * 4)
    if size <= len(payload):
        return payload[:size]
    return payload + b"\x00" * (size - len(payload))


# --------------------------------------------------------------------------- #
# 有效 .xls（BIFF4 原始记录流）
# --------------------------------------------------------------------------- #
def minimal_xls_bytes() -> bytes:
    """真正有效的 .xls（Excel 4.0 BIFF 记录流，非 OLE2 容器）。

    复核意见要求"合法样本必须是有效文件、不能只是带扩展名的字节"。这里按 BIFF 规范
    写出 BOF / CODEPAGE / DIMENSIONS / LABEL / NUMBER / EOF 六类记录，字节内容可被
    真实解析器（``xlrd``）读出单元格——单元测试用 xlrd 校验这一点。
    """
    def record(record_id: int, payload: bytes) -> bytes:
        return struct.pack("<HH", record_id, len(payload)) + payload

    header = "手机号".encode("gbk")
    amount = "金额".encode("gbk")
    return b"".join([
        # BOF：BIFF4 工作表
        record(0x0409, struct.pack("<HHHH", 0x0400, 0x0010, 0, 0)),
        # CODEPAGE：GBK（缺了它解析器按 ascii 解码会失败）
        record(0x0042, struct.pack("<H", 936)),
        # DIMENSIONS：0..10 行、0..2 列
        record(0x0200, struct.pack("<HHHH", 0, 10, 0, 2)),
        # 表头（BIFF4 的 LABEL = row, col, xf, 2 字节长度, 文本）
        record(0x0204, struct.pack("<HHH", 0, 0, 0) + struct.pack("<H", len(header)) + header),
        record(0x0204, struct.pack("<HHH", 0, 1, 0) + struct.pack("<H", len(amount)) + amount),
        # 数据行：A2 文本、B2 数值
        record(0x0204, struct.pack("<HHH", 1, 0, 0)
               + struct.pack("<H", len(b"18320101265")) + b"18320101265"),
        record(0x0203, struct.pack("<HHH", 1, 1, 0) + struct.pack("<d", 100.0)),
        # EOF
        record(0x000A, b""),
    ])


def xls_base64() -> str:
    return base64.b64encode(minimal_xls_bytes()).decode("ascii")


def sample_spec(*, name: str, mime: str, source: str, byte_size: int | None = None,
                kind: str = "", threshold_confirmed: bool = False) -> dict[str, Any]:
    """给执行器的样本描述（不含大块数据，按需拼装）。"""
    spec: dict[str, Any] = {
        "name": name,
        "mime": mime,
        "source": source,          # workbook_stored | bytes
        "kind": kind,              # valid_workbook | disallowed_extension | boundary...
        "threshold_confirmed": bool(threshold_confirmed),
    }
    if byte_size is not None:
        spec["bytes"] = int(byte_size)
    return spec


__all__ = [
    "minimal_xls_bytes",
    "xls_base64",
    "XLSX_MIME",
    "XLSX_PARTS",
    "bad_extension_bytes",
    "minimal_xlsx_bytes",
    "sample_spec",
    "xlsx_parts_base64",
    "xlsx_stored_overhead",
]
