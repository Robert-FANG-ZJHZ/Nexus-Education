"""轻量知识结构索引（补齐"知识结构"这块地基）。

诚实的定位：这是**结构化索引**，不是真正的知识图谱。
它做的事：把切片的来源文件名解析成「学科 / 学段 / 版本 / 册次」结构化字段，
再从标题与高频术语里抽知识点，建立 课题 → 相关切片 的关联。

能带来的实际收益：
- 检索按学科/学段前置过滤，语文老师备语文课时不再被数学教材干扰；
- 「相关知识点」可作为二次检索的扩展词（比同义词表更贴近教材体系）；
- 界面能告诉教师"这份依据来自哪一册教材"，引用更可核对。

做不到的事：不表达知识点之间的先修关系与认知层级——那需要真正的课标知识库
（结构化数据 + 人工校准），不是从文件名能推断出来的。这一点不夸大。
"""

import json
import os
import re
from collections import defaultdict

from . import config

_PATH = os.path.join(config.WORKSPACE_DIR, "kg.json")

_SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理",
             "道法", "政治", "科学", "信息技术", "音乐", "美术", "体育"]
_GRADES = [("七年级", 7), ("八年级", 8), ("九年级", 9),
           ("一年级", 1), ("二年级", 2), ("三年级", 3),
           ("四年级", 4), ("五年级", 5), ("六年级", 6),
           ("高一", 10), ("高二", 11), ("高三", 12)]
_EDITIONS = ["人教版", "部编版", "北师大版", "苏教版", "外研版", "浙教版", "沪教版"]

_STOP = set("的了和与在是中不为上下之其并或对把被能会要有第单元课时教学")
# 通用词兜底：df 过滤之外再挡一层，避免"一个/可以/什么"这类词占据知识点榜首
_GENERIC = {"一个", "可以", "什么", "自己", "我们", "他们", "你们", "这个", "那个",
            "表示", "我国", "如图", "生活", "进行", "通过", "没有", "就是", "这些",
            "那些", "这样", "那样", "一下", "一些", "因为", "所以", "但是", "如果",
            "已经", "还有", "之后", "之前", "中的", "其中", "对于", "关于", "以及"}

_kg = {"subjects": {}, "topics": {}, "built": ""}


def parse_source(source):
    """从来源文件名解析结构化字段。解析不出就返回空值，不硬猜。"""
    name = os.path.splitext(os.path.basename(source or ""))[0]
    out = {"subject": "", "grade": "", "edition": "", "volume": ""}
    for s in _SUBJECTS:
        if s in name:
            out["subject"] = s
            break
    for label, _n in _GRADES:
        if label in name:
            out["grade"] = label
            break
    if not out["grade"]:
        m = re.search(r"(\d{1,2})年级", name)
        if m:
            out["grade"] = m.group(1) + "年级"
    for e in _EDITIONS:
        if e in name:
            out["edition"] = e
            break
    if "上" in name[-6:]:
        out["volume"] = "上册"
    elif "下" in name[-6:]:
        out["volume"] = "下册"
    return out


def _terms(text, limit=12):
    """抽术语：2~4 字汉字片段，去掉虚词与超高频词。"""
    core = "".join(re.findall(r"[\u4e00-\u9fff]", text or ""))
    freq = defaultdict(int)
    for size in (2, 3, 4):
        for i in range(len(core) - size + 1):
            g = core[i:i + size]
            if any(c in _STOP for c in g):
                continue
            freq[g] += 1
    # 频次过低无统计意义，过高多为通用词；取中间段
    ranked = sorted(freq.items(), key=lambda kv: -kv[1])
    picked = [w for w, c in ranked if c >= 2][:limit]
    return picked


def build(chunks):
    """从切片重建知识结构索引。

    术语要经过**文档频次过滤**：只按频次取会剩下一堆"年级/我们/可以"这类通用词，
    对二次检索毫无用处。保留"出现在少数切片里、但切片内反复出现"的词——
    那才是教材里的专有内容。
    """
    subjects = defaultdict(list)
    per_chunk = []
    for c in chunks:
        meta = parse_source(c.get("source", ""))
        c["kg"] = meta                              # 回写，供检索时前置过滤
        if meta["subject"]:
            subjects[meta["subject"]].append(c.get("id", ""))
        per_chunk.append((c.get("id", ""),
                          set(_terms(c.get("source", "") + " "
                                     + (c.get("text", "")[:200]), limit=40))))

    n = max(len(per_chunk), 1)
    df = defaultdict(int)
    for _cid, ts in per_chunk:
        for t in ts:
            df[t] += 1
    hi = max(3, int(n * 0.01))                      # 出现在 >1% 切片里 → 太通用
    kept = {t for t, d in df.items()
            if 2 <= d <= hi and t not in _GENERIC}

    topics = defaultdict(list)
    for cid, ts in per_chunk:
        for t in ts & kept:
            topics[t].append(cid)
    _kg["subjects"] = {k: v[:400] for k, v in subjects.items()}
    _kg["topics"] = {k: v[:120] for k, v in
                     sorted(topics.items(), key=lambda kv: -len(kv[1]))[:300]}
    import time
    _kg["built"] = time.strftime("%Y-%m-%d %H:%M")
    return _kg


def related(topic, subject="", limit=8):
    """与课题相关的知识点词（可用于检索扩展）。"""
    if not _kg["topics"]:
        return []
    core = "".join(re.findall(r"[\u4e00-\u9fff]", topic or ""))
    if not core:
        return []
    grams = {core[i:i + 2] for i in range(len(core) - 1)}
    scored = []
    for t in _kg["topics"]:
        if t in core or core in t:
            scored.append((2.0, t))
            continue
        tg = {t[i:i + 2] for i in range(len(t) - 1)}
        if grams & tg:
            scored.append((len(grams & tg) / max(len(grams), 1), t))
    scored.sort(key=lambda x: -x[0])
    return [t for _s, t in scored[:limit]]


def filter_ids(subject="", grade=""):
    """按学科/学段给出候选切片 id（用于检索前置过滤）。"""
    if not subject:
        return None
    ids = set(_kg.get("subjects", {}).get(subject, []))
    return ids or None


# ---------------- 课标知识结构（先修关系与认知层级）----------------
#
# 明确边界：知识点之间的**先修关系**和**认知层级**不是能从文件名或共现统计里
# 推断出来的，它需要依据课标由人来校准。因此这里做成一份**可编辑的数据文件**，
# 随包只放模板与少量示例；未配置时如实报告"未配置"，绝不假装已知。
_CURR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "curriculum.json")
_curriculum = None


def load_curriculum(force=False):
    """加载课标数据（懒加载）。文件缺失或为空时返回 {}。"""
    global _curriculum
    if _curriculum is not None and not force:
        return _curriculum
    try:
        with open(_CURR_PATH, "r", encoding="utf-8") as f:
            obj = json.load(f)
        _curriculum = (obj.get("subjects") or {}) if isinstance(obj, dict) else {}
    except Exception:
        _curriculum = {}
    return _curriculum


def curriculum_status():
    cur = load_curriculum()
    n_topics = sum(len((v.get("prereq") or {})) for v in cur.values())
    return {"configured": bool(cur), "path": _CURR_PATH,
            "subjects": sorted(cur.keys()), "topics": n_topics,
            "message": (f"已配置课标数据：{len(cur)} 个学科、{n_topics} 个知识点"
                        if cur else
                        "未配置课标数据——先修关系与认知层级需人工校准，"
                        "可在 nexedu/data/curriculum.json 中补充")}


def _match_topic(topic, keys):
    """在知识点集合里匹配课题：完全相等或互相包含。"""
    t = (topic or "").strip()
    if not t:
        return None
    if t in keys:
        return t
    for k in keys:
        if k and (k in t or t in k):
            return k
    return None


def prerequisites(topic, subject=""):
    """查某课题的先修知识点。未配置或无匹配时返回空列表（不编造）。"""
    cur = load_curriculum()
    if not cur:
        return []
    pool = {}
    subs = [subject] if subject and subject in cur else list(cur.keys())
    for s in subs:
        pool.update(cur.get(s, {}).get("prereq") or {})
    key = _match_topic(topic, pool.keys())
    return list(pool.get(key) or []) if key else []


def cognitive_level(topic, subject=""):
    """查某课题的认知层级（识记/理解/应用/分析/评价/创造）。"""
    cur = load_curriculum()
    if not cur:
        return ""
    pool = {}
    subs = [subject] if subject and subject in cur else list(cur.keys())
    for s in subs:
        pool.update(cur.get(s, {}).get("level") or {})
    key = _match_topic(topic, pool.keys())
    return pool.get(key) or "" if key else ""


def status():
    st = {"built": _kg.get("built", ""),
          "subjects": sorted(_kg.get("subjects", {}).keys()),
          "topics": len(_kg.get("topics", {}))}
    st["curriculum"] = curriculum_status()
    return st
