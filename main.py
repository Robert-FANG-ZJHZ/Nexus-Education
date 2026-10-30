"""NexEdu 入口：启动本地服务，并以桌面 App 窗口打开界面。

启动策略（优先级从高到低）：
1. pywebview 无边框原生窗口（WebView2 渲染，前端自定义标题栏，
   依赖 pywebview + pythonnet，位于项目 _vendor 目录；打包后内置）；
2. Edge / Chrome 的 --app 模式「无边框应用窗口」；
3. 系统默认浏览器。

窗口控制桥：前端标题栏按钮经 window.pywebview.api.* 调用
win_minimize / win_maximize / win_restore / win_close。
pywebview 模式下前端不发 shutdown beacon，窗口关闭即进程退出。

启动提速：资源包与内置知识库的解密（首次约 3 秒）放到后台线程，
窗口先弹出来，不卡在双击到出窗之间。
"""

import logging
import os
import sys

# 源码运行时加载项目内 _vendor 第三方依赖（pywebview 等）；打包后此目录不存在
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

import shutil
import subprocess
import threading
import time
import webbrowser

# PyInstaller --windowed 模式下 stdout/stderr 可能为 None，print 会抛异常
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from nexedu import agents, config, knowledge
from nexedu.server import make_server


def _find_browser_exe():
    """定位 Edge 或 Chrome 的可执行文件路径（Windows 优先）。"""
    # 1) PATH 中查找
    for name in ("msedge", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    # 2) 注册表 App Paths
    if sys.platform == "win32":
        try:
            import winreg
            for exe in ("msedge.exe", "chrome.exe"):
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe,
                    ) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                        if value and os.path.exists(value):
                            return value
                except OSError:
                    continue
        except ImportError:
            pass
    # 3) 常见安装位置
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/microsoft-edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def open_app_window(url):
    """用 Edge/Chrome 的 App 模式打开无边框应用窗口。成功返回 True。"""
    exe = _find_browser_exe()
    if not exe:
        return False
    args = [
        exe,
        "--app=" + url,
        "--window-size=1280,860",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=(sys.platform != "win32"),
        )
        return True
    except OSError:
        return False


def run_native_window(url):
    """pywebview 无边框原生窗口（阻塞直至窗口关闭，随后进程退出）。

    pywebview / WebView2 不可用时返回 False，由上层走 Edge 兜底。
    注意：窗口控制函数以闭包形式经 window.expose() 暴露给
    window.pywebview.api.*；不能用持有 Window 引用的 js_api 对象
    （pywebview 会递归序列化其公开属性，导致 .NET 对象图被遍历）。
    """
    try:
        import webview
    except Exception:
        return False

    try:
        # 仅响应直接命中拖拽区的鼠标事件，避免误拖（标题栏按钮不受影响）
        webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True

        window = webview.create_window(
            "山海智绘 · NexEdu",
            url=url,
            width=1280,
            height=860,
            min_size=(980, 640),
            frameless=True,
            background_color="#F6F7FB",
        )

        def win_minimize():
            window.minimize()

        def win_maximize():
            window.maximize()

        def win_restore():
            window.restore()

        def win_close():
            window.destroy()

        window.expose(win_minimize, win_maximize, win_restore, win_close)

        def _on_closed():
            os._exit(0)  # 窗口已关，直接退出（服务线程均为 daemon）

        window.events.closed += _on_closed
        webview.start()  # 主线程运行消息循环（WinForms 需 STA 主线程）
        os._exit(0)
    except Exception:
        return False


def setup_logging():
    """日志落盘到 workspace/logs/nexedu.log（便于教师反馈问题时回传排查）。"""
    import logging.handlers
    log_dir = os.path.join(config.WORKSPACE_DIR, "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "nexedu.log"), maxBytes=2 * 1024 * 1024,
            backupCount=3, encoding="utf-8")
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[handler])
    except OSError:
        logging.basicConfig(level=logging.WARNING)


def _prewarm_assets():
    """后台预解密资源包与内置知识库（首次解密约 3 秒，不卡首个页面加载）。"""
    try:
        from nexedu import assets
        assets.read_text("web/index.html")
        assets.read_data("kb/builtin_kb.json")
    except Exception:
        pass


def main():
    setup_logging()

    # 提示词默认随程序内置分发、不落盘（防止内部指令被直接读取）。
    # 仅当环境变量 NEXEDU_EXPORT_PROMPTS=1 时，才以**混淆形式**导出到
    # workspace/prompts/ 供教研员定制，修改后重启生效。
    try:
        agents.export_default_prompts()
    except Exception as e:
        logging.getLogger("nexedu").warning("提示词导出失败：%s", e)

    # 资源预解密入后台：窗口先出，解密后首屏自动就绪
    threading.Thread(target=_prewarm_assets, daemon=True).start()

    host = config.SERVER_HOST

    # 后台线程运行本地 HTTP 服务（前端 + API）；端口占用时自动顺延
    server, port = make_server(host, config.SERVER_PORT)
    url = f"http://{host}:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # 先用「种子 + 本地缓存」秒级预热索引，让第一个提问不必等重建；
    # 随后再由 build_all 后台做增量扫盘（两者不抢同一把锁的长时间窗口）。
    knowledge.warm_async()
    threading.Thread(target=knowledge.build_all, daemon=True).start()

    time.sleep(0.3)  # 等服务就绪

    print("=" * 52)
    print("  山海智绘 · NexEdu  教学评一体化教育智能体")
    print("=" * 52)
    print(f"  服务地址: {url}" + (f"（{config.SERVER_PORT} 被占用，已顺延）"
                                  if port != config.SERVER_PORT else ""))
    print(f"  当前模型: {config.get_model()}"
          + ("（本地模型 · 数据不出本机）" if config.is_local_base_url(
              config.get_base_url()) else ""))
    print("  API Key: " + ("已配置（真实调用）" if config.get_api_key()
                          else "未配置（演示模式，可在「设置」中配置；"
                               "本地模型无需 Key）"
                          if config.is_local_base_url(config.get_base_url())
                          else "未配置（演示模式，可在「设置」中配置）"))
    print(f"  数据目录: {config.WORKSPACE_DIR}")
    print("=" * 52)

    if config.OPEN_BROWSER:
        # 1) pywebview 无边框原生窗口（阻塞至关闭，函数内自行退出进程）
        if run_native_window(url):
            return
        # 2) Edge/Chrome App 窗口兜底；3) 默认浏览器
        if not open_app_window(url):
            webbrowser.open(url)

    try:
        # 主线程保活；窗口关闭时由 /api/shutdown 触发 os._exit 退出
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已停止服务。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
