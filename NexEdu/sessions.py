"""历史对话（会话）管理：本地 JSON 持久化。

每个会话一个文件：workspace/sessions/{id}.json
结构：{id, title, created_at, updated_at,
       messages: [{role, content, ts, expert?, title?}]}

治理（2026-10-06 优化）：会话数量设上限（MAX_SESSIONS），
超出自动删除最旧，避免目录无限膨胀、列表接口越来越慢。
"""

import json
import os
import time
import uuid

from . import config

SESSIONS_DIR = os.path.join(config.WORKSPACE_DIR, "sessions")

MAX_SESSIONS = 200       # 会话数量上限：超出删除最旧的


def _ensure_dir():
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def _path(sid):
    return os.path.join(SESSIONS_DIR, f"{sid}.json")


def _valid_sid(sid):
    return bool(sid) and sid.isalnum() and len(sid) <= 40


def _prune():
    """保留最近 MAX_SESSIONS 个会话，超出部分删除最旧。"""
    try:
        files = [os.path.join(SESSIONS_DIR, fn)
                 for fn in os.listdir(SESSIONS_DIR) if fn.endswith(".json")]
        if len(files) <= MAX_SESSIONS:
            return
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for p in files[MAX_SESSIONS:]:
            try:
                os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def list_sessions():
    """返回会话概要列表（按 updated_at 倒序），不含消息体。"""
    if not os.path.isdir(SESSIONS_DIR):
        return []
    out = []
    for fn in os.listdir(SESSIONS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS_DIR, fn), "r", encoding="utf-8") as f:
                s = json.load(f)
            out.append({
                "id": s.get("id") or fn[:-5],
                "title": s.get("title") or "未命名对话",
                "updated_at": s.get("updated_at") or 0,
                "count": len(s.get("messages") or []),
            })
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out


def create_session(title="新对话"):
    """新建会话，返回会话数据。"""
    _ensure_dir()
    sid = uuid.uuid4().hex[:10]
    now = time.time()
    data = {
        "id": sid,
        "title": (title or "新对话").strip()[:40] or "新对话",
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    with open(_path(sid), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _prune()
    return data


def get_session(sid):
    """读取会话详情；不存在返回 None。"""
    if not _valid_sid(sid):
        return None
    p = _path(sid)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def append_messages(sid, messages):
    """把若干条消息追加到会话；返回更新后的会话，不存在返回 None。"""
    data = get_session(sid)
    if data is None:
        return None
    data.setdefault("messages", []).extend(messages)
    data["updated_at"] = time.time()
    _ensure_dir()
    with open(_path(sid), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def delete_session(sid):
    """删除会话文件，返回是否成功。"""
    if not _valid_sid(sid):
        return False
    p = _path(sid)
    if os.path.exists(p):
        try:
            os.remove(p)
            return True
        except OSError:
            return False
    return False


def clear_all():
    """删除全部会话文件（清痕入口）。返回删除数量。"""
    if not os.path.isdir(SESSIONS_DIR):
        return 0
    n = 0
    for fn in os.listdir(SESSIONS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            os.remove(os.path.join(SESSIONS_DIR, fn))
            n += 1
        except OSError:
            pass
    return n
