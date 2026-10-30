"""本机文件夹选择（用于「教案输出目录」）。

为什么由服务端来选：
- 网页里 `<input webkitdirectory>` 只能拿到相对路径，拿不到绝对路径（浏览器安全限制）；
- pywebview 的原生对话框只在桌面壳里可用，浏览器模式下没有。

因此统一由服务端进程调用系统对话框：
Windows 用 PowerShell + Windows.Forms.FolderBrowserDialog（系统自带、零第三方依赖），
浏览器模式与桌面壳模式下都能用。
"""

import os
import subprocess
import sys


def supported():
    """当前平台是否支持弹出系统文件夹对话框。

    设置环境变量 NEXEDU_NO_DIALOG=1 可显式禁用（无图形界面 / 自动化测试 /
    远程桌面等场景），接口会立即返回“不支持”，而不是弹出对话框后一直等待——
    否则服务端线程会被一个无人点击的对话框挂住。
    """
    if os.environ.get("NEXEDU_NO_DIALOG"):
        return False
    return sys.platform == "win32"


def pick_folder(title="选择教案输出文件夹", timeout=180):
    """弹出系统文件夹选择对话框。返回 (路径, 错误信息)。"""
    if not supported():
        return None, "当前系统不支持系统对话框，可直接在界面填写输出目录路径"

    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$form = New-Object System.Windows.Forms.Form;"
        "$form.TopMost = $true;"
        "$form.WindowState = 'Minimized';"
        "$form.ShowInTaskbar = $false;"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.Description = '" + title.replace("'", "''") + "';"
        "$d.ShowNewFolderButton = $true;"
        "$r = $d.ShowDialog($form);"
        "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { $d.SelectedPath }"
        "$form.Dispose();"
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="ignore")
    except subprocess.TimeoutExpired:
        return None, "选择超时，请重试"
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"无法打开系统对话框：{e}"

    lines = [ln.strip() for ln in (done.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None, "已取消选择"
    path = lines[-1]
    if not os.path.isdir(path):
        return None, f"选择的路径无效：{path}"
    return path, ""
