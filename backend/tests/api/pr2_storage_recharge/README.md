# PR-2 储值充值 API 骨架

这组文件只负责骨架分层，不包含真实接口路径。

落地顺序：
1. `test_p0_auth.py`
2. `test_p1_query.py`
3. `test_p1_write_recharge.py`
4. `test_p1_write_refund.py`
5. `test_p1_batch_import.py`
6. `test_p2_composite_flow.py`
7. `test_p2_negative_boundary.py`
8. `test_p3_cleanup.py`

建议断言顺序：
- HTTP 状态码
- 业务返回
- DB / 流水 / 余额
- 文件结果清单



## 接口映射（抓包确认 2026-09-04）

统一信封：HTTP200 + {resultCode:0,resultMsg:success,data:...}；鉴权头 ccess-token。

| 层 | 端点（相对 MARKETING/CONSOLE GW） | 请求体要点 | 断言位 |
|---|---|---|---|
| P0 登录 | POST {CONSOLE}/api/user/v1/users/ssouser/login → changeToken | login{userName,password:base64,verifyCode,uniqueId,tenantId,type:user/tenants} → changeToken{targetInstanceId,panelInstanceToken} | resultCode=0；data.token 可访问 org/getOrgInfo |
| P1 查询 | POST /api/icdp/v1/memberCard/account/page | {pageNum,pageSize,mobile} | data.list[] phone/principalBalance/cardNo |
| P1 充值/退款 | POST /api/icdp/v1/recharge/reSetRecharge | {type:0充值/1退款, phones[], amount, rechargeBizType:ENTERPRISE} | data.successPhones / errorPhones / noExistPhones；余额 ±0.01 |
| P1 批量 | OSS policy → PUT → POST /api/icdp/v1/memberInfoBestore/import/memberAccount?fileUrl=&type=0 | xlsx 表头 手机号/充值金额/备注 | resultCode=0；成功/失败字段首跑收敛 |
| P2 复合 | 充值→查余额→退款→查余额 | 同上 | 回到基线 ±0.011 |
| P2 负向 | 非会员 / 金额 0 / 99999.01 | 同上 | 非会员不 success；金额边界应被业务拒绝 |
| P3 收口 | 只读 + 对账 | — | 账户可查、无脏状态 |

GW 默认：CONSOLE=https://console-uat.xysjg.com/yundt-saas-gateway；MARKETING=https://imarketing-uat.xysjg.com/yundt-saas-gateway
（env：PR2_GW_CONSOLE/PR2_GW_MARKETING/PR2_TENANT_ID/PR2_MKT_INSTANCE）

## 运行（固定回归入口）

鉴权模型（2026-09-05 收敛）：UAT 网关不支持纯 httpx（401/实例识别失败），conftest 使用
**浏览器会话 API 客户端**（PlaywrightApiClient：UI 登录一次 → 同 cookies/TLS + ccess-token + 
eferer）。

`ash
# 0) 默认/常规回归（CI 安全隔离，无 live 环境时 10/10 skip，零写入）
python -m pytest backend/tests/api/pr2_storage_recharge -q

# 1) 只读 live（无写入；需 full-access 拉 Chromium + 外网）
# 优先使用平台标准命名 LIVE_API，兼容旧命名 PR2_LIVE_API
LIVE_API=1 python -m pytest backend/tests/api/pr2_storage_recharge/test_p0_auth.py     backend/tests/api/pr2_storage_recharge/test_p1_query.py -v

# 2) 写层 live（产生 0.01 级 UAT 流水；先处理独立项 I-4 留痕）
LIVE_API=1 python -m pytest backend/tests/api/pr2_storage_recharge/test_p1_write_recharge.py     backend/tests/api/pr2_storage_recharge/test_p1_write_refund.py -v

# 3) 复合/负向（非会员安全）+ 批量（小额）
LIVE_API=1 python -m pytest backend/tests/api/pr2_storage_recharge/test_p2_composite_flow.py     backend/tests/api/pr2_storage_recharge/test_p2_negative_boundary.py     backend/tests/api/pr2_storage_recharge/test_p1_batch_import.py -v

# 4) 门控负向（需业务确认后显式开启）
# 优先使用平台标准命名 RUN_RISKY_NEG / AUTH_NEG，兼容旧命名 PR2_RUN_RISKY_NEG / PR2_AUTH_NEG
LIVE_API=1 RUN_RISKY_NEG=1 AUTH_NEG=1 python -m pytest     backend/tests/api/pr2_storage_recharge/test_p2_negative_boundary.py     backend/tests/api/pr2_storage_recharge/test_p0_auth.py -v
`

登录账号默认 andan/andan123（env：PR2_API_USER/PR2_API_PASSWORD/PR2_CONSOLE_LOGIN_URL）；
live 收敛证据：pr2-储值充值交付-20260904/API执行记录-20260905.md（归档，非本轮重跑）。
