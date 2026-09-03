"""
Android 测试设备管理工具

提供 adb 设备连接检查、App 信息获取、屏幕截图等功能
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
async def check_android_device() -> str:
    """
    检查 Android 设备连接状态

    执行 adb devices 命令检查是否有已连接的 Android 设备，
    返回设备列表和连接状态。

    Returns:
        JSON 格式的设备状态信息

    Example:
        >>> result = await check_android_device()
    """
    try:
        # 检查 adb 是否可用
        adb_version = subprocess.run(
            ["adb", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if adb_version.returncode != 0:
            return json.dumps({
                "success": False,
                "error": "adb 命令不可用，请检查 Android SDK 是否安装并配置 PATH",
                "hint": "设置 ANDROID_HOME 环境变量并添加 $ANDROID_HOME/platform-tools 到 PATH"
            }, ensure_ascii=False, indent=2)

        # 获取设备列表
        devices_result = subprocess.run(
            ["adb", "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        lines = devices_result.stdout.strip().split("\n")
        devices = []

        # 跳过第一行 "List of devices attached"
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                udid = parts[0]
                status = parts[1]
                # 解析额外信息
                info = {}
                for part in parts[2:]:
                    if ":" in part:
                        key, value = part.split(":", 1)
                        info[key] = value

                devices.append({
                    "udid": udid,
                    "status": status,
                    "info": info,
                })

        # 检查是否有已授权的设备
        authorized_devices = [d for d in devices if d["status"] == "device"]

        return json.dumps({
            "success": len(authorized_devices) > 0,
            "adb_available": True,
            "adb_version": adb_version.stdout.strip().split("\n")[0] if adb_version.stdout else "unknown",
            "total_devices": len(devices),
            "authorized_devices": len(authorized_devices),
            "devices": devices,
            "message": f"发现 {len(authorized_devices)} 台已授权设备" if authorized_devices else "未找到已授权设备，请检查 USB 调试是否开启",
        }, ensure_ascii=False, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "error": "adb 命令执行超时"
        }, ensure_ascii=False, indent=2)
    except FileNotFoundError:
        return json.dumps({
            "success": False,
            "error": "adb 命令未找到，请安装 Android Platform Tools",
            "hint": "下载地址: https://developer.android.com/studio/releases/platform-tools"
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"检查设备时发生错误: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def list_connected_devices() -> str:
    """
    列出所有已连接的 Android 设备详情

    Returns:
        JSON 格式的设备列表，包含设备型号、Android 版本、屏幕分辨率等信息

    Example:
        >>> devices = await list_connected_devices()
    """
    try:
        # 获取设备列表
        devices_result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        lines = devices_result.stdout.strip().split("\n")
        device_udids = []

        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                device_udids.append(parts[0])

        if not device_udids:
            return json.dumps({
                "success": False,
                "error": "未找到已连接的设备",
                "devices": []
            }, ensure_ascii=False, indent=2)

        # 获取每个设备的详细信息
        devices_info = []
        for udid in device_udids:
            try:
                # 获取设备型号
                model_result = subprocess.run(
                    ["adb", "-s", udid, "shell", "getprop", "ro.product.model"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                device_model = model_result.stdout.strip() if model_result.returncode == 0 else "unknown"

                # 获取 Android 版本
                version_result = subprocess.run(
                    ["adb", "-s", udid, "shell", "getprop", "ro.build.version.release"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                android_version = version_result.stdout.strip() if version_result.returncode == 0 else "unknown"

                # 获取屏幕分辨率
                size_result = subprocess.run(
                    ["adb", "-s", udid, "shell", "wm", "size"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                screen_size = size_result.stdout.strip().replace("Physical size: ", "") if size_result.returncode == 0 else "unknown"

                # 获取屏幕密度
                density_result = subprocess.run(
                    ["adb", "-s", udid, "shell", "wm", "density"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                screen_density = density_result.stdout.strip().replace("Physical density: ", "") if density_result.returncode == 0 else "unknown"

                devices_info.append({
                    "udid": udid,
                    "model": device_model,
                    "android_version": android_version,
                    "screen_size": screen_size,
                    "screen_density": screen_density,
                })
            except Exception as e:
                devices_info.append({
                    "udid": udid,
                    "error": str(e),
                })

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
async def get_app_info(
    app_package: str,
    device_udid: Optional[str] = None,
) -> str:
    """
    获取 Android 应用的基本信息

    通过 adb 命令获取应用的包名、版本、主 Activity 等信息。

    Args:
        app_package: 应用包名，如 "com.example.app"
        device_udid: 可选，指定设备序列号（多设备时使用）

    Returns:
        JSON 格式的应用信息

    Example:
        >>> info = await get_app_info("com.dongchedi.app")
    """
    try:
        adb_prefix = ["adb"]
        if device_udid:
            adb_prefix = ["adb", "-s", device_udid]

        # 检查应用是否已安装
        pm_result = subprocess.run(
            adb_prefix + ["shell", "pm", "list", "packages", app_package],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if app_package not in pm_result.stdout:
            return json.dumps({
                "success": False,
                "error": f"应用 {app_package} 未在设备上安装",
                "installed": False,
            }, ensure_ascii=False, indent=2)

        # 获取应用版本
        version_result = subprocess.run(
            adb_prefix + ["shell", "dumpsys", "package", app_package, "|", "grep", "versionName"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version = "unknown"
        if version_result.returncode == 0:
            for line in version_result.stdout.split("\n"):
                if "versionName=" in line:
                    version = line.split("versionName=")[1].strip()
                    break

        # 获取主 Activity
        activity_result = subprocess.run(
            adb_prefix + ["shell", "cmd", "package", "resolve-activity", "--brief", app_package],
            capture_output=True,
            text=True,
            timeout=10,
        )
        main_activity = "unknown"
        if activity_result.returncode == 0:
            lines = activity_result.stdout.strip().split("\n")
            for line in lines:
                line = line.strip()
                if line and not line.startswith("priority") and "/" in line:
                    main_activity = line
                    break

        # 获取当前焦点窗口（确认应用是否在前台）
        focus_result = subprocess.run(
            adb_prefix + ["shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        current_focus = focus_result.stdout.strip() if focus_result.returncode == 0 else "unknown"
        is_foreground = app_package in current_focus

        return json.dumps({
            "success": True,
            "installed": True,
            "app_package": app_package,
            "version": version,
            "main_activity": main_activity,
            "is_foreground": is_foreground,
            "current_focus": current_focus,
        }, ensure_ascii=False, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "error": "adb 命令执行超时"
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"获取应用信息失败: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def take_android_screenshot(
    device_udid: Optional[str] = None,
    project_identifier: str = "",
) -> str:
    """
    截取 Android 设备屏幕并保存到 MinIO

    Args:
        device_udid: 可选，指定设备序列号（多设备时使用）
        project_identifier: 项目标识符，用于 MinIO 存储路径

    Returns:
        JSON 格式的截图结果，包含 MinIO 对象路径

    Example:
        >>> result = await take_android_screenshot(device_udid="abc123", project_identifier="proj_001")
    """
    try:
        adb_prefix = ["adb"]
        if device_udid:
            adb_prefix = ["adb", "-s", device_udid]

        # 创建临时截图文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_path = Path(settings.android_workspace_root) / "screenshots" / f"screenshot_{timestamp}.png"
        temp_path.parent.mkdir(parents=True, exist_ok=True)

        # 截取屏幕
        screenshot_result = subprocess.run(
            adb_prefix + ["exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=15,
        )

        if screenshot_result.returncode != 0:
            return json.dumps({
                "success": False,
                "error": f"截图失败: {screenshot_result.stderr.decode('utf-8', errors='replace')}",
            }, ensure_ascii=False, indent=2)

        # 保存到临时文件
        with open(temp_path, "wb") as f:
            f.write(screenshot_result.stdout)

        # 上传到 MinIO
        file_size = temp_path.stat().st_size
        object_name = f"android-tests/{project_identifier}/screenshots/screenshot_{timestamp}.png"

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
async def analyze_screenshot_quality(
    object_name: str,
) -> str:
    """
    分析 Android 截图质量

    从 MinIO 下载截图并分析其质量（分辨率、文件大小、清晰度指标）。
    用于排查 AI 视觉识别失败的原因。

    Args:
        object_name: MinIO 中的截图对象路径

    Returns:
        JSON 格式的截图质量分析结果

    Example:
        >>> result = await analyze_screenshot_quality("android-tests/proj_001/screenshots/screenshot_20250613_143000.png")
    """
    try:
        # 从 MinIO 下载截图
        screenshot_bytes = MinIOClient.download_file(object_name)
        file_size = len(screenshot_bytes)

        # 分析文件大小（1080p 手机正常截图约 100-300KB，<50KB 说明质量很低）
        size_quality = "good" if file_size > 100 * 1024 else ("low" if file_size < 50 * 1024 else "medium")

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
            recommendations.append("截图文件过小，建议调高屏幕分辨率: adb shell wm size 1080x1920")
        if is_black:
            recommendations.append("截图可能为全黑，检查设备屏幕是否锁定或 adb 连接是否正常")
        if width > 0 and height > 0 and (width < 720 or height < 1280):
            recommendations.append("截图分辨率较低，建议提高分辨率以获得更好的 AI 识别效果")

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
