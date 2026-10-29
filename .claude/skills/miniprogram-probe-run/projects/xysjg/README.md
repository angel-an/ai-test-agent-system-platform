# 小杨生煎 UAT —— 项目探针配置（miniprogram-probe-run 的参考实例）

本目录是 `miniprogram-probe-run` skill 的**项目配置**，与框架引擎分离。
框架用法见上级 SKILL.md，此处只记项目事实。

## 工程信息

- 小程序：Xysj点单，appid `wx37b9ba33202ba852`，uni-app 编译包
- 工程目录：`backend/workspace/miniprogram/xysjg`（源包 D:\360Downloads\202610271443\mp-weixin，不入库）
- 环境：UAT（imarketing-uat.xysjg.com 等），测试账号 angel（V4 会员）
- 平台项目 identifier：PR-2；探针画像名：`xiaoyang-shengjian` / `xiaoyang-shengjian-device`
- 设备：荣耀 JDY-AN00（Android 14，WeChat 8.0.79），adb 序列号 AP6NCP5309409723

## 执行示例

```bash
# 真机（手机须先经 DevTools「真机调试」扫码挂会话）
python .claude/skills/miniprogram-probe-run/probe_runner.py \
  --project PR-2 --base http://127.0.0.1:8000 \
  --selectors .claude/skills/miniprogram-probe-run/projects/xysjg/selectors.json \
  --device AP6NCP5309409723 --profile xiaoyang-shengjian-device --main-module 首页

# 重采金标（运营配置变更后）
... 同上 --recapture-refs
```

## 已知事实（2026-10-29 实测）

- **落地真值 ≠ wxml 静态 path**（运营配置驱动）：图文广告→订单页、储值有礼→新增地址、
  个人资料→我的卡券、线上商城→微商城。金标已按实测落地采集，断言的是「落地不漂移」。
- 堂食=scanCode 扫桌码（源码 type=scanCode），`manual` 转人工；到店取/小杨送普通导航。
- 荣耀无障碍树不暴露微信 UI 文本 → u2 只能坐标点击；负一屏通知横幅会瞬时遮挡标题区
  （断言已带 3.5s 重拍 + 连拍容忍）。
- 真机渲染亚像素抖动：同名页两次截图文字边缘 RMSE 可达 53；比对用缩放+±3px 平移取 min
  （同页≈9 / 异页≈54，阈值 14）。
- 模拟点击失效（重插 USB 后）→ 重启手机恢复；设备残留 com.tencent.weautomator 撞死
  u2 agent → adb uninstall。
- 储值有礼落地「新增地址」是深链表单，BACK 链 3+ 层，恢复策略 14 轮 + 连续 BACK 上限 8。
- 数据模式 readonly；flow 类（下单等）不在本通道执行，计入 skipped_flow。

## 验收记录

- 2026-10-29 run `4c30d484`（device）：8 pass / 0 fail / 堂食转人工 / 储值有礼恢复超时（偶发）。
- 金标 8 张：home / goods(=订单页) / coupon_or_member(=我的卡券) / pickup / delivery /
  paying_member(=新增地址) / integral_mall / wemall(=微商城)。
