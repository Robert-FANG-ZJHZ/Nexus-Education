"""图片理解（板书 / 学生作业照片），补齐多模态里最实用的一块。

和 OCR 一样的取舍：**不打包模型，用已配置的服务商**；默认关闭，教师显式开启后才
调用。走 OpenAI 兼容的 `image_url` 消息格式（智谱 / 通义 / OpenAI 等均支持），
图片按 base64 **内联**发送，不落地任何中间文件，也不写入知识库。

诚实边界：
- 只能发给已配置的服务商，且**教师开启即知情**；
- 语音（课堂口语）当前**不支持**——语音转写在标准库下不可实现，需要额外引擎，
  这里不给半成品，也不会说"已支持"。
"""

import base64
import mimetypes
import os

from . import config

_SUPPORTED = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def settings():
    try:
        cur = config.current() or {}
    except Exception:
        cur = {}
    return {"enabled": bool(cur.get("vision_enabled")),
            "model": (cur.get("vision_model") or "").strip()}


def available():
    s = settings()
    return s["enabled"] and bool(config.get_api_key())


def status():
    s = settings()
    if not s["enabled"]:
        return {"ok": False,
                "message": "图片理解默认关闭。开启后图片将发送给你已配置的服务商，"
                           "不会上传校本文档或教师档案。"}
    if not config.get_api_key():
        return {"ok": False, "message": "已开启但未配置 API Key"}
    return {"ok": True, "message": "已开启" + (f"（模型 {s['model']}）" if s["model"] else "")}


def _data_url(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("图片过大（>8MB）")
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())


def describe(path, prompt="请描述这张图片的内容", timeout=90):
    """识别图片。返回 {"text": ..., "error": ...}。"""
    if not os.path.exists(path):
        return {"text": "", "error": "文件不存在"}
    if os.path.splitext(path)[1].lower() not in _SUPPORTED:
        return {"text": "", "error": f"不支持的图片格式：{os.path.splitext(path)[1]}"}
    if not available():
        st = status()
        return {"text": "", "error": st.get("message") or "图片理解未开启"}

    s = settings()
    try:
        url = _data_url(path)
    except Exception as e:
        return {"text": "", "error": f"读取图片失败：{e}"}

    try:
        from . import llm
        client = llm.LLMClient()
        model = s["model"] or config.get_model()
        out = []
        for chunk in client.chat(
                [{"role": "user", "content": [
                    {"type": "text", "text": prompt[:500]},
                    {"type": "image_url", "image_url": {"url": url}}]}],
                temperature=0.2, stream=True, demo_role=None, task="vision"):
            out.append(chunk)
        text = "".join(out).strip()
        return {"text": text or "（模型未返回内容）", "error": ""}
    except Exception as e:
        return {"text": "", "error": f"识别失败：{e}"}
