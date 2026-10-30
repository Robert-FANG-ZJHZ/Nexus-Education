"""多方案生成与择优（补齐「只有一个方案、谈不上选择」的短板）。

超级智能体与"能生成内容的工具"的一条分水岭：前者会给**候选 + 理由 + 代价**，
后者只给一个看起来还不错的答案。教学尤其如此——同一课题，探究式、任务式、
讲授式各有适用条件，选错的代价是整节课的效率。

本模块**零模型调用**：先用确定性规则生成 3 条候选路线，按目标对齐、认知层级
覆盖、时长可行性、分层覆盖、新颖度打分排序，给出推荐与适用条件。
它不替代专家生成细节，而是先帮教师把"走哪条路"这件事定下来——
路线定了，后续生成才有方向；路线没定，生成得再漂亮也可能是错的。

刻意不做的事：不假装"自动帮你选好不用看"。每条候选都列出代价与不适用场景，
最终选择权在教师。
"""

import re

# 三条候选路线：(id, 名称, 取向, 环节, 评价方式, 典型时长, 新颖度, 认知层级)
_ROUTES = (
    ("inquiry", "探究式·问题链驱动", "以问题链推动学生自己得出结论",
     ("情境导入·提出核心问题", "分组探究·收集证据",
      "全班论证·形成结论", "迁移应用·新问题检验"),
     "探究记录单 + 论证表现量规", 45, 0.9,
     ("分析", "评价", "创造")),
    ("task", "任务式·真实情境产出", "围绕一个可交付的真实任务组织学习",
     ("发布任务与评价标准", "拆解任务·分工", "实践制作·教师巡回指导",
      "成果展示·同伴互评", "复盘改进"),
     "作品量规 + 互评反馈表", 60, 0.85,
     ("应用", "分析", "创造", "评价")),
    ("direct", "结构式·精讲多练", "先搭结构再分层练习，短时高效",
     ("前测诊断·暴露误区", "精讲结构·示范思路",
      "分层练习·基础/进阶/挑战", "当堂检测·即时补救"),
     "当堂检测 + 分层完成度", 40, 0.5,
     ("记忆", "理解", "应用", "分析")),
)

_WEIGHTS = {"alignment": 0.30, "cognitive": 0.20, "feasible": 0.25,
            "layered": 0.15, "novelty": 0.10}

_COGNITIVE = ("记忆", "理解", "应用", "分析", "评价", "创造")

_WEAK_HINTS = ("薄弱", "基础差", "学困", "跟不上", "参差", "两极", "慢")
_STRONG_HINTS = ("拔尖", "基础好", "超前", "能力强", "优生")


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _parse_minutes(text, default=45):
    m = re.search(r"(\d+)\s*(?:分钟|min|mins|课时)", text or "")
    if not m:
        return default
    n = int(m.group(1))
    return n if 10 <= n <= 180 else default


def _score_route(route, minutes, student_info, layers_wanted):
    rid, name, orient, steps, assess, typical, novelty, cog = route
    # 时长可行性：偏离典型时长越远越低
    feasible = _clamp(1.0 - abs(minutes - typical) / 60.0)
    cognitive = _clamp(len(cog) / 4.0)
    novelty = novelty
    # 分层：任务式与结构式天然易分层；探究式需额外支架
    layered = 0.7 if rid == "inquiry" else 0.85
    if layers_wanted:
        layered = _clamp(layered + 0.15 if rid != "inquiry" else layered + 0.05)
    # 目标对齐：默认各路线都能对齐，探究/任务对素养目标更友好
    alignment = 0.85 if rid in ("inquiry", "task") else 0.8
    info = student_info or ""
    if any(h in info for h in _WEAK_HINTS):
        # 基础薄弱：结构式更稳，探究式需降速
        if rid == "direct":
            alignment, feasible = 0.9, _clamp(feasible + 0.1)
        elif rid == "inquiry":
            feasible = _clamp(feasible - 0.15)
    if any(h in info for h in _STRONG_HINTS):
        if rid in ("inquiry", "task"):
            alignment = 0.9
    scores = {"alignment": round(alignment, 2), "cognitive": round(cognitive, 2),
              "feasible": round(feasible, 2), "layered": round(layered, 2),
              "novelty": round(novelty, 2)}
    total = sum(scores[k] * _WEIGHTS[k] for k in _WEIGHTS)
    return scores, round(total * 100, 1)


def generate(message, subject="", grade="", topic="", minutes=None,
             student_info="", layered=None):
    """生成候选教学路线并排序。

    返回 dict：
      topic         识别出的课题
      minutes       采用的教学时长
      candidates    [{id,name,orientation,steps,assessment,scores,score,
                      costs,fit,unfit}]
      recommended   推荐路线 id
      reason        推荐理由（可展示给教师）
    """
    text = message or ""
    if minutes is None:
        minutes = _parse_minutes(text)
    minutes = int(minutes)
    if not topic:
        m = re.search(r"《(.+?)》", text)
        topic = m.group(1).strip() if m else ""
    if layered is None:
        layered = ("分层" in text) or any(
            h in (student_info or "") for h in _WEAK_HINTS + _STRONG_HINTS)

    cands = []
    for route in _ROUTES:
        rid, name, orient, steps, assess, typical, novelty, cog = route
        scores, total = _score_route(route, minutes, student_info, layered)
        cands.append({
            "id": rid, "name": name, "orientation": orient,
            "steps": list(steps), "assessment": assess,
            "cognitive_levels": list(cog), "typical_minutes": typical,
            "scores": scores, "score": total,
        })
    cands.sort(key=lambda c: -c["score"])

    for c in cands:
        if c["id"] == "inquiry":
            c["costs"] = "耗时较长，需教师有较强的追问与收束能力"
            c["fit"] = "概念建构、规律探究、需要学生自己得出结论的内容"
            c["unfit"] = "课时紧张、班级自学纪律较弱时不建议"
        elif c["id"] == "task":
            c["costs"] = "准备量大，作品评价耗时，进度不易控"
            c["fit"] = "单元综合、写作与实践类、可产出可见成果的内容"
            c["unfit"] = "知识点密集、必须当堂落实的新授课"
        else:
            c["costs"] = "学生主动建构不足，容易变成教师主导"
            c["fit"] = "基础薄弱班、考点落实、复习课与短课时"
            c["unfit"] = "素养导向的开放目标、需要高阶思维的课题"

    top, second = cands[0], (cands[1] if len(cands) > 1 else None)
    reason = "推荐「%s」：%s（综合分 %.1f）" % (
        top["name"], top["orientation"], top["score"])
    if second and top["score"] - second["score"] < 5:
        reason += ("；与「%s」差距很小（%.1f 分），可按班级实际二选一"
                   % (second["name"], second["score"]))
    if layered:
        reason += "；已按分层要求加权"

    return {"topic": topic, "subject": subject, "grade": grade,
            "minutes": minutes, "layered": bool(layered),
            "candidates": cands, "recommended": top["id"], "reason": reason}


def pick(result, choice=None):
    """教师选定路线后取出该候选（choice 为空则取推荐项）。"""
    cands = (result or {}).get("candidates") or []
    if not cands:
        return None
    want = choice or (result or {}).get("recommended")
    for c in cands:
        if c["id"] == want:
            return c
    return cands[0]


def render(result):
    """渲染为 Markdown（供对话展示或导出）。"""
    if not result:
        return ""
    lines = ["**候选教学路线（按适配度排序）**\n"]
    for i, c in enumerate(result["candidates"], 1):
        lines.append("%d. **%s** — %s（%.1f 分）" % (
            i, c["name"], c["orientation"], c["score"]))
        lines.append("   - 环节：" + " → ".join(c["steps"]))
        lines.append("   - 评价：%s" % c["assessment"])
        lines.append("   - 适用：%s" % c["fit"])
        lines.append("   - 代价：%s" % c["costs"])
    lines.append("\n%s" % result["reason"])
    return "\n".join(lines)
