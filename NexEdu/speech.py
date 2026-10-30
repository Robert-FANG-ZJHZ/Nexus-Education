"""语音转写（课堂口语场景）。

态度与 OCR 一致：**不假装支持**。语音转写在标准库下不可实现，
也没有像 tesseract 那样广为安装的通用命令行引擎，因此：

- **本地路径**：检测常见的本地 ASR 命令行工具（whisper / whisper-cli /
  whisper.cpp 的 main.exe / vosk-transcriber），有就调用，全部在本机完成；
- **授权联网路径**：走已配置的服务商的音频转写接口（OpenAI 兼容
  `/audio/transcriptions`），**默认关闭**，需教师显式开启；
- **都不可用时**：明确告诉教师"当前环境无法转写，需要什么才能用"，
  而不是把音频收下、却在检索时永远搜不到——那是比报错更糟的欺骗。

音频只在教师开启联网转写时才离开本机，且走的是同一个已配置的服务商。

2026-10-06 修复：whisper.cpp 官方二进制常叫 main.exe / whisper.exe，
且默认把结果写入 `<输入名>.txt` 而非 stdout——现在按所在目录识别
main.exe，并在 stdout 为空时回退读取输出文件。
"""

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

from . import config

SUPPORTED_EXT = (".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".flac")

_TIMEOUT = 300          # 转写可能很慢（一节课 40 分钟）
_MAX_BYTES = 200 * 1024 * 1024


def _local_engine():
    for cmd in ("whisper", "whisper-cli", "vosk-transcriber"):
        p = shutil.which(cmd)
        if p:
            return p
    # whisper.cpp 官方二进制常叫 main.exe / whisper.exe：按所在目录识别，
    # 避免 PATH 里其它项目的同名可执行文件被误当成 ASR 引擎
    for cmd in ("main", "whisper", "whisper-cli"):
        p = shutil.which(cmd)
        if p and "whisper" in os.path.dirname(p).lower():
            return p
    return None


def settings():
    try:
        cur = config.current() or {}
    except Exception:
        cur = {}
    return {"cloud": bool(cur.get("speech_cloud")),
            "model": (cur.get("speech_model") or "whisper-1").strip()}


def available():
    return bool(_local_engine()) or (settings()["cloud"] and bool(config.get_api_key()))


def status():
    eng = _local_engine()
    s = settings()
    if eng:
        return {"ok": True, "mode": "local",
                "message": f"检测到本地语音引擎（{os.path.basename(eng)}），"
                           f"转写全程在本机完成"}
    if s["cloud"] and config.get_api_key():
        return {"ok": True, "mode": "cloud",
                "message": "已开启联网转写：音频将发送给你已配置的服务商"}
    if s["cloud"]:
        return {"ok": False, "message": "已开启联网转写但未配置 API Key"}
    return {"ok": False, "mode": "none",
            "message": "当前无法转写语音：未检测到本地 ASR 引擎，且联网转写未开启。"
                       "可选做法——① 安装 whisper.cpp / vosk 等本地引擎（数据不出本机）；"
                       "② 在设置中开启联网转写（音频会发往已配置的服务商）"}


def _read_output_file(path):
    """whisper.cpp 默认把结果写到 <输入名>.txt；stdout 为空时回退读取。"""
    for cand in (os.path.splitext(path)[0] + ".txt", path + ".txt"):
        if os.path.exists(cand):
            try:
                with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                    txt = f.read().strip()
                if txt:
                    return txt
            except OSError:
                pass
    return ""


def transcribe(path, language="zh"):
    """转写音频。返回 {"text": ..., "error": ..., "mode": ...}。"""
    if not os.path.exists(path):
        return {"text": "", "error": "文件不存在"}
    if os.path.getsize(path) > _MAX_BYTES:
        return {"text": "", "error": "音频过大（>200MB）"}

    eng = _local_engine()
    if eng:
        try:
            # 不同引擎参数各异：vosk 不支持 --language；whisper.cpp 的 --language
            # 是通用且向后兼容的。stdout 与输出文件双路读取，取非空者。
            out = subprocess.run(
                [eng, path, "--language", language],
                capture_output=True, text=True, timeout=_TIMEOUT,
                encoding="utf-8", errors="ignore")
            txt = (out.stdout or "").strip()
            if not txt:
                txt = _read_output_file(path)
            if txt:
                return {"text": txt, "error": "", "mode": "local"}
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
            return {"text": "", "error": f"本地转写失败：{e}", "mode": "local"}

    s = settings()
    if not (s["cloud"] and config.get_api_key()):
        return {"text": "", "error": (status()["message"]), "mode": "none"}

    # 联网转写：OpenAI 兼容 multipart 上传（标准库手工拼 multipart，不引第三方）
    try:
        base = (config.get_base_url() or "").rstrip("/")
        url = base + "/audio/transcriptions"
        boundary = "----nexedu-speech-boundary"
        with open(path, "rb") as f:
            audio = f.read()
        name = os.path.basename(path)
        body = b""
        def field(k, v):
            nonlocal body
            body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{k}"\r\n\r\n{v}\r\n').encode()
        field("model", s["model"])
        body += (f'--{boundary}\r\nContent-Disposition: form-data; '
                 f'name="file"; filename="{name}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n").encode()
        body += audio + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Authorization": "Bearer " + (config.get_api_key() or "none")})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        return {"text": (data.get("text") or "").strip(), "error": "",
                "mode": "cloud"}
    except urllib.error.HTTPError as e:
        return {"text": "", "error": f"转写接口返回 {e.code}", "mode": "cloud"}
    except Exception as e:
        return {"text": "", "error": f"转写失败：{e}", "mode": "cloud"}
