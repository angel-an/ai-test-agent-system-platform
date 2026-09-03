"""
渗透测试 - 信息收集工具模块

提供子域名枚举、端口扫描、目录扫描、指纹识别等信息收集功能。
所有工具通过子进程调用安全测试命令行工具执行。
"""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from langchain_core.tools import tool

from app.config.settings import settings


# ============================================================================
# 工作区配置
# ============================================================================

SECURITY_WORKSPACE = Path(settings.security_workspace_root).resolve()
SECURITY_WORKSPACE.mkdir(parents=True, exist_ok=True)

# 命令超时时间（秒）- 限制外部工具执行时间避免阻塞事件循环
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 180


def _check_command(cmd: list[str]) -> bool:
    """检查命令是否可用"""
    if not cmd:
        return False
    return shutil.which(cmd[0]) is not None


async def _run_command(cmd: list[str], timeout: int = DEFAULT_TIMEOUT, cwd: Optional[str] = None) -> dict:
    """通用异步命令执行辅助函数"""
    if not cmd:
        return {"success": False, "error": "命令为空"}

    if not _check_command(cmd):
        return {"success": False, "error": f"命令未找到: {cmd[0]}. 请确保已安装相应工具。"}

    # 限制最大超时时间
    timeout = min(timeout, MAX_TIMEOUT)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or str(SECURITY_WORKSPACE),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "success": proc.returncode == 0,
                "return_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            }
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return {"success": False, "error": f"命令执行超时（{timeout}秒）"}
    except Exception as e:
        return {"success": False, "error": f"命令执行失败: {str(e)}"}


# ============================================================================
# 子域名枚举
# ============================================================================

async def _recon_subdomains_impl(
    domain: str,
    mode: str = "passive",
    wordlist: Optional[str] = None,
    output_file: Optional[str] = None,
) -> str:
    """子域名枚举核心实现"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = output_file or f"subdomains_{domain}_{timestamp}.txt"
    out_path = SECURITY_WORKSPACE / out_file

    discovered = []

    # Passive enumeration
    if mode in ("passive", "comprehensive"):
        # subfinder
        sf_result = await _run_command(
            ["subfinder", "-d", domain, "-all", "-silent"],
            timeout=120
        )
        if sf_result["success"]:
            for line in sf_result["stdout"].strip().split("\n"):
                if line and line not in discovered:
                    discovered.append(line)

        # assetfinder
        af_result = await _run_command(
            ["assetfinder", "--subs-only", domain],
            timeout=120
        )
        if af_result["success"]:
            for line in af_result["stdout"].strip().split("\n"):
                if line and line not in discovered:
                    discovered.append(line)

    # Active brute force
    if mode in ("active", "comprehensive"):
        wordlist_path = wordlist or "/usr/share/wordlists/seclists/Discovery/DNS/subdomains-top1million-5000.txt"
        # dnsx brute force
        bf_result = await _run_command(
            ["dnsx", "-d", domain, "-w", wordlist_path, "-silent"],
            timeout=180
        )
        if bf_result["success"]:
            for line in bf_result["stdout"].strip().split("\n"):
                if line and line not in discovered:
                    discovered.append(line)

    # DNS resolution
    resolved = []
    if discovered:
        # Write to temp file for dnsx
        temp_subs = SECURITY_WORKSPACE / f"temp_subs_{timestamp}.txt"
        temp_subs.write_text("\n".join(discovered), encoding="utf-8")
        dns_result = await _run_command(
            ["dnsx", "-l", str(temp_subs), "-a", "-resp", "-silent"],
            timeout=120
        )
        temp_subs.unlink(missing_ok=True)
        if dns_result["success"]:
            for line in dns_result["stdout"].strip().split("\n"):
                if line:
                    resolved.append(line)

    # Save results
    if discovered:
        out_path.write_text("\n".join(discovered), encoding="utf-8")

    return json.dumps({
        "success": True,
        "domain": domain,
        "mode": mode,
        "total_discovered": len(discovered),
        "total_resolved": len(resolved),
        "subdomains": discovered[:200],  # Limit output
        "resolved_details": resolved[:100],
        "output_file": str(out_path) if discovered else None,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)


@tool
async def recon_subdomains(
    domain: str,
    mode: str = "passive",
    wordlist: Optional[str] = None,
    output_file: Optional[str] = None,
) -> str:
    """
    子域名枚举与 DNS 侦察

    使用 subfinder、assetfinder 等工具发现目标域名的子域名。

    Args:
        domain: 目标域名，如 example.com
        mode: 扫描模式，可选 passive（被动）/ active（主动）/ comprehensive（综合）
        wordlist: 自定义字典路径（可选）
        output_file: 输出文件路径（可选，默认保存到 workspace）

    Returns:
        JSON 格式的子域名列表和统计信息

    Example:
        >>> result = await recon_subdomains(domain="example.com", mode="comprehensive")
    """
    return await _recon_subdomains_impl(domain, mode, wordlist, output_file)


# ============================================================================
# 端口扫描
# ============================================================================

async def _recon_port_scan_impl(
    target: str,
    ports: str = "top1000",
    scan_type: str = "syn",
    service_detection: bool = True,
    os_detection: bool = False,
    output_file: Optional[str] = None,
) -> str:
    """端口扫描核心实现"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = output_file or f"portscan_{target.replace('.', '_')}_{timestamp}.xml"
    out_path = SECURITY_WORKSPACE / out_file

    # Build port specification
    port_map = {
        "top100": "--top-ports 100",
        "top1000": "--top-ports 1000",
        "full": "-p-",
    }
    port_arg = port_map.get(ports, f"-p {ports}")

    # Build scan type
    scan_map = {
        "syn": "-sS",
        "connect": "-sT",
        "udp": "-sU",
        "aggressive": "-sS -sV -sC -O",
    }
    scan_arg = scan_map.get(scan_type, "-sS")

    # Build nmap command
    cmd_parts = ["nmap", scan_arg, port_arg, "-T4"]
    if service_detection:
        cmd_parts.append("-sV")
    if os_detection:
        cmd_parts.append("-O")
    cmd_parts.extend(["-oX", str(out_path), target])

    result = await _run_command(cmd_parts, timeout=600)

    if not result["success"]:
        # Fallback to rustscan for faster scanning
        rust_result = await _run_command(
            ["rustscan", "-a", target, "--", "-sV"],
            timeout=300
        )
        if rust_result["success"]:
            result = rust_result
            # Save output
            out_path.with_suffix(".txt").write_text(result["stdout"], encoding="utf-8")

    # Parse simple results
    open_ports = []
    lines = result.get("stdout", "").split("\n")
    for line in lines:
        if "/tcp" in line and "open" in line:
            parts = line.split()
            if len(parts) >= 3:
                open_ports.append({
                    "port": parts[0],
                    "state": parts[1],
                    "service": parts[2],
                })

    return json.dumps({
        "success": result.get("success", False),
        "target": target,
        "ports": ports,
        "scan_type": scan_type,
        "open_ports": open_ports,
        "total_open": len(open_ports),
        "raw_output": result.get("stdout", "")[:5000],
        "output_file": str(out_path) if out_path.exists() else None,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)


@tool
async def recon_port_scan(
    target: str,
    ports: str = "top1000",
    scan_type: str = "syn",
    service_detection: bool = True,
    os_detection: bool = False,
    output_file: Optional[str] = None,
) -> str:
    """
    端口扫描与服务识别

    使用 nmap、rustscan 等工具扫描目标主机的开放端口和运行服务。

    Args:
        target: 目标 IP 或域名
        ports: 端口范围，可选 top100 / top1000 / full / 自定义如 "80,443,8080"
        scan_type: 扫描类型，syn / connect / udp / aggressive
        service_detection: 是否进行服务版本检测
        os_detection: 是否进行操作系统检测（需要 root）
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的端口扫描结果

    Example:
        >>> result = await recon_port_scan(target="192.168.1.1", ports="top1000", scan_type="syn")
    """
    return await _recon_port_scan_impl(target, ports, scan_type, service_detection, os_detection, output_file)


# ============================================================================
# 目录扫描
# ============================================================================

async def _recon_directory_scan_impl(
    target_url: str,
    wordlist: Optional[str] = None,
    extensions: Optional[str] = None,
    recursive: bool = False,
    threads: int = 50,
    output_file: Optional[str] = None,
) -> str:
    """目录扫描核心实现"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = output_file or f"dirscan_{timestamp}.json"
    out_path = SECURITY_WORKSPACE / out_file

    wordlist_path = wordlist or "/usr/share/wordlists/seclists/Discovery/Web-Content/common.txt"

    cmd = [
        "ffuf",
        "-u", f"{target_url}/FUZZ",
        "-w", wordlist_path,
        "-t", str(threads),
        "-mc", "200,204,301,302,307,401,403,405",
        "-o", str(out_path),
        "-of", "json",
    ]

    if extensions:
        cmd.extend(["-e", extensions])
    if recursive:
        cmd.append("-recursion")

    result = await _run_command(cmd, timeout=300)

    # Parse results
    findings = []
    if out_path.exists():
        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
            for r in data.get("results", []):
                findings.append({
                    "url": r.get("url", ""),
                    "status": r.get("status", 0),
                    "size": r.get("length", 0),
                    "words": r.get("words", 0),
                })
        except (json.JSONDecodeError, KeyError):
            pass

    return json.dumps({
        "success": result.get("success", False),
        "target_url": target_url,
        "total_findings": len(findings),
        "findings": findings[:100],
        "raw_output": result.get("stdout", "")[:3000],
        "output_file": str(out_path) if out_path.exists() else None,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)


@tool
async def recon_directory_scan(
    target_url: str,
    wordlist: Optional[str] = None,
    extensions: Optional[str] = None,
    recursive: bool = False,
    threads: int = 50,
    output_file: Optional[str] = None,
) -> str:
    """
    目录与文件扫描

    使用 ffuf 等工具爆破目标网站的目录和文件。

    Args:
        target_url: 目标 URL，如 https://example.com
        wordlist: 自定义字典路径（可选）
        extensions: 扩展名列表，如 "php,txt,bak,zip"（可选）
        recursive: 是否递归扫描
        threads: 线程数，默认 50
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的目录扫描结果

    Example:
        >>> result = await recon_directory_scan(
        ...     target_url="https://example.com",
        ...     extensions="php,txt,bak"
        ... )
    """
    return await _recon_directory_scan_impl(target_url, wordlist, extensions, recursive, threads, output_file)


# ============================================================================
# 指纹识别
# ============================================================================

async def _recon_fingerprint_impl(
    target_url: str,
    detect_waf: bool = True,
    detect_tech: bool = True,
    output_file: Optional[str] = None,
) -> str:
    """指纹识别核心实现"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = output_file or f"fingerprint_{timestamp}.json"
    out_path = SECURITY_WORKSPACE / out_file

    findings = {
        "technologies": [],
        "waf": [],
        "headers": {},
        "server": None,
    }

    # whatweb for tech detection
    if detect_tech:
        ww_result = await _run_command(
            ["whatweb", "-a", "3", "--json", target_url],
            timeout=120
        )
        if ww_result["success"]:
            try:
                ww_data = json.loads(ww_result["stdout"])
                for entry in ww_data:
                    for plugin, info in entry.get("plugins", {}).items():
                        findings["technologies"].append({
                            "name": plugin,
                            "version": info.get("version", ["unknown"])[0] if isinstance(info.get("version"), list) else str(info.get("version", "unknown")),
                        })
            except (json.JSONDecodeError, KeyError):
                pass

    # wafw00f for WAF detection
    if detect_waf:
        waf_result = await _run_command(
            ["wafw00f", "-a", target_url],
            timeout=120
        )
        if waf_result["success"]:
            for line in waf_result["stdout"].split("\n"):
                if "is behind" in line.lower():
                    findings["waf"].append(line.strip())

    # httpx for headers
    httpx_result = await _run_command(
        ["httpx", "-u", target_url, "-json", "-silent"],
        timeout=60
    )
    if httpx_result["success"]:
        try:
            hx_data = json.loads(httpx_result["stdout"])
            findings["server"] = hx_data.get("webserver")
            findings["headers"] = hx_data.get("headers", {})
        except (json.JSONDecodeError, KeyError):
            pass

    # Save results
    out_path.write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")

    return json.dumps({
        "success": True,
        "target_url": target_url,
        "technologies": findings["technologies"],
        "waf_detection": findings["waf"],
        "server": findings["server"],
        "headers": findings["headers"],
        "output_file": str(out_path),
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)


@tool
async def recon_fingerprint(
    target_url: str,
    detect_waf: bool = True,
    detect_tech: bool = True,
    output_file: Optional[str] = None,
) -> str:
    """
    Web 指纹识别与 WAF 检测

    使用 whatweb、wafw00f、httpx 等工具识别目标网站的技术栈和防护设备。

    Args:
        target_url: 目标 URL
        detect_waf: 是否检测 WAF
        detect_tech: 是否检测技术栈
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的指纹识别结果

    Example:
        >>> result = await recon_fingerprint(target_url="https://example.com")
    """
    return await _recon_fingerprint_impl(target_url, detect_waf, detect_tech, output_file)


# ============================================================================
# 综合扫描
# ============================================================================

@tool
async def recon_full_scan(
    target: str,
    target_url: Optional[str] = None,
    domain: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> str:
    """
    综合信息收集扫描

    一键执行全部信息收集：子域名枚举、端口扫描、目录扫描、指纹识别。
    各扫描任务并行执行，总超时 8 分钟。

    Args:
        target: 目标 IP 或域名
        target_url: 目标 URL（用于目录扫描和指纹识别）
        domain: 目标域名（用于子域名枚举，如未提供则使用 target）
        output_dir: 输出目录（可选）

    Returns:
        JSON 格式的综合扫描结果

    Example:
        >>> result = await recon_full_scan(
        ...     target="example.com",
        ...     target_url="https://example.com"
        ... )
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_domain = domain or target
    scan_url = target_url or f"https://{target}"

    results = {
        "timestamp": timestamp,
        "target": target,
        "scans": {},
        "total_time_seconds": 0,
    }

    start_time = datetime.now()

    # 构建并行扫描任务
    scan_tasks = []

    # 1. Subdomain enumeration
    async def run_subdomains():
        try:
            sub_result = await _recon_subdomains_impl(
                domain=scan_domain,
                mode="passive",  # 只用被动模式，更快
                output_file=f"subdomains_{timestamp}.txt"
            )
            return ("subdomains", json.loads(sub_result))
        except Exception as e:
            return ("subdomains", {"success": False, "error": str(e), "subdomains": []})
    scan_tasks.append(run_subdomains())

    # 2. Port scan - 使用 top100 而非 top1000，更快
    async def run_ports():
        try:
            port_result = await _recon_port_scan_impl(
                target=target,
                ports="top100",  # 从 top1000 改为 top100，显著加快
                scan_type="syn",
                output_file=f"portscan_{timestamp}.xml"
            )
            return ("ports", json.loads(port_result))
        except Exception as e:
            return ("ports", {"success": False, "error": str(e), "open_ports": []})
    scan_tasks.append(run_ports())

    # 3. Directory scan - 限制扩展名和线程
    async def run_dirs():
        try:
            dir_result = await _recon_directory_scan_impl(
                target_url=scan_url,
                extensions="php,txt",  # 减少扩展名
                threads=30,  # 减少线程避免被拦截
                output_file=f"dirscan_{timestamp}.json"
            )
            return ("directories", json.loads(dir_result))
        except Exception as e:
            return ("directories", {"success": False, "error": str(e), "findings": []})
    scan_tasks.append(run_dirs())

    # 4. Fingerprint
    async def run_fp():
        try:
            fp_result = await _recon_fingerprint_impl(
                target_url=scan_url,
                detect_waf=True,
                detect_tech=True,
                output_file=f"fingerprint_{timestamp}.json"
            )
            return ("fingerprint", json.loads(fp_result))
        except Exception as e:
            return ("fingerprint", {"success": False, "error": str(e), "technologies": []})
    scan_tasks.append(run_fp())

    # 并行执行所有扫描，总超时 8 分钟
    try:
        completed_results = await asyncio.wait_for(
            asyncio.gather(*scan_tasks, return_exceptions=True),
            timeout=480  # 8 分钟总超时
        )
        for res in completed_results:
            if isinstance(res, Exception):
                continue
            key, value = res
            results["scans"][key] = value
    except asyncio.TimeoutError:
        results["timeout"] = True
        results["message"] = "综合扫描超时（8分钟），部分结果可能不完整"

    elapsed = (datetime.now() - start_time).total_seconds()
    results["total_time_seconds"] = round(elapsed, 1)

    # 汇总统计
    total_open_ports = results["scans"].get("ports", {}).get("total_open", 0)
    total_dirs = results["scans"].get("directories", {}).get("total_findings", 0)
    total_subdomains = results["scans"].get("subdomains", {}).get("total_discovered", 0)
    results["summary"] = {
        "open_ports": total_open_ports,
        "directories_found": total_dirs,
        "subdomains_discovered": total_subdomains,
    }

    return json.dumps(results, ensure_ascii=False, indent=2)
