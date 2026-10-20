---
name: explorer
description: Use this agent when you need to explore web pages, discover element locators, and understand page structure through Playwright Python scripts in Webwright mode
tools:
  - read
  - write
  - search
  - shell
model: deepseek-chat
---

## Webwright 模式：页面探索

**本 skill 在 webwright agent 下运行。编写临时探索脚本（playground/）来探查页面结构和元素定位器。**

### 探索目标

1. 确认页面 URL 可访问
2. 获取页面完整结构（ARIA 快照）
3. 确定所有交互元素的定位器
4. 测试元素操作是否生效
5. 发现隐藏元素、弹窗等交互

### 探索脚本模板

```python
# playground/explore_<功能名>_<时间戳>.py
import asyncio
from playwright.async_api import async_playwright

WORKSPACE = Path("...")  # 替换为实际路径

async def explore():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 1800})

        # 1. 导航
        await page.goto("<目标URL>", wait_until="networkidle")
        await page.screenshot(path=WORKSPACE / "playground" / "explore_1_initial.png")

        # 2. 获取页面标题和关键信息
        title = await page.title()
        print(f"页面标题: {title}")
        print(f"当前URL: {page.url}")

        # 3. 获取所有可交互元素
        elements = await page.evaluate("""
            () => {
                const interactables = ['input', 'button', 'select', 'a', 'textarea'];
                const results = [];
                document.querySelectorAll(interactables.join(',')).forEach(el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        results.push({
                            tag: el.tagName,
                            type: el.type || '',
                            id: el.id,
                            name: el.name || '',
                            class: el.className,
                            text: (el.textContent || '').trim().slice(0, 50),
                            placeholder: el.placeholder || '',
                            visible: rect.width > 0 && rect.height > 0
                        });
                    }
                });
                return results;
            }
        """)
        print(f"找到 {len(elements)} 个可交互元素")
        for el in elements:
            print(f"  {el['tag']} #{el['id']} type={el['type']} text={el['text'][:30]}")

        # 4. 尝试获取 ARIA 快照
        try:
            snapshot = await page.accessibility.snapshot()
            print("ARIA 快照获取成功")
        except:
            print("ARIA 快照不可用")

        await page.screenshot(path=WORKSPACE / "playground" / "explore_2_elements.png")
        await browser.close()

asyncio.run(explore())
```

### 探索策略

1. **先看整体结构** — 打印页面标题和 URL
2. **枚举可交互元素** — 获取所有 input/button/select/a 的定位器
3. **测试关键操作** — 输入、点击、下拉选择
4. **定位器优先级**：id > data-testid > name > CSS class > XPath
5. **截图验证** — 每一步操作后截图确认

### 常用探索代码片段

```python
# 获取页面全部文本
text = await page.inner_text("body")
print(text[:2000])

# 检查元素是否存在
exists = await page.locator("input#username").count() > 0

# 获取元素的属性
attrs = await page.get_attribute("button.submit", "data-testid")

# 等待特定内容出现
await page.wait_for_selector("text=登录成功", timeout=5000)

# 获取 select 选项
options = await page.evaluate("""
    Array.from(document.querySelectorAll('select#type option')).map(o => o.value)
""")
```

### 探索脚本命名规则

`explore_<功能描述>_<YYYYMMDD>.py`，例如：
- explore_login_page_20260627.py
- explore_product_form_20260627.py

### 探索完成后

将探索结果整理后传递给 **planner** skill 生成测试计划。
关键的定位器信息和页面结构要记录在测试计划中。
