<!-- SHELL-POLICY-BANNED-FILE: contains commands banned by shell policy
     (playwright-cli eval / run-code / npx playwright test / python|node direct-run).
     Reference only - DO NOT execute these examples. Run scripts via execute_web_script. -->

# CLI 宸ュ叿妯″紡濂戠害

## 姒傝堪

CLI 宸ュ叿妯″紡鏄?Webwright 鐨勭浜岀宸ヤ綔妯″紡銆傚湪杩欑妯″紡涓嬶紝鐢熸垚鐨?final_script.py 鏄竴涓彲閫氳繃鍛戒护琛屽弬鏁伴厤缃殑鐙珛宸ュ叿鑴氭湰銆傚畠灏佽涓轰竴涓彲澶嶇敤鐨勫嚱鏁帮紝鍐呭祵 argparse 鍙傛暟瑙ｆ瀽锛屽彲浠ュ湪涓嶅悓 URL銆佷笉鍚岀幆澧冧腑浠ョ被浼?CLI 宸ュ叿鐨勬柟寮忛噸澶嶆墽琛屻€?
## 閫傜敤鍦烘櫙

- 闇€瑕佸湪澶氫釜鐜锛坉ev/staging/prod锛変腑閲嶅鎵ц鐩稿悓娴嬭瘯
- CI/CD 绠￠亾闆嗘垚
- 鍙傛暟鍖栨祴璇曪紙涓嶅悓 URL銆佷笉鍚屾祴璇曟暟鎹級
- 鍥㈤槦鍏变韩鐨勮嚜鍔ㄥ寲宸ュ叿

## 鍙傛暟琛?
### 鏍囧噯鍙傛暟

| 鍙傛暟鍚?| 绫诲瀷 | 榛樿鍊?| 蹇呴渶 | 璇存槑 |
|--------|------|--------|------|------|
| `--url` | str | 鈥?| 鏄?| 鐩爣缃戠珯 URL |
| `--output-dir` | str | `./final_runs/run_001` | 鍚?| 杈撳嚭鐩綍璺緞 |
| `--timeout` | int | 30000 | 鍚?| 鍏ㄥ眬瓒呮椂鏃堕棿锛堟绉掞級 |
| `--viewport-width` | int | 1280 | 鍚?| 娴忚鍣ㄨ鍙ｅ搴?|
| `--viewport-height` | int | 1800 | 鍚?| 娴忚鍣ㄨ鍙ｉ珮搴?|
| `--headless` | bool | True | 鍚?| 鏄惁浠ユ棤澶存ā寮忚繍琛?|
| `--browser` | str | chromium | 鍚?| 娴忚鍣ㄧ被鍨嬶細chromium/firefox/webkit |

### 涓氬姟鍙傛暟

涓氬姟鐩稿叧鍙傛暟鏍规嵁鍏蜂綋浠诲姟娣诲姞锛屾斁鍦ㄦ爣鍑嗗弬鏁颁箣鍚庛€傚懡鍚嶈鍒欙細`--<涓氬姟鍙傛暟鍚?`锛屽皬鍐欏姞杩炲瓧绗︺€?
绀轰緥锛?| 鍙傛暟鍚?| 绫诲瀷 | 榛樿鍊?| 蹇呴渶 | 璇存槑 |
|--------|------|--------|------|------|
| `--username` | str | 鈥?| 鏄?| 鐧诲綍鐢ㄦ埛鍚?|
| `--password` | str | 鈥?| 鏄?| 鐧诲綍瀵嗙爜 |
| `--search-keyword` | str | 鈥?| 鍚?| 鎼滅储鍏抽敭璇?|

## 鏍囧噯鑴氭湰楠ㄦ灦

```python
"""
鍔熻兘锛?鍔熻兘鍚嶇О>
妯″紡锛欳LI 宸ュ叿妯″紡
鐢熸垚鏃堕棿锛?鏃堕棿>
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PwTimeout


def parse_args():
    """瑙ｆ瀽鍛戒护琛屽弬鏁?""
    parser = argparse.ArgumentParser(
        description="<鍔熻兘鎻忚堪> - Webwright CLI 宸ュ叿",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 鏍囧噯鍙傛暟
    parser.add_argument(
        "--url",
        required=True,
        help="鐩爣缃戠珯 URL锛堝繀闇€锛?
    )
    parser.add_argument(
        "--output-dir",
        default="./final_runs/run_001",
        help="杈撳嚭鐩綍璺緞锛堥粯璁? ./final_runs/run_001锛?
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30000,
        help="鍏ㄥ眬瓒呮椂鏃堕棿锛屽崟浣嶆绉掞紙榛樿: 30000锛?
    )
    parser.add_argument(
        "--viewport-width",
        type=int,
        default=1280,
        help="娴忚鍣ㄨ鍙ｅ搴︼紙榛樿: 1280锛?
    )
    parser.add_argument(
        "--viewport-height",
        type=int,
        default=1800,
        help="娴忚鍣ㄨ鍙ｉ珮搴︼紙榛樿: 1800锛?
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="浠ユ棤澶存ā寮忚繍琛屾祻瑙堝櫒锛堥粯璁? True锛?
    )
    parser.add_argument(
        "--browser",
        choices=["chromium", "firefox", "webkit"],
        default="chromium",
        help="娴忚鍣ㄧ被鍨嬶紙榛樿: chromium锛?
    )

    # 涓氬姟鍙傛暟锛堟寜闇€娣诲姞锛?    # parser.add_argument("--username", required=True, help="鐧诲綍鐢ㄦ埛鍚?)
    # parser.add_argument("--password", required=True, help="鐧诲綍瀵嗙爜")

    return parser.parse_args()


async def run_automation(args):
    """
    鏍稿績鑷姩鍖栧嚱鏁?
    鍙傛暟:
        args: argparse.Namespace 鈥?鍛戒护琛屽弬鏁板璞?
    杩斿洖:
        int 鈥?0 琛ㄧず鎴愬姛锛? 琛ㄧず澶辫触
    """
    # 閰嶇疆杈撳嚭鐩綍
    output_dir = Path(args.output_dir)
    screenshots_dir = output_dir / "screenshots"
    log_file = output_dir / "final_script_log.txt"

    screenshots_dir.mkdir(parents=True, exist_ok=True)

    # 閰嶇疆鏃ュ織
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    logger.info(f"Webwright CLI 宸ュ叿鍚姩")
    logger.info(f"鐩爣 URL: {args.url}")
    logger.info(f"杈撳嚭鐩綍: {output_dir}")
    logger.info(f"娴忚鍣? {args.browser}")
    logger.info("=" * 50)

    async with async_playwright() as p:
        # 閫夋嫨娴忚鍣?        browser_launcher = getattr(p, args.browser)
        browser = await browser_launcher.launch(
            headless=args.headless,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )

        context = await browser.new_context(
            viewport={
                "width": args.viewport_width,
                "height": args.viewport_height
            },
            ignore_https_errors=True,
        )
        page = await context.new_page()

        step = 1

        try:
            # === 涓氬姟閫昏緫鍐欏湪姝ゅ ===

            # Step 1: 瀵艰埅
            logger.info(f"Step {step}: 瀵艰埅鍒?{args.url}")
            await page.goto(args.url, wait_until="networkidle", timeout=args.timeout)
            await page.screenshot(
                path=screenshots_dir / f"final_execution_{step}_navigate.png"
            )
            logger.info(f"Step {step} 瀹屾垚")
            step += 1

            # === 鏇村姝ラ ===

            # 鏈€缁堢姸鎬?            logger.info("=" * 50)
            logger.info(f"鑴氭湰鎵ц瀹屾垚 | 鐘舵€? 鎴愬姛 | 鎬绘楠? {step - 1}")
            logger.info("=" * 50)
            return 0

        except PwTimeout as e:
            logger.error(f"鎿嶄綔瓒呮椂: {e}")
            await page.screenshot(path=screenshots_dir / "error_timeout.png")
            return 1
        except Exception as e:
            logger.error(f"鑴氭湰鎵ц澶辫触: {e}", exc_info=True)
            await page.screenshot(path=screenshots_dir / "error_failure.png")
            return 1
        finally:
            await browser.close()


def main():
    """CLI 鍏ュ彛"""
    args = parse_args()
    exit_code = asyncio.run(run_automation(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
```

## import 瀹夊叏瑙勫垯

鍦?CLI 宸ュ叿妯″紡涓嬶紝import 蹇呴』閬靛惊浠ヤ笅瀹夊叏瑙勫垯锛?
### 1. 鏍囧噯搴撲紭鍏?
鎵€鏈夋爣鍑嗗簱 import 鏀惧湪鏈€鍓嶉潰锛屾寜瀛楁瘝搴忔帓鍒楋細

```python
import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
```

### 2. 绗笁鏂瑰簱鏀惧湪鏍囧噯搴撳悗闈?
```python
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
```

### 3. 鏉′欢瀵煎叆

鍙湪蹇呰鏃惰繘琛屾潯浠跺鍏ワ紝骞剁粰鍑烘竻鏅扮殑閿欒鎻愮ず锛?
```python
try:
    from playwright.async_api import async_playwright
except ImportError:
    print("閿欒: 闇€瑕佸畨瑁?playwright銆傝杩愯: pip install playwright")
    sys.exit(1)
```

### 4. 绂佹鍔ㄦ€佸鍏?
涓嶄娇鐢?`__import__()` 鎴?`importlib.import_module()` 杩涜鍔ㄦ€佸鍏ャ€傛墍鏈?import 蹇呴』鏄枃浠堕《灞傜殑闈欐€佸鍏ャ€?
### 5. 涓氬姟妯″潡闅旂

濡傛灉鑴氭湰渚濊禆鑷畾涔夋ā鍧楋紝杩欎簺妯″潡蹇呴』鏀惧湪鑴氭湰鍚岀洰褰曚笅锛屽苟閫氳繃鐩稿璺緞瀵煎叆锛?
```python
from .utils import wait_for_element  # 涓嶆帹鑽?# 鎺ㄨ崘锛氬皢杈呭姪鍑芥暟鐩存帴鍐呰仈鍒拌剼鏈腑
```

## Step 0 鍙傛暟锛氭棩蹇楄

鍦ㄦ墍鏈?CLI 宸ュ叿鑴氭湰涓紝绗竴鏉℃棩蹇楄緭鍑哄繀椤绘槸 Step 0锛岀敤浜庤褰曟墽琛屽惎鍔ㄤ俊鎭拰鎵€鏈変紶鍏ュ弬鏁般€?
```python
def main():
    args = parse_args()

    # Step 0: 鍙傛暟璁板綍
    params_summary = (
        f"Webwright CLI 宸ュ叿 | "
        f"url={args.url} | "
        f"output_dir={args.output_dir} | "
        f"browser={args.browser} | "
        f"timeout={args.timeout} | "
        f"headless={args.headless} | "
        f"viewport={args.viewport_width}x{args.viewport_height}"
    )
    print(f"[Step 0] {params_summary}")
    print(f"[Step 0] 寮€濮嬫椂闂? {datetime.now().isoformat()}")

    # 灏嗗弬鏁颁篃鍐欏叆鏃ュ織鏂囦欢
    logging.basicConfig(...)
    logger = logging.getLogger(__name__)
    logger.info(f"[Step 0] 鎵ц鍙傛暟: {params_summary}")

    exit_code = asyncio.run(run_automation(args))
    sys.exit(exit_code)
```

Step 0 鐨勭洰鐨勬槸锛?- 璁板綍瀹屾暣鐨勬墽琛屼笂涓嬫枃
- 渚夸簬鎺掓煡闂鏄弬鏁伴敊璇繕鏄剼鏈€昏緫閿欒
- 涓?CI/CD 鎻愪緵鍙拷婧殑鎵ц璁板綍

## 浣跨敤鏂瑰紡

### 鍛戒护琛岃皟鐢?
```bash
# 鍩烘湰鐢ㄦ硶
python3 final_script.py --url "https://example.com"

# 鎸囧畾杈撳嚭鐩綍
python3 final_script.py \
    --url "https://example.com" \
    --output-dir "./final_runs/run_001" \
    --timeout 45000

# 浣跨敤 Firefox
python3 final_script.py \
    --url "https://example.com" \
    --browser firefox

# 瀹屾暣鍙傛暟
python3 final_script.py \
    --url "https://example.com/login" \
    --output-dir "./final_runs/run_002" \
    --timeout 60000 \
    --viewport-width 1280 \
    --viewport-height 1800 \
    --browser chromium \
    --username admin \
    --password secret123
```

### CI/CD 闆嗘垚绀轰緥锛圙itHub Actions锛?
```yaml
- name: Run Webwright CLI test
  run: |
    python3 final_script.py \
      --url "${{ secrets.TEST_URL }}" \
      --output-dir "./final_runs/run_${{ github.run_number }}" \
      --browser firefox \
      --username "${{ secrets.TEST_USER }}" \
      --password "${{ secrets.TEST_PASS }}"
```

## 涓庝竴娆℃€фā寮忕殑瀵规瘮

| 鐗规€?| 涓€娆℃€фā寮?| CLI 宸ュ叿妯″紡 |
|------|-----------|-------------|
| 鍙傛暟鍖?| URL/鏁版嵁纭紪鐮?| 閫氳繃鍛戒护琛屽弬鏁颁紶鍏?|
| 鍙鐢ㄦ€?| 鍗曟浣跨敤 | 鍙法鐜澶嶇敤 |
| 鑴氭湰澶嶆潅搴?| 杈冧綆 | 杈冮珮锛堝惈 argparse锛?|
| CI/CD 闆嗘垚 | 涓嶆敮鎸?| 鍘熺敓鏀寔 |
| 閫傜敤鍦烘櫙 | 蹇€熼獙璇併€佹帰绱?| 閲嶅娴嬭瘯銆佸鐜 |
