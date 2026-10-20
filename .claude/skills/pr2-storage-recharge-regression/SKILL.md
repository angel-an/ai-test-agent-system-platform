---
name: pr2-storage-recharge-regression
description: 驱动 PR-2 小杨生煎 B 端储值充值的 Web/API 双轨回归（场景→分轨执行→聚合回写）。仅在用户要求对 PR-2 储值充值跑回归/编排或引用本 skill 时使用。
---

# PR-2 储值充值 · 双轨回归编排（Web/API）

## 何时使用
用户要求对 PR-2「小杨生煎-B端储值充值」跑 Web/API 回归、按场景编排执行、或出统一报告时。

## 关键资产（必须引用，勿凭记忆造数据）
- 规则与口径（冻结）：`pr2-储值充值交付-20260904/PR2_规则与测试数据口径-冻结.md`
- 总控/双轨：`PR2_整体流程图与执行计划.md`、`Web_API双轨流程对照表.md`
- API 入口/鉴权：`backend/tests/api/pr2_storage_recharge/README.md`（浏览器会话客户端；写接口需同会话三件套，见 `写接口会话证据与复验说明-20260905.md`）
- Web 手册：`Web回归运行手册.md`
- 证据/回写：`API执行记录-20260905.md`、`门控负向执行记录-20260905.md`、`回归结果回写-20260904.md`

## 步骤
1. 向用户确认场景清单（ID 见口径表：W-S1/W-S6/W-S7/W-S4；A-RC-1/A-RF-1/A-RE-1/A-NM-1/A-AB-1/A-BI-1/AUTH-OK）。
2. 生成计划：`python pr2-储值充值交付-20260904/orchestrate_regression.py --scenarios <ids>`（dry-run 先展示轨道/门控）。
3. 经用户确认后再执行：加 `--live`；先检查门控 env（写层 PR2_LIVE_API=1；风险项另加 PR2_RUN_RISKY_NEG=1；Web 轨同）。
4. 聚合结果：Web 轨 self_reflect_result.json + API 轨 pytest 输出 → 统一报告（复用回写口径，禁止把待复核项算作通过）。
5. 更新执行记录（追加段）并提示 UAT 留痕（I-4）累计。

## 门控红线
- 无 live env 时只输出计划/默认全 skip，禁止擅自写层执行；
- 金额边界 0/99999.01、错误密码需业务确认并显式 `PR2_RUN_RISKY_NEG=1`/`PR2_AUTH_NEG=1`；
- 写层执行会真实产生 UAT 小额流水，结果必须登记留痕。
