---
name: miniprogram-probe-run
description: 用 minium + 设备通道执行「上线Checklist」小程序探针：IDE（模拟器全能力）与 device（真机混合通道）双模式，结果与证据自动回传平台，不可自动化场景自动转人工打标。当用户要求执行/复跑 Checklist 回归探针、验证小程序页面功能点时使用。
---

# miniprogram-probe-run（框架能力，不携带项目信息）

本 skill 是**通用小程序 Checklist 探针执行器**。项目相关的选择器、坐标、金标、
tabBar 名单一律放在**项目配置目录**（见下），skill 本身只含引擎与协议。

## 双通道

| 通道 | 适用 | 能力 |
|---|---|---|
| IDE（默认） | 微信开发者工具模拟器 | automator 全能力：元素/数据/路由断言 |
| device（`--device <adb序列号>`） | 真机（真机调试桥） | u2 坐标执行 + adb 截图 + **图像金标断言** |

> 真机调试 2.0 的 automator 是**影子栈**（路由事件不穿透手机渲染层），
> 真机落地判定必须用图像断言，automator 仅用于会话存在性检查。

## 执行

```bash
# IDE 通道（须显式工程目录——项目相关，不做框架默认值）
python probe_runner.py --project PR-2 --base http://127.0.0.1:8000 \
  --selectors <项目配置目录>/selectors.json \
  --mini-project <小程序工程目录> --main-module 首页

# device 通道（手机须先经 DevTools「真机调试」二维码挂入自动化会话）
python probe_runner.py --project PR-2 --base http://127.0.0.1:8000 \
  --selectors <项目配置目录>/selectors.json \
  --device <adb序列号> --profile <项目画像名>
```

环境变量 `CHECKLIST_TOKEN`（或 --username/--password）。金标目录默认
`<selectors所在目录>/assert_refs`；`--recapture-refs` 重采基线（UI 变更后）。

## 项目配置目录结构

```
projects/<项目名>/
├── selectors.json    # 行配置：row_id → 探针规格（见下）
└── assert_refs/      # 落地页标题金标（图像断言基线，随项目入库）
```

`selectors.json` 模式：

```jsonc
{
  "tab_pages": ["pages/home/index", ...],   // IDE 用 tabBar 名单（device 用 _device.tab_pages）
  "_device": {                               // device 通道参数
    "screen": [720, 1612],
    "tab_home": [0.086, 0.958],             // 首页 tab 坐标（百分比）
    "home_rect": [0.05, 0.44, 0.9, 0.10],   // 首页特征区（图像判定，选通知横幅遮不到的稳定区）
    "title_rect": [0.15, 0.055, 0.7, 0.075],// 落地页标题裁剪区
    "rmse_threshold": 14.0,
    "tab_pages": [...],                     // device 用 tabBar 名单
    "reopen": {"swipe": [[x1,y1],[x2,y2]], "icon": [x,y]}  // 自动重进小程序手势
  },
  "<row_id>": {
    "page": "pages/home/index",
    "data_path": "banner_1",                // 数据断言（仅 IDE 强制；device 影子栈不镜像数据）
    "device": {
      "click": {"coord": [0.5, 0.484]},     // 或 {"css": ".class"}（页面级走 automator）
      "assert_img": "goods",                // 落地页金标 key（缺则不判定落地）
      "skip_data_assert": true,             // device 通道建议 true
      "expect_path_prefix": "pages/store/goods"  // 仅 IDE 用
    },
    "manual": {"reason": "...", "guide": "..."}  // 显式转人工（扫码/支付/真实数据类）
  }
}
```

## 自动打标语义

- 配置 `manual` → status=unverified + error=`[转人工]<原因>` + consistency=处置指引；
- device 通道无点击配置 → 自动 `[转人工]`；点击进原生页（扫码/支付）→ 留证据截图 + 回退 + `[转人工]`；
- 页面侧说明列识别 `[转人工]` 前缀显示琥珀徽标。`[转人工]` 与「产品 fail」严格区分，
  是否转人工由**配置评审**把关，防止把产品缺陷藏进人工队列。

## 环境前置（各机器一次）

1. 微信开发者工具已登录，`cli auto --project <工程> --auto-port 9420` 开启自动化；
2. device 通道另需：adb、uiautomator2（.venv 已装）、手机开 USB 调试并挂真机调试；
3. Android 若残留 com.tencent.weautomator 会撞死 u2 agent，`adb uninstall` 可除；
4. 模拟点击失效（重插 USB 后常见）→ 重启手机即可恢复输入管道。

## 参考项目配置

`projects/xysjg/`（小杨生煎 UAT）：完整坐标/金标/已知坑的实例，新项目的配置照此结构建。
