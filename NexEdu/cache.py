"""会话缓存文档：临时参考资料，不计入知识库。

与知识库的边界必须清楚：
- **知识库**（workspace/knowledge）：教师有意沉淀的资料，长期存在、参与所有检索、
  可在知识库页管理；
- **缓存**（workspace/cache）：临时丢给智能体当参考的文档（一份练习卷、一节参考
  教案、一张资料照片），**只在近期会话里参与检索**，不进知识库、不出现在知识库列表、
  可一键清空。

为什么要分开：教师常有"这个问题只针对这份材料"的需求。若一律入库，知识库会被
一次性材料污染，长期检索质量下降；若不提供上传，教师又只能靠复制粘贴。
缓存就是这两难的解法——**用完可弃，不污染长期知识**。

实现上刻意保持轻量：缓存规模小（通常几份），用简单的片段匹配打分就够，
不值得为它建 BM25 索引。

启动提速（2026-10-06 优化）：提取出的**文本随元数据落盘**，启动时直接复用，
不再对图片重新 OCR、对音频重新转写——有图片/音频缓存时启动明显更快。
"""

import json
import os
import re
import time

from . import config

_DIR = os.path.join(config.WORKSPACE_DIR, "cache")
_META_PATH = os.path.join(_DIR, "index.json")

MAX_FILES = 20                 # 缓存文件数上限（超出后写入失败，提示先清理）
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_TEXT_STORED = 2_000_000    # 单份缓存落盘文本上限（约 3300 片，足够）
CHUNK_SIZE = 600

# 支持与知识库同样的日常格式（复用同一套解析，教师不用记两套规则）
SUPPORTED_EXT = None           # 延迟从 knowledge 读取，避免导入顺序问题

_files = {}                    # name -> {"path","chunks":[...],"ts","text"}


def _exts():
    global SUPPORTED_EXT
    if SUPPORTED_EXT is None:
        try:
            from . import knowledge
            SUPPORTED_EXT = tuple(knowledge.SUPPORTED_EXT)
        except Exception:
            SUPPORTED_EXT = (".txt", ".md", ".docx", ".pdf", ".pptx",
                             ".png", ".jpg", ".jpeg")
    return SUPPORTED_EXT


def _ensure():
    os.makedirs(_DIR, exist_ok=True)


def _split(text, size=CHUNK_SIZE, overlap=80):
    """按段落边界切分，避免把语义切断。"""
    if not text:
        return []
    if len(text) <= size:
        return [text]
    out, buf = [], ""
    for para in re.split(r"\n+", text):
        if len(buf) + len(para) + 1 <= size:
            buf = (buf + "\n" + para).strip()
        else:
            if buf:
                out.append(buf)
            buf = para
            while len(buf) > size:
                out.append(buf[:size])
                buf = buf[size - overlap:]
    if buf:
        out.append(buf)
    return out


def add(name, data):
    """写入一份缓存文档并切片。返回 (切片数, 说明)。

    解析复用 knowledge 的提取逻辑（pptx / docx / pdf / 图片 OCR 等一致），
    这样教师不需要记两套"什么格式能用"。
    """
    _ensure()
    name = os.path.basename(name or "")
    if not name or not name.lower().endswith(_exts()):
        return 0, "不支持的文件类型"
    if len(data) > MAX_FILE_BYTES:
        return 0, "文件过大（上限 30MB）"
    if name not in _files and len(_files) >= MAX_FILES:
        return 0, f"缓存已满（{MAX_FILES} 份），请先清空或删除"

    path = os.path.join(_DIR, name)
    try:
        with open(path, "wb") as f:
            f.write(data)
        from . import knowledge
        text = knowledge._extract(path)
    except Exception as e:
        return 0, f"解析失败：{e}"

    chunks = [c for c in _split(text or "") if c.strip()]
    _files[name] = {"path": path, "chunks": chunks, "ts": time.time(),
                    "text": (text or "")[:MAX_TEXT_STORED]}
    _save_meta()
    if not chunks:
        why = ""
        try:
            from . import knowledge
            why = (knowledge._STATE.get("message") or "").strip()
        except Exception:
            pass
        return 0, why or "未提取到文本（内容损坏、为扫描件或缺少 OCR 引擎）"
    return len(chunks), ""


def _save_meta():
    _ensure()
    meta = {n: {"path": v["path"], "chunks": len(v["chunks"]),
                "ts": v["ts"], "text": v.get("text", "")}
            for n, v in _files.items()}
    tmp = _META_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _META_PATH)


def load():
    """启动时恢复缓存索引。

    优先复用落盘的提取文本（图片 OCR / 音频转写结果已随元数据保存），
    只有旧版本元数据（无 text 字段）才回退到重新提取。
    """
    if not os.path.exists(_META_PATH):
        return 0
    try:
        with open(_META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return 0
    n = 0
    for name, m in (meta or {}).items():
        p = m.get("path") or os.path.join(_DIR, name)
        if not os.path.exists(p):
            continue
        text = m.get("text")
        if not text:
            # 旧格式元数据：回退重新提取
            try:
                from . import knowledge
                text = knowledge._extract(p)
            except Exception:
                continue
        chunks = [c for c in _split(text or "") if c.strip()]
        if chunks:
            _files[name] = {"path": p, "chunks": chunks, "ts": m.get("ts") or 0,
                            "text": (text or "")[:MAX_TEXT_STORED]}
            n += 1
    return n


def list_files():
    return [{"name": n, "chunks": len(v["chunks"]),
             "ts": time.strftime("%m-%d %H:%M", time.localtime(v["ts"]))}
            for n, v in sorted(_files.items(), key=lambda kv: -kv[1]["ts"])]


def remove(name):
    v = _files.pop(name, None)
    if v:
        try:
            if os.path.exists(v["path"]):
                os.remove(v["path"])
        except OSError:
            pass
        _save_meta()
        return True
    return False


def clear():
    """清空缓存（不影响知识库）。

    做法：**只清索引 + 截断元数据**，不删磁盘文件。
    原因是清空一定要成功——删文件可能因占用、权限或环境策略失败，
    那种失败对教师毫无意义（他要的是"别再引用了"）。磁盘上残留的旧文件
    不再被索引引用，下次同名上传会直接覆盖，也可由教师在缓存目录手动清理。
    """
    n = len(_files)
    _files.clear()
    try:
        with open(_META_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f)
    except OSError:
        pass
    return {"ok": True, "cleared": n}


def search(query, topk=3):
    """在缓存里检索（简单打分：命中词数 + 稀有度近似）。"""
    if not query or not _files:
        return []
    kws = [w for w in re.split(r"[\s,，、。；;：:！!？?（）()《》]+", query)
           if len(w.strip()) >= 2]
    if not kws:
        return []
    hits = []
    for name, v in _files.items():
        for i, c in enumerate(v["chunks"]):
            score = sum(c.count(k) for k in kws)
            if score:
                hits.append((score, name, i, c))
    hits.sort(key=lambda x: -x[0])
    return [{"name": n, "index": i, "text": c[:600]}
            for _s, n, i, c in hits[:topk]]


def render(query, topk=3):
    """渲染成可注入上下文的参考块（标注来自缓存，与知识库区分）。"""
    hits = search(query, topk)
    if not hits:
        return ""
    lines = ["# 临时参考（本次上传的缓存文档，未计入知识库，会话结束可清空）"]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. 【{h['name']}·片段{h['index'] + 1}】{h['text']}")
    return "\n".join(lines)
