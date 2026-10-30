"""生成的教案保存（本地沙盒 workspace/lessons/）。

- save_lesson()：保存 Markdown 原文（.json 元数据）+ 同名 Word（.docx）
- list_lessons() / get_lesson() / delete_lesson()
- docx_path()：Word 文件路径（供下载）
- open_folder()：在资源管理器中打开沙盒目录
- cleanup(days)：按时间批量清理（2026-10-06 优化，防止沙盒无限膨胀）
- clear_all()：清痕入口

第三方用户下载 exe 后，教案默认保存在 exe 同级的 workspace/lessons/ 下，
可在界面「我的教案」中查看、重新下载 Word 或删除。
"""

import json
import os
import re
import threading
import time
import uuid

from . import config, docxgen, pptxgen

LESSONS_DIR = os.path.join(config.WORKSPACE_DIR, "lessons")

# 当前请求所属的会话（thread-local）：服务端处理 /api/agent 等请求时设置，
# 这样 AI 在对话中保存的教案会记下来源会话，删除对话时能一并清掉产出文件。
_CTX = threading.local()


def set_session(sid):
    """绑定当前线程的会话 id（请求结束时用 clear_session 解绑）。"""
    _CTX.sid = sid or ""


def clear_session():
    _CTX.sid = ""


def _ctx_session():
    return getattr(_CTX, "sid", "") or ""


# 输出物统一标注（设计文档 §6.4：AI 产出草稿，教师最终裁定）
DISCLAIMER = "AI 生成 · 仅供教学参考，教师确认后方可用于正式教学"


def _ensure_dir():
    os.makedirs(LESSONS_DIR, exist_ok=True)


def _valid_id(lid):
    """教案 id 白名单校验（防路径穿越）：仅允许字母数字、下划线与短横线。"""
    return bool(lid) and re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", lid) is not None


def _meta_path(lid):
    if not _valid_id(lid):   # 非法 id 直接指向不存在的路径
        return os.path.join(LESSONS_DIR, "__invalid__.json")
    return os.path.join(LESSONS_DIR, lid + ".json")


def _safe_title(title):
    """文件名安全化：去 Windows 非法字符，限长。"""
    t = re.sub(r'[\\/:*?"<>|\r\n]', "_", title or "").strip(" ._")
    return t[:60] or "未命名教案"


# 最近产出的文件（供界面提示「已生成 Word」）
LAST_ARTIFACTS = []


def _record_artifact(path, kind="docx"):
    try:
        LAST_ARTIFACTS.append({"kind": kind, "name": os.path.basename(path),
                               "path": path})
        if len(LAST_ARTIFACTS) > 20:
            del LAST_ARTIFACTS[0]
    except Exception:
        pass
    return path


def save_lesson(title, content, meta=None, output_dir=None, session_id=None):
    """保存教案（Markdown + Word 双格式）。

    - 始终在默认沙盒 workspace/lessons 写一份，保证界面「我的教案」可用；
    - 若教师指定了输出目录，再往该目录写一份 Word + Markdown（类似 IDE 的工作区）。

    output_dir 为 None 时取 config.get_output_dir()（留空即用默认沙盒）。
    session_id 为空时取当前请求的会话上下文（用于「删对话即删产出」）。
    返回教案元数据 dict；同标题教案重复保存时新建（保留历史版本）。
    """
    _ensure_dir()
    title = (title or "").strip() or "未命名教案"
    lid = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    m = {
        "id": lid,
        "title": title,
        "content": content or "",
        "created_at": time.time(),
        "meta": meta or {},
        "docx": "%s_%s.docx" % (_safe_title(title), lid[-6:]),
        "session_id": (session_id or _ctx_session() or ""),
    }
    meta_lines = []
    if meta:
        if meta.get("subject") or meta.get("grade"):
            meta_lines.append(
                " · ".join(x for x in (meta.get("subject"), meta.get("grade")) if x))
        if meta.get("topic"):
            meta_lines.append("课题：" + meta["topic"])
    meta_lines.append("生成于 " + time.strftime("%Y-%m-%d %H:%M"))
    meta_lines.append(DISCLAIMER)
    docx_path = os.path.join(LESSONS_DIR, m["docx"])
    docxgen.save_docx(docx_path, title, content, meta_lines=meta_lines)
    _record_artifact(docx_path)
    with open(_meta_path(lid), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)

    # 输出到教师指定的目录（未指定则跳过，等同默认沙盒）
    out = output_dir if output_dir is not None else config.get_output_dir()
    out = (out or "").strip()
    if out and os.path.abspath(out) != os.path.abspath(LESSONS_DIR):
        try:
            os.makedirs(out, exist_ok=True)
            stem = _safe_title(title)
            out_docx = os.path.join(out, "%s_%s.docx" % (stem, lid[-6:]))
            out_md = os.path.join(out, "%s_%s.md" % (stem, lid[-6:]))
            docxgen.save_docx(out_docx, title, content, meta_lines=meta_lines)
            with open(out_md, "w", encoding="utf-8") as f:
                f.write(content or "")
            m["output_dir"] = out
            m["output_docx"] = out_docx
            m["output_md"] = out_md
            _record_artifact(out_docx)
            with open(_meta_path(lid), "w", encoding="utf-8") as f:
                json.dump(m, f, ensure_ascii=False, indent=1)
        except OSError as e:
            m["output_error"] = str(e)
    return m


def open_output_folder():
    """在资源管理器中打开当前输出目录（默认沙盒或教师指定目录）。"""
    path = config.resolve_output_dir()
    try:
        os.startfile(path)      # noqa  Windows 专有
        return True
    except (OSError, AttributeError):
        return False


def list_lessons():
    """教案列表（按时间倒序，不含正文）。"""
    if not os.path.isdir(LESSONS_DIR):
        return []
    out = []
    for fn in os.listdir(LESSONS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(LESSONS_DIR, fn), "r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "id": m.get("id"),
            "title": m.get("title", ""),
            "created_at": m.get("created_at", 0),
            "meta": m.get("meta", {}),
            "has_docx": bool(m.get("docx")) and
            os.path.exists(os.path.join(LESSONS_DIR, m.get("docx", ""))),
        })
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return out


def get_lesson(lid):
    """教案详情（含 Markdown 正文）。"""
    p = _meta_path(lid)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def delete_lesson(lid):
    """删除教案（.json + .docx + 输出目录副本）。"""
    m = get_lesson(lid)
    removed = False
    p = _meta_path(lid)
    if os.path.exists(p):
        try:
            os.remove(p)
            removed = True
        except OSError:
            removed = False
    if m and m.get("docx"):
        dp = os.path.join(LESSONS_DIR, m["docx"])
        if os.path.exists(dp):
            try:
                os.remove(dp)
            except OSError:
                pass
    # 教师指定输出目录里的那份副本也要一起删，避免"删了却还在"
    if m:
        for key in ("output_docx", "output_md"):
            fp = (m.get(key) or "").strip()
            if fp and os.path.isfile(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass
    return removed


def cleanup(days=30):
    """按时间批量清理：删除 N 天前生成的教案（含 Word 与输出目录副本）。

    治理入口：沙盒不会无限膨胀；days 必须 ≥ 1。
    """
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 30
    cutoff = time.time() - days * 86400
    removed = 0
    if not os.path.isdir(LESSONS_DIR):
        return 0
    for fn in os.listdir(LESSONS_DIR):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(LESSONS_DIR, fn)
        try:
            with open(p, "r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if float(m.get("created_at") or 0) > cutoff:
            continue
        lid = m.get("id") or fn[:-5]
        if _valid_id(lid) and delete_lesson(lid):
            removed += 1
    return removed


def clear_all():
    """清痕入口：删除沙盒内全部教案（.json 及其联动的 docx/输出副本）。"""
    if not os.path.isdir(LESSONS_DIR):
        return 0
    n = 0
    for fn in list(os.listdir(LESSONS_DIR)):
        if fn.endswith(".json"):
            lid = fn[:-5]
            if _valid_id(lid) and delete_lesson(lid):
                n += 1
    return n


def delete_by_session(sid):
    """删除某个对话产出的全部文件（教案 .json / .docx / 输出目录副本）。

    教师删掉一条历史对话时，这条对话里生成的本地文件不应继续留在磁盘上。
    返回被删除的文件数。
    """
    sid = (sid or "").strip()
    if not sid or not os.path.isdir(LESSONS_DIR):
        return 0
    n = 0
    for fn in os.listdir(LESSONS_DIR):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(LESSONS_DIR, fn)
        try:
            with open(p, "r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if (m.get("session_id") or "") != sid:
            continue
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
        if m.get("docx"):
            dp = os.path.join(LESSONS_DIR, m["docx"])
            if os.path.isfile(dp):
                try:
                    os.remove(dp)
                    n += 1
                except OSError:
                    pass
        for key in ("output_docx", "output_md"):
            fp = (m.get(key) or "").strip()
            if fp and os.path.isfile(fp):
                try:
                    os.remove(fp)
                    n += 1
                except OSError:
                    pass
    return n


def docx_path(lid):
    """返回教案 Word 文件绝对路径（不存在返回 None）。"""
    m = get_lesson(lid)
    if not m or not m.get("docx"):
        return None
    p = os.path.join(LESSONS_DIR, m["docx"])
    return p if os.path.exists(p) else None


def export_docx_bytes(title, content, meta=None):
    """不落盘直接生成 Word 字节（用于界面「导出 Word」即时下载）。"""
    meta_lines = []
    if meta:
        if meta.get("subject") or meta.get("grade"):
            meta_lines.append(
                " · ".join(x for x in (meta.get("subject"), meta.get("grade")) if x))
        if meta.get("topic"):
            meta_lines.append("课题：" + meta["topic"])
    meta_lines.append(DISCLAIMER)
    return docxgen.docx_bytes(title, content, meta_lines=meta_lines)


def export_pptx_bytes(title, content):
    """不落盘直接生成 PPT 字节（用于界面「导出 PPT」即时下载）。

    教案 Markdown 按标题层级自动分页：一级标题→章节封面，二级标题→内容页，
    列表/段落→要点。投影场景不渲染表格与引用核对标记（可追溯性由 Word 承载）。
    """
    return pptxgen.pptx_bytes(title or "未命名演示", content or "")


def open_folder():
    """在资源管理器中打开教案沙盒目录（Windows）。"""
    _ensure_dir()
    try:
        os.startfile(LESSONS_DIR)  # noqa  Windows 专有
        return True
    except (OSError, AttributeError):
        return False
