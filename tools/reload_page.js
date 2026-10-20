async (page) => {
  return await page.evaluate(() => {
    const btn = Array.from(document.querySelectorAll('button')).find(
      (b) => b.textContent.includes('刷新页面') && b.offsetParent !== null
    );
    if (btn) { btn.click(); return 'yes'; }
    return 'no';
  });
}
