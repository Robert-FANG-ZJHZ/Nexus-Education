"""三层记忆持久化（参照 CowAgent 的记忆分层，零第三方依赖）。

Phase 2.3 后明确为三层：

- **工作记忆**（working）：当前会话的短期上下文，进程内，不落盘（会话结束即释放）。
- **情景记忆**（episodic）：workspace/memory/YYYY-MM-DD.md —— 当日任务记录；
  以及教案沙盒里的历史教案，按需注入。
- **语义记忆**（semantic）：workspace/MEMORY.md（教师档案）+ workspace/memory/profile.json
  （自动从近期教案提炼的教师画像：偏好的目标动词、常用活动类型、是否习惯分层）。

教师画像是**统计提炼**而非模型推断：只用确定性计数，结果可解释、可一键清除，
不把教师的偏好存成说不清道不明的"黑箱印象"。

注入治理（2026-10-06 优化）：日记与档案随每次请求注入 System Prompt，
重度使用日会重复付费——因此注入前做字数截断（档案与当日记忆各 ≤ 2KB），
并支持档案归一化合并（同语义条目去重、合并学段范围、限量 10 行）。
"""

import datetime
import json
import os
import re
import threading

from . import config

# 工作记忆：当前会话的短期上下文（不落盘，进程内即可）
_WORKING = []

# 并行任务下多个线程会同时记日记 / 更新教师档案，这些是「读-改-写」，
# 必须串行：否则两行正文会交错写进同一份文件，档案里也会出现重复条目。
_LOCK = threading.RLock()

# 画像提炼用的词表（与 negotiation 的可观测动词保持一致口径）
_OBJECTIVE_VERBS = ("说出", "写出", "列举", "设计", "完成", "解释", "背诵",
                    "圈画", "计算", "描述", "归纳", "比较", "判断", "仿写",
                    "批注", "朗读", "填写", "绘制", "分类", "举例", "改写",
                    "概括", "分析", "评价", "运用", "辨认")
_ACTIVITY_TYPES = (("情境导入", ("情境", "导入", "出示")),
                   ("小组合作", ("小组", "合作", "讨论")),
                   ("问题链探究", ("问题链", "探究", "追问")),
                   ("成果展示", ("展示", "汇报", "分享")),
                   ("练笔迁移", ("练笔", "仿写", "迁移")),
                   ("互评反馈", ("互评", "自评", "反馈")))
_LAYER_HINT = ("分层", "基础层", "进阶", "挑战", "基础/", "应用", "创新")

# ---- 注入治理：记忆块注入 System Prompt 的字数上限 ----
_INJECT_MAX = 2000       # 每块（档案 / 当日日记）最多注入 2000 字
_DAILY_TAIL_LINES = 25   # 当日日记只取末尾若干行（越新越相关）

# ---- 档案归一化 ----
_SUBJECT_WORDS = (
    "道德与法治", "信息技术", "信息科技", "综合实践", "通用技术",
    "语文", "数学", "英语", "科学", "历史", "地理", "物理", "化学",
    "生物", "政治", "音乐", "美术", "体育", "劳动", "日语", "俄语")
_PROFILE_MAX_LINES = 10


def ensure():
    os.makedirs(os.path.join(config.WORKSPACE_DIR, "memory"), exist_ok=True)


# ---------------- 工作记忆 ----------------

def working_note(text):
    """记一条工作记忆（当前会话有效，不落盘）。"""
    if text:
        _WORKING.append(str(text)[:200])
        del _WORKING[:-20]          # 只保留最近 20 条
    return list(_WORKING)


def working_context():
    return list(_WORKING)


def working_clear():
    _WORKING.clear()
    return True


def _core_path():
    return os.path.join(config.WORKSPACE_DIR, "MEMORY.md")


def _daily_path(dt=None):
    dt = dt or datetime.date.today()
    return os.path.join(config.WORKSPACE_DIR, "memory", f"{dt:%Y-%m-%d}.md")


def get_core():
    p = _core_path()
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def get_daily(dt=None):
    p = _daily_path(dt)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def _tail_chars(text, limit=_INJECT_MAX):
    """按字数截尾（保留最新内容），避免整份日记/档案撑爆 System Prompt。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    return "…（更早记录未注入，可在「记忆」中查看）\n" + cut


def build_context():
    """组装注入 System Prompt 的记忆块（含近期教案索引，便于跨会话复用）。

    注入治理：档案与当日日记各截断到 ≤2KB，防止重度使用日重复付费。
    """
    parts = []
    pt = profile_text()
    if pt:
        parts.append(f"【教师画像·近 {get_profile().get('samples', 0)} 份教案提炼】{pt}")
    core = _tail_chars(get_core())
    if core:
        parts.append(f"【长期记忆·教师档案】\n{core}")
    daily = get_daily()
    if daily:
        lines = daily.splitlines()
        tail = "\n".join(lines[-_DAILY_TAIL_LINES:])
        parts.append(f"【今日任务记录】\n{_tail_chars(tail)}")
    try:
        from . import lessons                      # 延迟导入，避免循环依赖
        items = lessons.list_lessons()[:5]
        if items:
            lines = "\n".join(f"- {m.get('title') or '未命名'}（id={m.get('id')}）"
                              for m in items)
            parts.append("【近期教案·可检索复用】\n" + lines)
    except Exception:
        pass
    return "\n\n".join(parts)


def record(subject, grade, topic, summary):
    """把一次任务写入日级记忆，并把新的学科/学段并入教师档案。"""
    ensure()
    now = datetime.datetime.now()
    line = f"- {now:%H:%M}｜{subject}｜{grade}｜{topic}｜{summary}\n"
    with _LOCK:
        with open(_daily_path(now), "a", encoding="utf-8") as f:
            f.write(line)
        _update_profile(subject, grade)


def note(text):
    """写入一条日级记忆（通用记录，不更新教师档案）。"""
    ensure()
    now = datetime.datetime.now()
    with _LOCK:
        with open(_daily_path(now), "a", encoding="utf-8") as f:
            f.write(f"- {now:%H:%M}｜{text}\n")


def _profile_path():
    ensure()
    return os.path.join(config.WORKSPACE_DIR, "memory", "profile.json")


def build_profile(max_lessons=8):
    """从近期教案**统计提炼**教师画像（确定性，不调用模型）。

    提炼什么：常用的目标行为动词、常用的活动类型、是否习惯分层设计、
    主要任教学科与学段。用于二次备课时自动沿用教师既有风格。
    """
    try:
        from . import lessons
        items = lessons.list_lessons()[:max_lessons]
    except Exception:
        items = []

    verbs, acts, layers, subjects, grades, n = {}, {}, 0, {}, {}, 0
    for m in items:
        lid = m.get("id")
        if not lid:
            continue
        detail = lessons.get_lesson(lid) if hasattr(lessons, "get_lesson") else None
        text = ""
        if isinstance(detail, dict):
            text = detail.get("content") or ""
        if not text:
            continue
        n += 1
        for v in _OBJECTIVE_VERBS:
            if v in text:
                verbs[v] = verbs.get(v, 0) + 1
        for name, keys in _ACTIVITY_TYPES:
            if any(k in text for k in keys):
                acts[name] = acts.get(name, 0) + 1
        if any(k in text for k in _LAYER_HINT):
            layers += 1
        meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
        s = meta.get("subject") or m.get("subject") or ""
        g = meta.get("grade") or m.get("grade") or ""
        if s:
            subjects[s] = subjects.get(s, 0) + 1
        if g:
            grades[g] = grades.get(g, 0) + 1

    top = lambda d, k=5: [w for w, _ in sorted(d.items(), key=lambda kv: -kv[1])[:k]]
    profile = {
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "samples": n,
        "objective_verbs": top(verbs),
        "activity_types": top(acts),
        "layered_ratio": round(layers / n, 2) if n else 0.0,
        "subjects": top(subjects, 3),
        "grades": top(grades, 3),
    }

    # 教师反馈加权：好评/差评分专家统计，写进画像。
    # 这样画像不只是"我以前怎么写"，还包含"教师认可哪种写法"——
    # 学习是加权的，不是无差别复述历史。
    try:
        from . import metrics
        fb = ((metrics.summary().get("quality") or {})
              .get("feedback_by_expert") or {})
        pref = {}
        for e, d in fb.items():
            up, down = int(d.get("up") or 0), int(d.get("down") or 0)
            if up + down >= 3:                     # 样本太少不下结论
                pref[e] = round((up - down) / (up + down), 2)
        if pref:
            profile["feedback"] = pref
            profile["preferred_expert"] = max(pref.items(), key=lambda kv: kv[1])[0]
    except Exception:
        pass
    try:
        with open(_profile_path(), "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return profile


def get_profile():
    """读取画像。已清除（samples=0 / cleared）时返回空字典，等同未提炼。"""
    p = _profile_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict) or not d.get("samples"):
                return {}
            return d
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def clear_profile():
    """一键清除教师画像（教师对自己数据的控制权）。

    用**覆盖为空画像**而不是删除文件：删除在某些环境会失败（受控目录、
    文件被占用），而"画像已清除"的效果用清空内容即可达成。
    """
    try:
        with open(_profile_path(), "w", encoding="utf-8") as f:
            json.dump({"cleared": True, "samples": 0}, f, ensure_ascii=False)
    except OSError:
        pass
    try:
        global _WORKING
        _WORKING.clear()
    except Exception:
        pass
    return True


def profile_text():
    """把画像渲染成可注入 System Prompt 的短文本。"""
    p = get_profile()
    if not p or not p.get("samples"):
        return ""
    parts = []
    if p.get("objective_verbs"):
        parts.append("常用目标动词：" + "、".join(p["objective_verbs"]))
    if p.get("activity_types"):
        parts.append("常用活动类型：" + "、".join(p["activity_types"]))
    if p.get("layered_ratio", 0) >= 0.5:
        parts.append("习惯做分层设计（基础/进阶/挑战）")
    if p.get("subjects"):
        parts.append("主要任教：" + "、".join(p["subjects"]))
    if p.get("grades"):
        parts.append("学段：" + "、".join(p["grades"]))
    if p.get("preferred_expert"):
        parts.append("教师反馈较好的环节：" + str(p["preferred_expert"]))
    return "；".join(parts)


# ---------------- 教师档案（MEMORY.md）：归一化合并 ----------------

def _split_subject_grade(raw):
    """把一行「任教」记录拆成 (学科, 学段)。

    兼容新旧格式：「语文｜初中七年级」「语文（初中七年级）」「语文（初中）」等。
    """
    raw = (raw or "").strip().strip("｜|、 ")
    if not raw:
        return "", ""
    subject, grade = "", ""
    for w in sorted(_SUBJECT_WORDS, key=len, reverse=True):
        if raw.startswith(w):
            subject = w
            grade = raw[len(w):].strip("｜|（）()、 -—")
            break
    if not subject:
        # 无法识别学科：整行当作学科标签，学段为空
        subject = raw.strip("（）()｜| ")
        grade = ""
    return subject, grade


def _merge_grade(a, b):
    """合并两条学段描述：互为包含时保留更具体（更长）的一条。"""
    a, b = (a or "").strip(), (b or "").strip()
    if not a:
        return b
    if not b:
        return a
    if a == b:
        return a
    if a in b:
        return b
    if b in a:
        return a
    return "、".join((a, b))


def _read_profile_entries():
    """解析 MEMORY.md：返回 (任教条目列表, 其他行列表)。"""
    entries, others = [], []
    p = _core_path()
    if not os.path.exists(p):
        return entries, others
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f.read().splitlines():
                line = line.rstrip()
                m = re.match(r"^\s*-\s*任教[:：]?\s*(.*)$", line)
                if m:
                    s, g = _split_subject_grade(m.group(1))
                    if s:
                        entries.append((s, g))
                else:
                    others.append(line)
    except OSError:
        pass
    return entries, others


def _write_profile_entries(entries, others):
    """重写 MEMORY.md：档案头 + 其他行 + 归一化后的任教条目（限量）。"""
    ensure()
    lines = []
    for line in others:
        if line.strip() and not line.strip().startswith("#"):
            lines.append(line)
    header = "# 教师档案\n" if not any(
        l.strip().startswith("#") for l in others) else ""
    body = header
    for line in lines:
        body += line + "\n"
    kept = list(entries)[-_PROFILE_MAX_LINES:]
    for s, g in kept:
        body += "- 任教：" + "｜".join(x for x in (s, g) if x) + "\n"
    with open(_core_path(), "w", encoding="utf-8") as f:
        f.write(body)


def _update_profile(subject, grade):
    """在 MEMORY.md 中维护「任教」学科/学段清单（归一化合并去重）。"""
    subject = (subject or "").strip()
    grade = (grade or "").strip()
    if not subject and not grade:
        return
    with _LOCK:
        entries, others = _read_profile_entries()
        merged = False
        for i, (s, g) in enumerate(entries):
            if s == subject:
                ng = _merge_grade(g, grade)
                if ng != g:
                    entries[i] = (s, ng)
                merged = True
                break
        if not merged:
            entries.append((subject, grade))
        _write_profile_entries(entries, others)


def compact_profile():
    """一次性整理教师档案：归一化合并重复条目、压缩旧格式、限量行数。

    返回 {"removed": n, "lines": [...]} 供界面展示。
    """
    with _LOCK:
        entries, others = _read_profile_entries()
        before = len(entries)
        # 按（学科）分组合并学段
        merged = []
        seen = {}
        for s, g in entries:
            if s in seen:
                merged[seen[s]] = (s, _merge_grade(merged[seen[s]][1], g))
            else:
                seen[s] = len(merged)
                merged.append((s, g))
        removed = max(0, before - len(merged))
        _write_profile_entries(merged, others)
        return {"removed": removed,
                "lines": ["｜".join(x for x in (s, g) if x)
                          for s, g in merged]}
