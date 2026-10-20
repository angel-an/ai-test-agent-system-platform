async (page) => {
  const fs = require('fs');
  const path = require('path');

  const LOG_FILE = path.resolve('agent_monitor.log');
  const SS_DIR = path.resolve('agent_screenshots');

  // 全局禁用 agent_screenshots 截图功能
  const shouldSkipScreenshots = true;

  if (!shouldSkipScreenshots && !fs.existsSync(SS_DIR)) fs.mkdirSync(SS_DIR, { recursive: true });

  function now() {
    return new Date().toISOString();
  }

  function logLine(obj) {
    const line = JSON.stringify({ time: now(), ...obj });
    fs.appendFileSync(LOG_FILE, line + '\n');
    console.log(line);
  }

  async function captureState() {
    return await page.evaluate(() => {
      const taskBtn = document.querySelector('button[class*="grid w-full"]');
      const taskText = taskBtn ? taskBtn.innerText.replace(/\s+/g, ' ').trim() : '';
      const runningEl = Array.from(document.querySelectorAll('*')).find(
        (el) => el.textContent && el.textContent.includes('Agent 正在运行中')
      );
      const pausedEl = Array.from(document.querySelectorAll('*')).find(
        (el) => el.textContent && el.textContent.includes('Agent 已暂停')
      );
      const errorEl = Array.from(document.querySelectorAll('*')).find(
        (el) => el.textContent && el.textContent.includes('连接已断开')
      );
      const doneEl = Array.from(document.querySelectorAll('*')).find(
        (el) => el.textContent && el.textContent.includes('所有任务已完成')
      );
      const approveBtn = Array.from(document.querySelectorAll('button')).find(
        (b) => b.textContent.includes('批准') && !b.disabled && b.offsetParent !== null
      );
      const reloadBtn = Array.from(document.querySelectorAll('button')).find(
        (b) => b.textContent.includes('刷新页面') && b.offsetParent !== null
      );
      return {
        taskText,
        running: !!runningEl,
        paused: !!pausedEl,
        error: errorEl ? errorEl.textContent.replace(/\s+/g, ' ').trim().substring(0, 300) : '',
        done: !!doneEl,
        hasApprove: !!approveBtn,
        hasReload: !!reloadBtn,
      };
    });
  }

  async function approveAll() {
    return await page.evaluate(() => {
      let count = 0;
      const buttons = Array.from(document.querySelectorAll('button'));
      for (const b of buttons) {
        if (b.textContent.includes('批准') && !b.disabled && b.offsetParent !== null) {
          b.click();
          count++;
        }
      }
      return count;
    });
  }

  async function reloadIfError() {
    return await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button')).find(
        (b) => b.textContent.includes('刷新页面') && b.offsetParent !== null
      );
      if (btn) {
        btn.click();
        return true;
      }
      return false;
    });
  }

  logLine({ event: 'monitor_start', url: page.url(), isWebwrightMode });
  let lastTaskText = '';
  let lastScreenshot = 0;
  let iteration = 0;
  const maxIterations = 120; // 10 minutes at 5s interval

  for (let i = 0; i < maxIterations; i++) {
    iteration = i + 1;
    await page.waitForTimeout(5000);

    const state = await captureState();
    const ssName = path.join(SS_DIR, `step_${String(iteration).padStart(3, '0')}.png`);

    const taskChanged = state.taskText !== lastTaskText;
    const timeForScreenshot = Date.now() - lastScreenshot > 30000;
    // 截图条件：非禁用状态，且满足截图触发条件
    const shouldScreenshot = !shouldSkipScreenshots && (taskChanged || state.error || state.paused || state.done || timeForScreenshot);
    if (shouldScreenshot) {
      try {
        await page.screenshot({ path: ssName, fullPage: false });
        state.screenshot = ssName;
        lastScreenshot = Date.now();
      } catch (e) {
        state.screenshotError = String(e);
      }
    }
    if (shouldSkipScreenshots) {
      state.screenshotsDisabled = true;
      state.screenshotSkipped = true;
    }
    lastTaskText = state.taskText;

    if (state.hasApprove) {
      const approved = await approveAll();
      logLine({ event: 'approval_clicked', approved, state });
      await page.waitForTimeout(2000);
      continue;
    }

    if (state.error && state.hasReload) {
      const reloaded = await reloadIfError();
      logLine({ event: 'reload_clicked', reloaded, state });
      await page.waitForTimeout(3000);
      continue;
    }

    logLine({ event: 'poll', iteration, state });

    if (state.done || (!state.running && !state.paused && state.taskText.includes('所有任务已完成'))) {
      logLine({ event: 'completed', reason: 'tasks_done', state });
      break;
    }

    if (!state.taskText && !state.running && !state.paused && iteration > 10) {
      logLine({ event: 'completed', reason: 'idle_no_tasks', state });
      break;
    }
  }

  const finalState = await captureState();
  logLine({ event: 'monitor_end', iteration, finalState });
}
