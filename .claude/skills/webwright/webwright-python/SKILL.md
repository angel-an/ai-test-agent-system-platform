---
name: webwright-python
description: Core skill for script-driven Web automation using Python/Playwright scripts instead of MCP tools or CLI commands
tools: Bash(mkdir:*) Bash(cp:*) Bash(ls:*)
model: deepseek-chat
---

# Webwright: Script-Driven Web Automation

## Overview

Webwright is a script-driven browser automation mode. Instead of controlling the browser step-by-step via MCP tools (web_mcp mode) or playwright-cli commands (web_cli mode), you write Python/Playwright scripts that execute the entire workflow in a single run. This approach produces reusable, version-controllable test scripts.

## Core Workflow: Explore -> Plan -> Generate -> Execute -> Verify

The Webwright workflow follows a five-phase loop:

### 1. Explore
Write and run temporary exploration scripts to understand page structure, discover element locators, and verify assumptions. Exploration scripts are saved in the playground/ directory.

### 2. Plan
Design the test strategy: identify critical user flows, preconditions, expected outcomes, and edge cases. Output a plan.md document.

### 3. Generate
Write the final_script.py: a complete, self-contained Python/Playwright automation script that performs all steps from start to finish.

### 4. Execute
Run the final_script.py in a dedicated run directory. Each execution creates a new run_<id+1>/ directory containing:
- final_script.py (the executed script)
- final_script_log.txt (step-by-step execution log)
- screenshots/ (screenshot at each critical step)
- self_reflect_result.json (verification results)

### 5. Verify
Analyze screenshots and logs to verify each step completed successfully. If failures are found, loop back to the Healer skill to fix and re-execute with a new run ID.

## Critical Rules

1. EACH new run gets a NEW run_<id+1>/ directory. Never overwrite an existing run directory.
2. Run directories follow the pattern: final_runs/run_001/, final_runs/run_002/, etc.
3. Every final_script.py must be self-contained (no dependency on external session state).
4. Screenshots must be taken at every critical step (navigation, form fill, click, verification).
5. Screenshots use viewport-sized captures (1280x1800), NOT full-page screenshots (full_page=True is forbidden).
6. The script must have proper error handling and logging.
7. After execution, always check final_script_log.txt and screenshots/ for verification.

## Run Directory Structure

```
final_runs/run_<id>/
├── final_script.py              # The generated Playwright script
├── final_script_log.txt         # Execution log output
├── screenshots/                 # Step-by-step screenshots
│   ├── final_execution_1_navigate.png
│   ├── final_execution_2_login_form.png
│   ├── final_execution_3_filled_form.png
│   └── ...
└── self_reflect_result.json     # Self-verification results
```

## Typical final_script.py Pattern

```python
import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright

WORKSPACE = Path(__file__).parent
SCREENSHOTS = WORKSPACE / "screenshots"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(WORKSPACE / "final_script_log.txt"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 1800}
        )
        page = await context.new_page()

        try:
            # Step 1: Navigate
            logger.info("Navigating to target URL...")
            await page.goto("https://example.com", wait_until="networkidle")
            await page.screenshot(path=SCREENSHOTS / "final_execution_1_navigate.png")

            # Step 2: Interact
            logger.info("Performing interactions...")
            await page.fill("input#username", "test_user")
            await page.screenshot(path=SCREENSHOTS / "final_execution_2_fill_username.png")

            # ... more steps ...

            logger.info("Script completed successfully")
        except Exception as e:
            logger.error(f"Script failed: {e}")
            raise
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## Error Recovery

When a script fails:
1. Read final_script_log.txt to identify the failing step
2. Check screenshots to understand the page state at failure
3. Fix the script and increment run ID (run_<id+1>/)
4. Re-execute and verify
