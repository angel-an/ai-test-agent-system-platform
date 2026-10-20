async (page) => {
  return await page.evaluate(() => {
    // 任务进度
    const taskBtn = document.querySelector('button[class*="grid w-full"]');
    const taskText = taskBtn ? taskBtn.innerText.replace(/\s+/g, ' ').trim() : '';

    // 运行中提示
    const running = !!Array.from(document.querySelectorAll('*')).find((el) => {
      if (el.tagName === 'STYLE' || el.tagName === 'SCRIPT') return false;
      return el.childNodes.length > 0 && el.textContent.includes('Agent 正在运行中');
    });

    // 暂停提示
    const paused = !!Array.from(document.querySelectorAll('*')).find((el) => {
      if (el.tagName === 'STYLE' || el.tagName === 'SCRIPT') return false;
      return el.textContent.includes('Agent 已暂停');
    });

    // 流错误容器
    const errorContainer = document.querySelector('.bg-red-50');
    const error = errorContainer ? errorContainer.innerText.replace(/\s+/g, ' ').trim().substring(0, 300) : '';
    const hasReloadInError = !!errorContainer?.querySelector('button');

    // 所有任务完成
    const done = !!Array.from(document.querySelectorAll('*')).find((el) => {
      if (el.tagName === 'STYLE' || el.tagName === 'SCRIPT') return false;
      return el.textContent.includes('所有任务已完成');
    });

    // 批准按钮（工具审批）
    const approveBtn = Array.from(document.querySelectorAll('button')).find(
      (b) => b.textContent.includes('批准') && !b.disabled && b.offsetParent !== null
    );

    return `TASK:${taskText}|RUNNING:${running}|PAUSED:${paused}|ERROR:${error}|DONE:${done}|APPROVE:${!!approveBtn}|RELOAD:${hasReloadInError}`;
  });
}
