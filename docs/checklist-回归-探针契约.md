# Checklist 回归探针契约（miniprogram-probe-run ↔ 平台）

平台端点前缀：`/api/v2/projects/{project_identifier}/checklist`（如 PR-2）。
`--base` 默认 `http://127.0.0.1:8000`（外包机走 `http://192.168.83.250:8000`）。

## 鉴权（2026-10-28 起强制）

除 `GET /evidence-file`（分享链接语义，有意不鉴权）外，**所有端点都要求
`Authorization: Bearer <JWT>`**。探针/脚本用服务账号 `checklist-bot` 取 token：

```bash
curl -X POST {base}/api/v2/auth/login -H "Content-Type: application/json" \
  -d '{"username":"checklist-bot","password":"<向平台方索取>"}'
# → access_token，脚本传 --token
```

无 token → 401。token 有效期见平台配置（默认过期后重新登录获取）。

## 执行顺序（固定四步，不跳步）

1. **逐证据上传**（新增，2026-10-28 起必须）：对每个截图/证据文件
   `POST {prefix}/evidence`，multipart 字段名 `file`。
   响应 `data.object_name`（形如 `checklist-evidence/PR-2/{hash}.png`）。
   单文件上限 50MB；空文件 400，超大 413，对象存储故障 502。
2. **跑探针**，产出每个清单行的判定。
3. **整包回传**：`POST {prefix}/run-results`，body：

   ```json
   {
     "profile": "xiaoyang-shengjian",
     "run_verdict": "pass | fail | unverified | empty",
     "summary": {"pass": 10, "fail": 0, "unverified": 0},
     "skipped_flow": 9,
     "report_url": "https://...",
     "results": [
       {
         "row_id": "CK-0a7e9e9b",
         "status": "pass | fail | unverified",
         "error": null,
         "evidence": "checklist-evidence/PR-2/xxx.png",
         "consistency": null,
         "feature": "图文广告（支持跳转支持轮播）",
         "main_module": "首页"
       }
     ]
   }
   ```

   - `evidence` 填第 1 步返回的 **object_name**，不要再填探针侧相对路径
     （旧格式 `probe-runs\...` 前端不可达，页面只显示不可点的灰字）。
   - 响应给 `run_id` / `rows_touched`，留档备查。
4. **页面确认**：打开「上线Checklist」页，看执行历史卡片与凭证列。

## 行级锚定（与 checklist-sync 一致，勿改）

`row_id = "CK-" + md5(f"{主模块}|{子模块}|{功能点}")[:8]`（utf-8）。
探针不自己算 row_id——从平台 `GET {prefix}/rows` 拉清单行，按 feature/main_module 匹配或直接用 row_id。

## 已知边界

- `skipped_flow`：本轮跳过的流程类（probe_kind=flow）行数，人工确认后下轮恢复探测。
- `consistency`：一致性断言说明（如"C端与B端文案一致"），仅不一致时填。
- 平台只 upsert 不清除；清单删行需人工同步。
