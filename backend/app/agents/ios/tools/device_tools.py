"""
iOS 测试设备管理工具

提供 xcrun simctl 设备连接检查、App 信息获取、屏幕截图等功能
"""

import json
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime

from langchain_core.tools import tool

from app.config.minio_client import MinIOClient
from app.config.settings import settings


# ============================================================================
# 设备管理工具
# ============================================================================

@tool
async def check_ios_device() -> str:
    """
    检查 iOS 设备连接状态

    执行 xcrun simctl 命令检查是否有可用的 iOS 设备（模拟器或真机），
    返回设备列表和连接状态。

    Returns:
        JSON 格式的设备状态信息

    Example:
        >>> result = await check_ios_device()
    """
    try:
        # 检查 xcrun 是否可用
        xcrun_version = subprocess.run(
            ["xcrun", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if xcrun_version.returncode != 0:
            return json.dumps({
                "success": False,
                "error": "xcrun 命令不可用，请检查 Xcode 是否安装",
                "hint": "在 macOS 上运行 `xcode-select --install` 安装 Xcode Command Line Tools"
            }, ensure_ascii=False, indent=2)

        # 获取已启动的模拟器列表
        devices_result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "booted"],
            capture_output=True,
            text=True,
            timeout=15,
        )

        # 解析已启动的设备
        booted_devices = []
        lines = devices_result.stdout.strip().split("\n")
        for line in lines:
            line = line.strip()
            # 匹配格式: iPhone 15 Pro (UUID) (Booted)
            if "(Booted)" in line:
                # 提取设备名称和 UUID
                parts = line.split("(")
                if len(parts) >= 2:
                    device_name = parts[0].strip()
                    # 提取 UUID
                    uuid_part = parts[1].split(")")[0] if ")" in parts[1] else ""
                    booted_devices.append({
                        "name": device_name,
                        "udid": uuid_part,
                        "status": "Booted",
                        "type": "simulator",
                    })

        # 也检查可用的设备（未启动的模拟器）
        available_result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "available"],
            capture_output=True,
            text=True,
            timeout=15,
        )

        available_devices = []
        current_runtime = ""
        lines = available_result.stdout.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("--"):
                # 运行时标题行，如 "-- iOS 17.5 --"
                current_runtime = line.replace("--", "").strip()
                continue
            if line and not line.startswith("=="):
                # 设备行
                parts = line.split("(")
                if len(parts) >= 2:
                    device_name = parts[0].strip()
                    # 提取 UUID 和状态
                    remaining = "(".join(parts[1:])
                    uuid_and_status = remaining.split(")")
                    if len(uuid_and_status) >= 2:
                        udid = uuid_and_status[0].strip()
                        status = uuid_and_status[1].strip().strip("()").strip()
                        available_devices.append({
                            "name": device_name,
                            "udid": udid,
                            "status": status,
                            "runtime": current_runtime,
                            "type": "simulator",
                        })

        # 检查真机连接（通过 ios-deploy 或 xcrun devicectl）
        physical_devices = []
        try:
            devicectl_result = subprocess.run(
                ["xcrun", "devicectl", "list", "devices"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if devicectl_result.returncode == 0:
                # 解析 devicectl 输出（JSON 格式）
                try:
                    devicectl_data = json.loads(devicectl_result.stdout)
                    for device in devicectl_data.get("result", {}).get("devices", []):
                        physical_devices.append({
                            "name": device.get("name", "Unknown"),
                            "udid": device.get("identifier", ""),
                            "status": "Connected",
                            "type": "physical",
                            "os_version": device.get("osVersion", "unknown"),
                        })
                except json.JSONDecodeError:
                    pass
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        all_devices = booted_devices + physical_devices

        return json.dumps({
            "success": len(all_devices) > 0,
            "xcrun_available": True,
            "xcrun_version": xcrun_version.stdout.strip() if xcrun_version.stdout else "unknown",
            "booted_simulators": len(booted_devices),
            "available_simulators": len(available_devices),
            "physical_devices": len(physical_devices),
            "total_devices": len(all_devices),
            "booted_devices": booted_devices,
            "available_devices": available_devices,
            "physical_devices_list": physical_devices,
            "message": f"发现 {len(booted_devices)} 台已启动模拟器, {len(physical_devices)} 台真机" if all_devices else "未找到可用设备，请启动模拟器或连接真机",
        }, ensure_ascii=False, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "error": "xcrun 命令执行超时"
        }, ensure_ascii=False, indent=2)
    except FileNotFoundError:
        return json.dumps({
            "success": False,
            "error": "xcrun 命令未找到，iOS 测试需要在 macOS 上运行",
            "hint": "请确保在 macOS 系统上运行，并已安装 Xcode"
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"检查设备时发生错误: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def list_ios_devices() -> str:
    """
    列出所有可用的 iOS 设备详情

    Returns:
        JSON 格式的设备列表，包含设备型号、iOS 版本、屏幕分辨率等信息

    Example:
        >>> devices = await list_ios_devices()
    """
    try:
        # 获取所有可用设备
        devices_result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "available", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )

        devices_info = []
        if devices_result.returncode == 0:
            try:
                data = json.loads(devices_result.stdout)
                for runtime, devices in data.get("devices", {}).items():
                    for device in devices:
                        if device.get("isAvailable", False):
                            devices_info.append({
                                "udid": device.get("udid", ""),
                                "name": device.get("name", "Unknown"),
                                "device_type": device.get("deviceTypeIdentifier", "unknown"),
                                "runtime": runtime,
                                "status": "available",
                                "type": "simulator",
                            })
            except json.JSONDecodeError:
                # 回退到文本解析
                pass

        # 如果 JSON 解析失败，使用文本解析
        if not devices_info:
            text_result = subprocess.run(
                ["xcrun", "simctl", "list", "devices", "available"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            lines = text_result.stdout.strip().split("\n")
            current_runtime = ""
            for line in lines:
                line = line.strip()
                if line.startswith("--"):
                    current_runtime = line.replace("--", "").strip()
                elif line and "(" in line and ")" in line:
                    parts = line.split("(")
                    device_name = parts[0].strip()
                    remaining = "(".join(parts[1:])
                    uuid_and_status = remaining.split(")")
                    if len(uuid_and_status) >= 2:
                        udid = uuid_and_status[0].strip()
                        status = uuid_and_status[1].strip().strip("()").strip()
                        devices_info.append({
                            "udid": udid,
                            "name": device_name,
                            "runtime": current_runtime,
                            "status": status,
                            "type": "simulator",
                        })

        # 获取已启动设备的详细信息
        for device in devices_info:
            if device.get("status") == "Booted":
                try:
                    # 获取设备信息
                    info_result = subprocess.run(
                        ["xcrun", "simctl", "getenv", device["udid"], "SIMULATOR_VERSION_INFO"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if info_result.returncode == 0:
                        device["version_info"] = info_result.stdout.strip()
                except Exception:
                    pass

        return json.dumps({
            "success": True,
            "device_count": len(devices_info),
            "devices": devices_info,
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"获取设备列表失败: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def get_ios_app_info(
    app_bundle_id: str,
    device_udid: Optional[str] = None,
) -> str:
    """
    获取 iOS 应用的基本信息

    通过 xcrun simctl 命令获取应用的 Bundle ID、版本、安装状态等信息。

    Args:
        app_bundle_id: 应用 Bundle ID，如 "com.example.app"
        device_udid: 可选，指定设备 UDID（多设备时使用）

    Returns:
        JSON 格式的应用信息

    Example:
        >>> info = await get_ios_app_info("com.example.app")
    """
    try:
        # 如果没有指定设备，使用第一个已启动的模拟器
        target_udid = device_udid
        if not target_udid:
            booted_result = subprocess.run(
                ["xcrun", "simctl", "list", "devices", "booted"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = booted_result.stdout.strip().split("\n")
            for line in lines:
                if "(Booted)" in line:
                    parts = line.split("(")
                    if len(parts) >= 2:
                        target_udid = parts[1].split(")")[0].strip()
                        break

        if not target_udid:
            return json.dumps({
                "success": False,
                "error": "未找到已启动的 iOS 设备，请先启动模拟器",
                "hint": "运行 `xcrun simctl boot <device_udid>` 启动模拟器"
            }, ensure_ascii=False, indent=2)

        # 检查应用是否已安装（通过 listapps）
        apps_result = subprocess.run(
            ["xcrun", "simctl", "listapps", target_udid],
            capture_output=True,
            text=True,
            timeout=15,
        )

        app_info = {
            "bundle_id": app_bundle_id,
            "installed": False,
            "version": "unknown",
            "name": "unknown",
        }

        if apps_result.returncode == 0:
            try:
                apps_data = json.loads(apps_result.stdout)
                for bundle_id, app_data in apps_data.items():
                    if bundle_id == app_bundle_id:
                        app_info["installed"] = True
                        app_info["name"] = app_data.get("ApplicationName", "unknown")
                        app_info["version"] = app_data.get("BundleVersion", "unknown")
                        app_info["path"] = app_data.get("Path", "unknown")
                        app_info["type"] = app_data.get("ApplicationType", "unknown")
                        break
            except json.JSONDecodeError:
                # 回退到文本搜索
                if app_bundle_id in apps_result.stdout:
                    app_info["installed"] = True

        # 检查应用是否正在运行
        running_result = subprocess.run(
            ["xcrun", "simctl", "spawn", target_udid, "launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        is_running = app_bundle_id in running_result.stdout if running_result.returncode == 0 else False

        return json.dumps({
            "success": True,
            "device_udid": target_udid,
            "app_bundle_id": app_bundle_id,
            "installed": app_info["installed"],
            "app_name": app_info["name"],
            "version": app_info["version"],
            "is_running": is_running,
            "path": app_info.get("path", "unknown"),
            "app_type": app_info.get("type", "unknown"),
        }, ensure_ascii=False, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "error": "xcrun 命令执行超时"
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"获取应用信息失败: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def take_ios_screenshot(
    device_udid: Optional[str] = None,
    project_identifier: str = "",
) -> str:
    """
    截取 iOS 设备屏幕并保存到 MinIO

    Args:
        device_udid: 可选，指定设备 UDID（多设备时使用）
        project_identifier: 项目标识符，用于 MinIO 存储路径

    Returns:
        JSON 格式的截图结果，包含 MinIO 对象路径

    Example:
        >>> result = await take_ios_screenshot(device_udid="ABC123", project_identifier="proj_001")
    """
    try:
        # 如果没有指定设备，使用第一个已启动的模拟器
        target_udid = device_udid
        if not target_udid:
            booted_result = subprocess.run(
                ["xcrun", "simctl", "list", "devices", "booted"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = booted_result.stdout.strip().split("\n")
            for line in lines:
                if "(Booted)" in line:
                    parts = line.split("(")
                    if len(parts) >= 2:
                        target_udid = parts[1].split(")")[0].strip()
                        break

        if not target_udid:
            return json.dumps({
                "success": False,
                "error": "未找到已启动的 iOS 设备，请先启动模拟器",
            }, ensure_ascii=False, indent=2)

        # 创建临时截图文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_path = Path(settings.ios_workspace_root) / "screenshots" / f"screenshot_{timestamp}.png"
        temp_path.parent.mkdir(parents=True, exist_ok=True)

        # 截取屏幕（使用 xcrun simctl io）
        screenshot_result = subprocess.run(
            ["xcrun", "simctl", "io", target_udid, "screenshot", str(temp_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if screenshot_result.returncode != 0:
            return json.dumps({
                "success": False,
                "error": f"截图失败: {screenshot_result.stderr}",
            }, ensure_ascii=False, indent=2)

        # 上传到 MinIO
        file_size = temp_path.stat().st_size
        object_name = f"ios-tests/{project_identifier}/screenshots/screenshot_{timestamp}.png"

        with open(temp_path, "rb") as f:
            MinIOClient.upload_file(
                object_name=object_name,
                data=f,
                length=file_size,
                content_type="image/png",
            )

        # 清理临时文件
        temp_path.unlink()

        return json.dumps({
            "success": True,
            "object_name": object_name,
            "timestamp": timestamp,
            "file_size": file_size,
            "device_udid": target_udid,
            "message": f"截图已保存到 MinIO: {object_name}",
        }, ensure_ascii=False, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "error": "截图命令执行超时"
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"截图失败: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def analyze_ios_screenshot_quality(
    object_name: str,
) -> str:
    """
    分析 iOS 截图质量

    从 MinIO 下载截图并分析其质量（分辨率、文件大小、清晰度指标）。
    用于排查 AI 视觉识别失败的原因。

    Args:
        object_name: MinIO 中的截图对象路径

    Returns:
        JSON 格式的截图质量分析结果

    Example:
        >>> result = await analyze_ios_screenshot_quality("ios-tests/proj_001/screenshots/screenshot_20250613_143000.png")
    """
    try:
        # 从 MinIO 下载截图
        screenshot_bytes = MinIOClient.download_file(object_name)
        file_size = len(screenshot_bytes)

        # 分析文件大小（iOS 模拟器截图通常 200-800KB，<100KB 说明质量很低）
        size_quality = "good" if file_size > 200 * 1024 else ("low" if file_size < 100 * 1024 else "medium")

        # 尝试解析 PNG 头获取尺寸
        width, height = 0, 0
        if screenshot_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            # PNG 尺寸在 IHDR chunk 中
            import struct
            ihdr_start = 16
            if len(screenshot_bytes) > ihdr_start + 8:
                width = struct.unpack(">I", screenshot_bytes[ihdr_start:ihdr_start+4])[0]
                height = struct.unpack(">I", screenshot_bytes[ihdr_start+4:ihdr_start+8])[0]

        # 判断截图是否全黑（检查前 1000 个字节是否都是 0）
        is_black = False
        if file_size > 1000:
            # 检查 PNG 的 IDAT 数据是否全为 0（简化判断）
            sample = screenshot_bytes[100:500]
            is_black = all(b == 0 for b in sample)

        recommendations = []
        if size_quality == "low":
            recommendations.append("截图文件过小，建议检查模拟器分辨率设置")
        if is_black:
            recommendations.append("截图可能为全黑，检查设备屏幕是否锁定或模拟器是否响应")
        if width > 0 and height > 0 and (width < 750 or height < 1334):
            recommendations.append("截图分辨率较低，建议使用更高分辨率的模拟器（如 iPhone 15 Pro）")

        if not recommendations:
            recommendations.append("截图质量正常，如果 AI 识别仍失败，建议更换模型（豆包 Seed / Gemini 3.x 优先）")

        return json.dumps({
            "success": True,
            "file_size_bytes": file_size,
            "file_size_kb": round(file_size / 1024, 2),
            "size_quality": size_quality,
            "resolution": f"{width}x{height}" if width > 0 else "unknown",
            "is_black": is_black,
            "recommendations": recommendations,
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"分析截图质量失败: {str(e)}"
        }, ensure_ascii=False, indent=2)
