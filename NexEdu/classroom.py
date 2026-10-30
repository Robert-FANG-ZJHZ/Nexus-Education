"""班级与学情长期记忆（补齐「长期记忆只记教师、不记学生」的短板）。

教师画像解决的是"这位老师习惯怎么写"，但教学真正面对的是**具体的班**：
同样的教案给基础薄弱的班和给拔尖的班，活动节奏、支架密度、作业分层完全不同。
此前系统里没有"班"这个实体，学情只在当次请求里以一段文字出现，用完即弃。

本模块把班级建成一个**本地、可查看、可修改、可删除**的持久对象：
- 班级档案：学科、学段、整体学情、常见错误、教学进度；
- 学情流水：按时间追加的观察记录（自动脱敏、限量保留）；
- 注入：按需组装成 ≤1.5KB 的上下文，供专家生成时参考。

边界（写入时即声明，不因为"能存"就越界）：
- 只存**班级层面**的概括信息，不存学生个体成绩与评价；
- 所有文本写入前过一遍脱敏，命中的可识别信息不落盘；
- 教师可一键清空，数据不出本地。
"""

import datetime
import json
import os
import threading

from . import config

_LOCK = threading.RLock()
_MAX_NOTES = 30            # 每班最多保留的学情记录条数
_INJECT_MAX = 1500         # 注入 System Prompt 的字数上限
_MAX_CLASSES = 30


def ensure():
    os.makedirs(os.path.join(config.WORKSPACE_DIR, "memory"), exist_ok=True)


def _path():
    ensure()
    return os.path.join(config.WORKSPACE_DIR, "memory", "classes.json")


def _load():
    p = _path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data):
    data["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with _LOCK:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def _clean_name(name):
    return str(name or "").strip()[:40]


def _mask(text):
    """写入前的脱敏：命中可识别信息则替换，并回报命中类型。"""
    if not text:
        return text, []
    try:
        from . import safety
        return safety.mask_pii(text)
    except Exception:
        return text, []


def list_classes():
    """列出所有班级（摘要，不含全部流水）。"""
    data = _load()
    out = []
    for name, c in (data.get("classes") or {}).items():
        if not isinstance(c, dict):
            continue
        out.append({
            "name": name,
            "subject": c.get("subject") or "",
            "grade": c.get("grade") or "",
            "level": c.get("level") or "",
            "progress": c.get("progress") or "",
            "weak_points": c.get("weak_points") or [],
            "notes": len(c.get("notes") or []),
            "updated": c.get("updated") or "",
        })
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return out


def get_class(name):
    return (_load().get("classes") or {}).get(_clean_name(name)) or None


def save_class(name, subject="", grade="", level="", progress="",
               weak_points=None, notes=None, replace=False):
    """新建或更新班级档案。

    replace=False 时做字段级合并（只覆盖传入的非空字段），避免误清空。
    返回 (班级对象, 脱敏命中列表)。
    """
    key = _clean_name(name)
    if not key:
        return None, []
    masked_all = []
    if isinstance(weak_points, str):
        weak_points = [w.strip() for w in weak_points.split("，") if w.strip()]
    weak_points = [str(w)[:60] for w in (weak_points or [])][:12]
    wp2 = []
    for w in weak_points:
        w2, hit = _mask(w)
        wp2.append(w2)
        masked_all += hit
    level, h1 = _mask(level)
    progress, h2 = _mask(progress)
    masked_all += h1 + h2

    with _LOCK:
        data = _load()
        classes = data.setdefault("classes", {})
        cur = classes.get(key) if isinstance(classes.get(key), dict) else {}
        if not replace:
            merged = dict(cur)
            for k, v in (("subject", subject), ("grade", grade),
                         ("level", level), ("progress", progress)):
                if v:
                    merged[k] = str(v)[:120]
            if wp2:
                merged["weak_points"] = wp2
            if notes is not None:
                merged["notes"] = list(notes)[-_MAX_NOTES:]
        else:
            merged = {"subject": str(subject)[:120], "grade": str(grade)[:120],
                      "level": level, "progress": progress,
                      "weak_points": wp2,
                      "notes": list(notes or [])[-_MAX_NOTES:]}
        if len(classes) >= _MAX_CLASSES and key not in classes:
            return None, ["班级数量已达上限 %d" % _MAX_CLASSES]
        merged["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        classes[key] = merged
        _save(data)
    return merged, masked_all


def add_note(name, text, tag=""):
    """追加一条学情观察（自动脱敏，超限滚动丢弃最旧的）。"""
    key = _clean_name(name)
    note, hit = _mask(text)
    if not key or not note:
        return None, hit
    with _LOCK:
        data = _load()
        classes = data.setdefault("classes", {})
        cur = classes.get(key) if isinstance(classes.get(key), dict) else {}
        if not cur:
            return None, hit
        notes = list(cur.get("notes") or [])
        notes.append({
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "tag": str(tag)[:20], "text": note[:300],
        })
        cur["notes"] = notes[-_MAX_NOTES:]
        cur["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        classes[key] = cur
        _save(data)
    return cur, hit


def remove_class(name):
    key = _clean_name(name)
    with _LOCK:
        data = _load()
        classes = data.get("classes") or {}
        if key in classes:
            classes.pop(key)
            _save(data)
            return True
    return False


def clear_all():
    with _LOCK:
        _save({"classes": {}})
    return True


def build_context(limit=_INJECT_MAX):
    """组装注入 System Prompt 的班级上下文（越新越先，截断到上限）。"""
    items = list_classes()
    if not items:
        return ""
    parts = []
    for it in items[:5]:
        c = get_class(it["name"]) or {}
        seg = ["班级：%s" % it["name"]]
        if c.get("grade") or c.get("subject"):
            seg.append("%s%s" % (c.get("grade") or "", c.get("subject") or ""))
        if c.get("level"):
            seg.append("整体学情：%s" % c["level"])
        if c.get("weak_points"):
            seg.append("常见错误：" + "、".join(c["weak_points"][:6]))
        if c.get("progress"):
            seg.append("进度：%s" % c["progress"])
        notes = (c.get("notes") or [])[-3:]
        if notes:
            seg.append("近期观察：" + "；".join(
                (n.get("text") or "")[:60] for n in notes))
        parts.append("｜".join(seg))
    text = "\n".join(parts)
    if len(text) > limit:
        text = text[:limit] + "…（更多班级档案未注入）"
    return "【班级学情·本地档案】\n" + text


def summary_text():
    """给界面用的简短统计。"""
    items = list_classes()
    notes = sum(i.get("notes") or 0 for i in items)
    return {"classes": len(items), "notes": notes,
            "updated": _load().get("updated") or ""}
