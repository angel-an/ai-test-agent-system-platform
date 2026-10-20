# Webwright 完整工作流走查

本文档提供 Webwright 六个阶段的详细走查说明，以及每个阶段的完成检查清单。

## 阶段总览

```
Plan → Explore → Author → Execute → Self-Verify → Done
  │        │         │         │            │          │
  │        │         │         │            │          └─ 报告结果
  │        │         │         │            └─ 逐项验证 CP
  │        │         │         └─ 运行 final_script.py
  │        │         └─ 编写 final_script.py
  │        └─ 探查页面，发现定位器
  └─ 输出 plan.md（含 CP 列表）
```

## 阶段 1：规划（Plan）

### 目标

将用户需求转化为结构化的测试计划，输出 `plan.md` 文件。

### 输入

- 用户的任务描述
- 目标网站 URL
- 需要验证的功能点

### 输出

`plan.md` — 包含编号的关键检查点（Critical Points, CP）列表。

### 走查步骤

1. **理解需求** — 梳理用户想要自动化的操作流程
2. **分解步骤** — 将流程分解为可验证的独立步骤
3. **确定 CP** — 每个关键步骤必须对应一个 CP，CP 必须可验证（通过截图或日志）
4. **编写 plan.md** — 使用标准格式

### plan.md 标准格式

```markdown
# 测试计划：<任务名称>

## 概述
<简要描述此次自动化的目标>

## 关键检查点 (Critical Points)

- [ ] CP-001: <可验证的操作或断言>
- [ ] CP-002: <可验证的操作或断言>
- [ ] CP-003: <可验证的操作或断言>

## 定位器清单
| 元素 | 选择器 | 说明 |
|------|--------|------|
| <元素名> | <选择器> | <用途> |

## 截图计划
| CP | 截图文件名 | 截图时机 |
|----|-----------|---------|
| CP-001 | final_execution_1_navigate.png | 首次导航后 |
| CP-002 | final_execution_2_fill_form.png | 填入表单后 |
```

### 完成清单

- [ ] plan.md 文件已创建
- [ ] CP 列表完整，覆盖用户所有需求
- [ ] 每个 CP 都可验证（有截图或明确的日志断言）
- [ ] 初步的定位器已列出
- [ ] 截图计划完整

## 阶段 2：探索（Explore）

### 目标

通过临时探索脚本验证 plan.md 中的定位器假设，获取真实页面结构。

### 方法

编写临时 Python/Playwright 脚本并执行，探查页面结构、验证元素定位器。

### 探索脚本模板

```python
# playground/explore_login_1_structure.py
import asyncio
from playwright.async_api import async_playwright

WORKSPACE = Path("/path/to/workspace")  # 替换为实际路径

async def explore():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 1800})

        # 1. 导航
        await page.goto("https://example.com/login", wait_until="networkidle")
        await page.screenshot(path=WORKSPACE / "playground" / "explore_1_initial.png")

        # 2. 获取页面标题
        print(f"Title: {await page.title()}")
        print(f"URL: {page.url}")

        # 3. 枚举可交互元素
        elements = await page.evaluate("""() => {
            const tags = ['input', 'button', 'select', 'a', 'textarea'];
            const results = [];
            document.querySelectorAll(tags.join(',')).forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    results.push({
                        tag: el.tagName,
                        type: el.type || '',
                        id: el.id,
                        name: el.name || '',
                        text: (el.textContent || '').trim().slice(0, 50),
                        placeholder: el.placeholder || ''
                    });
                }
            });
            return results;
        }""")
        for el in elements:
            print(f"  {el['tag']} #{el['id']} name={el['name']} text={el['text'][:30]}")

        # 4. 测试定位器
        await page.wait_for_selector("input#username", timeout=5000)
        print("✓ input#username 存在")

        await browser.close()

asyncio.run(explore())
```

### 完成清单

- [ ] 至少一次成功导航到目标页面
- [ ] 关键交互元素（表单、按钮、链接）的定位器已确认有效
- [ ] 探索结果已记录（直接在 plan.md 中更新定位器）
- [ ] 潜在风险已识别（弹窗、动态加载、认证要求）

## 阶段 3：编写脚本（Author）

### 目标

基于 plan.md 和探索结果，编写完整的 final_script.py。

### 格式要求

参考 `reference/playwright_patterns.md` 中的标准骨架，确保：

1. 自包含 — 所有依赖在文件头部 import
2. 仪器化 — 每步有日志和截图
3. 错误处理 — 超时和异常捕获
4. 视口约束 — 1280x1800，无 full_page

### 完成清单

- [ ] 脚本文件已创建到 `final_runs/run_<id>/final_script.py`
- [ ] 目录 `final_runs/run_<id>/screenshots/` 已创建
- [ ] 脚本包含标准配置区（RUN_DIR、logging 配置）
- [ ] 脚本中的步骤编号对应 plan.md 的 CP 编号
- [ ] 每个步骤都有 `logger.info` 和 `screenshot` 调用
- [ ] 错误处理完整（TimeoutError 和通用 Exception）
- [ ] 无 `full_page=True` 的使用
- [ ] 视口设置为 1280x1800
- [ ] 脚本末尾有最终状态日志输出

## 阶段 4：执行（Execute）

### 目标

运行 final_script.py 并收集执行结果（唯一通道：execute_web_script，⛔ 禁止 shell 直跑 python3，SHELL-POLICY-BANNED）。

### 执行调用

```
execute_web_script(local_script_path="final_runs/run_001/final_script.py", framework="python", workspace_mode="webwright", project_identifier=..., sub_function_id=...)
```

### 执行后检查

1. 命令的返回码
   - returncode == 0 → 脚本运行成功
   - returncode != 0 → 发生了未捕获异常，需要修复

2. 检查日志文件是否存在
   - `final_runs/run_<id>/final_script_log.txt`

3. 检查截图文件是否存在
   - `ls final_runs/run_<id>/screenshots/`

### 完成清单

- [ ] 脚本已成功执行（或已记录失败信息）
- [ ] final_script_log.txt 已生成
- [ ] screenshots 目录中有截图文件
- [ ] 截图文件命名符合标准

## 阶段 5：自验证（Self-Verify）

### 目标

逐一验证 plan.md 中的每个关键检查点（CP）。

### 验证方法

对每个 CP，执行以下步骤：

1. **查找对应步骤的日志** — 在 final_script_log.txt 中搜索对应步骤的日志
2. **检查对应截图** — 打开 screenshots/ 中的对应截图
3. **确认状态** — 目视确认截图内容符合预期
4. **标记 CP** — 只在有具体证据（明确的日志记录 + 截图内容确认）时，将 CP 标记为通过

### 验证示例

```
CP-001: 导航到登录页面，页面标题正确
 → 日志: "Step 1: 导航到登录页面" + "Step 1 完成" (无 ERROR)
 → 截图: final_execution_1_navigate.png (可见登录表单、页面标题)
 → 结论: ✅ 通过

CP-002: 填写用户名和密码
 → 日志: "Step 2: 填写用户名" + "Step 2 完成"
 → 截图: final_execution_2_fill_username.png (可见输入框中已填入内容)
 → 结论: ✅ 通过
```

### 验证结果 JSON 格式

```json
{
  "run_id": "run_001",
  "execution_status": "passed",
  "total_cp": 5,
  "passed_cp": 5,
  "failed_cp": 0,
  "details": [
    {"cp": "CP-001", "status": "passed", "evidence": "日志无错误，截图显示登录页面"},
    {"cp": "CP-002", "status": "passed", "evidence": "截图显示用户名已填入"},
    {"cp": "CP-003", "status": "passed", "evidence": "日志显示点击成功"}
  ],
  "summary": "5/5 关键检查点通过。所有截图和日志正常。"
}
```

### 完成清单

- [ ] 所有 CP 已逐一验证
- [ ] 每个 CP 的验证都有具体证据引用
- [ ] self_reflect_result.json 已生成
- [ ] 失败 CP 已明确记录原因

## 阶段 6：完成（Done）

### 目标

汇总验证结果，报告最终状态。

### 状态判断

| 条件 | 结果 |
|------|------|
| 所有 CP 通过 | 成功，报告完成 |
| 部分 CP 失败（首次） | 触发修复循环（healer），新 run_<id+1>/ |
| 部分 CP 失败（已修复 3 次） | 标记为「不可修复」，需要人工介入 |

### 最终报告内容

```
Webwright 自动化执行报告
========================
任务: <任务名称>
运行 ID: run_001
执行状态: ✅ 成功 / ❌ 失败

关键检查点:
  CP-001: ✅ 导航到登录页面
  CP-002: ✅ 填写用户名和密码
  CP-003: ❌ 登录按钮点击（原因：定位器失效）

截图: 3/4 张正常（final_execution_3_click_submit.png 显示空白页）
日志: 无 ERROR，有 1 个 WARNING

总结: 4/5 CP 通过。需要修复 CP-003 的定位器。
```

### 完成清单

- [ ] 已验证所有 CP
- [ ] 报告已生成
- [ ] 如果失败，已触发修复流程（通知 healer）
- [ ] 如果成功，所有产物已归档到 run_<id>/ 目录中

## 修复循环（Healer 流程）

当自验证发现失败时，进入修复循环：

1. **诊断** — 读取 final_script_log.txt 和失败步骤的截图
2. **修复** — 修改定位器或逻辑，但保持测试意图不变
3. **增量运行 ID** — 创建新目录 run_<id+1>/
4. **重新执行** — 运行修复后的脚本
5. **重新验证** — 再次执行自验证
6. **最大重试 3 次** — 若仍失败则标记为不可修复
