async (page) => {
  return await page.evaluate(() => {
    let count = 0;
    for (const b of Array.from(document.querySelectorAll('button'))) {
      if (b.textContent.includes('批准') && !b.disabled && b.offsetParent !== null) {
        b.click();
        count++;
      }
    }
    return count;
  });
}
