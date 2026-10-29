---
name: miniprogram-probe-run
description: 用 minium 驱动微信开发者工具跑「上线Checklist」小程序探针，证据与结果自动回传平台。当用户要求执行/复跑 Checklist 回归探针、验证小程序页面功能点时使用。
---

# miniprogram-probe-run

本 skill 让 Checklist 回归**完全自托管在本机**：minium 驱动微信开发者工具跑
小程序探针，按平台清单行逐条探测，截图走证据契约回传，无需 WorkBuddy。

## 前置条件（一次性）

1. 微信开发者工具已登录（`cli.bat islogin` → `{"login":true}`）。
2. 已开启自动化服务端口：`微信开发者工具.exe cli auto --project <工程> --auto-port 9420`。
3. 平台后端在跑，checklist-bot token 可用（环境变量 `CHECKLIST_TOKEN`）。

小程序工程：小杨生煎 mp 编译包，位于
`backend/workspace/miniprogram/xysjg`（appid wx37b9ba33202ba852，租户渲染由后端下发）。

## 执行

```bash
.venv/Scripts/python.exe .claude/skills/miniprogram-probe-run/probe_runner.py \
  --project PR-2 --base http://127.0.0.1:8000 \
  --selectors .claude/skills/miniprogram-probe-run/xysjg_selectors.json \
  --main-module 首页
```

流程（与 docs/checklist-回归-探针契约.md 四步一致）：拉清单行 → minium 逐条
探测并截图 → 逐张上传证据拿 object_name → 整包 POST run-results。
输出 run_id 与逐行判定；页面「上线Checklist」即时可见。

## 探针类型

- `reachable`：导航到页面即通过（页面可达）
- `visible`：目标元素存在且可见
- `navigate`：点击元素后断言落地页路径（expect_path_prefix）
- `flow`：**本 skill 不执行**（涉及造数据），计入 skipped_flow 回传

## 选择器配置

`xysjg_selectors.json`：row_id → {page, by(text|xpath), selector,
expect_path_prefix?}。新增功能点 = 在配置里加一行 + 清单行已存在。
页面结构变动只需改配置，不动 runner。

## 已知边界

- 登录态：模拟器当前以测试账号运行；会员/订单等需登录态页面依赖该账号数据。
- 本包是 mp 编译产物（uni-app），选择器基于渲染文本/类名，改 WXML 结构需同步配置。
- DevTools 需保持运行；v22.16 + minium 1.6.1 已实测连通。
- IDE automator 跨会话导航不稳定，tabBar 页/组件内点击受限——navigate 探针
  按数据/素材下发断言降级，验证方式写在 consistency 列；落地断言待真机通道。

## 真机通道（预备状态，2026-10-29）

为降级行的落地断言准备。已就绪：adb（D:\apps\android_sdk）、uiautomator2
（已入 .venv）。**缺：Android 设备/可用模拟器**（本机无 AVD、无系统镜像、
夜神仅有残留目录不可运行）。

设备到位后的激活清单：
1. 设备开 USB 调试插本机（或把可用模拟器跑起来），`adb devices` 可见；
2. 设备安装微信并登录（测试账号的微信态）；
3. `cli preview` 出预览二维码，设备微信扫码打开本小程序包（体验版入口）；
4. minium Android 模式建连（`platform: "android"`，device_desire 具体配置
   有真机时实测校准），届时 click_coordinate/text_click 可用，恢复
   xysjg_selectors.json 中标注「待真机通道」的落地断言。
