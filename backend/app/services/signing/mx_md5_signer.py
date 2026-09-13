"""美心 MD5 签名组件。

规则（来自导读 Sheet）：
    sign = MD5(appid + appsercret + ts + msg)   # 32 位小写
    msg  = 请求体 JSON 字符串（与实际发送的字节完全一致）

提供：
- Python 实现：后端预校验 / 生成时校验签名一致性。
- JS 辅助函数源码：内联进生成的 Playwright 脚本，避免每个脚本重复实现。

关键约束：签名用的 msg 必须与最终发送的 body 完全一致（同一 JSON 序列化结果），
否则服务端验签失败。因此生成脚本时先序列化得到 msg，签名后原样发送。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_msg(payload: dict[str, Any]) -> str:
    """把请求体序列化为签名/发送共用的 JSON 字符串。

    使用紧凑分隔符、保证 key 顺序稳定（按插入序）、不转义非 ASCII，
    确保"签名输入 == 实际发送 body"。
    """
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def mx_md5_sign(appid: str, appsercret: str, ts: str, msg: str) -> str:
    """计算美心签名：MD5(appid + appsercret + ts + msg)，32 位小写。"""
    raw = f"{appid}{appsercret}{ts}{msg}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class MXMD5Signer:
    """美心 MD5 签名器（Python 侧）。"""

    def __init__(self, appid: str, appsercret: str):
        self.appid = appid
        self.appsercret = appsercret

    def sign(self, ts: str, payload: dict[str, Any]) -> tuple[str, str]:
        """返回 (msg, sign)。msg 为序列化后的 JSON，sign 为其签名。"""
        msg = canonical_msg(payload)
        return msg, mx_md5_sign(self.appid, self.appsercret, ts, msg)


def mx_md5_js_helper() -> str:
    """返回可内联进 Playwright 脚本的 JS 辅助函数源码。

    与 Python 侧 mx_md5_sign 逻辑保持一致（MD5 32 位小写，紧凑 JSON）。
    依赖 Node crypto（Playwright 运行环境自带）。
    """
    return r"""
// ---- 美心 MD5 签名辅助（由后端生成，勿手写重复实现）----
const crypto = require('crypto');
function canonicalMsg(payload) {
  // 与后端 canonical_msg 一致：紧凑分隔符、不转义非 ASCII、key 按插入序
  return JSON.stringify(payload);
}
function mxMd5Sign(appid, appsercret, ts, msg) {
  return crypto.createHash('md5').update(appid + appsercret + ts + msg, 'utf8').digest('hex');
}
function mxTimestamp(d = new Date()) {
  // yyyyMMddHHmmss（美心 ts 格式）
  const p = (n) => String(n).padStart(2, '0');
  return d.getFullYear().toString() + p(d.getMonth() + 1) + p(d.getDate())
    + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
}
// ---- 结束 ----
""".strip()
