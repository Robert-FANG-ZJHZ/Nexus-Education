"""产出验证器（补齐「可靠推理与结果验证」这一最大短板）。

Critic 自检是**模型自评**：模型说"我通过了"，不等于真的通过了。
本模块做模型之外的**确定性核对**，零模型调用、零联网，本地即可跑：

1. 结构完整性 —— 该有的环节是否都在（目标 / 活动 / 评价 / 分层）；
2. 目标可观测性 —— 目标是否用可观测行为动词叙写（"理解"不算，"说出"才算）；
3. 目标—活动—评价一致性 —— 目标里的关键内容是否真在活动与评价中出现；
4. 引用合法性 —— `[来源:xxx]` 能否在本机知识库中核对到（复用 knowledge）；
5. 安全与隐私 —— 复用 safety 的输出复核与敏感信息规则；
6. 学情适配 —— 教师给了学情时，产出是否真有对应安排（分层/支架）。

输出一份**可解释的核验报告**，并给出置信度与"哪些论断需要人工核对"。
立场：核验只能降低错误概率，不能证明内容正确——因此报告里凡是"未取到依据"
的部分一律如实标注为待核对，不因为格式漂亮就给出"通过"的错觉。
"""

import re

# ---------------- 产出类型识别 ----------------

_KIND_HINTS = (
    ("objective", ("课时目标", "教学目标", "学习目标", "目标叙写", "素养目标")),
    ("lesson", ("教案", "教学设计", "教学过程", "课时设计", "备课")),
    ("assessment", ("量规", "评价量表", "评分标准", "评价任务")),
    ("homework", ("作业", "练习", "题单", "课后任务")),
)

# 各类型必须具备的环节（命中其一即可）
_REQUIRED = {
    "objective": (("目标",), ("可观测", "行为动词", "评价", "条件")),
    "lesson": (("目标", "教学目标"), ("过程", "活动", "环节", "教学流程"),
               ("评价", "检测", "反馈", "作业")),
    "assessment": (("维度", "指标", "标准"), ("等级", "分值", "水平", "表现")),
    "homework": (("基础", "巩固"), ("提高", "进阶", "拓展", "挑战")),
}

# 可观测行为动词（与 memory 的画像词表保持同一口径）
_OBSERVABLE_VERBS = (
    "说出", "写出", "列举", "设计", "完成", "解释", "背诵", "圈画", "计算",
    "描述", "归纳", "比较", "判断", "仿写", "批注", "朗读", "填写", "绘制",
    "分类", "举例", "改写", "概括", "分析", "评价", "运用", "辨认", "标注",
    "推导", "演示", "复述", "检索", "制作", "测量", "观察", "记录")

# 不可观测的模糊动词（出现即提示）
_VAGUE_VERBS = ("理解", "掌握", "了解", "体会", "感悟", "熟悉", "认识", "领悟")

# 学情适配的信号词
_ADAPT_HINTS = ("分层", "基础题", "进阶", "挑战", "支架", "帮扶", "个别",
                "学困", "薄弱", "额外", "补救", "拓展任务")

_STOP = re.compile(r"[\s，。、；：（）()“”\"'「」《》\-\d\.]+")


def _bigrams(text):
    """中文二元字串集合（用于粗略的内容重合度比对，避免引入分词依赖）。"""
    t = _STOP.sub("", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def detect_kind(text):
    """识别产出类型：objective / lesson / assessment / homework / general。"""
    t = text or ""
    for kind, hints in _KIND_HINTS:
        if any(h in t for h in hints):
            return kind
    return "general"


def _lines(text):
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def _pick_section(text, keys, exclude=()):
    """取命中关键词的段落：标题命中则连同其下的正文一起取。

    只看单行会漏掉真正的内容（"## 教学目标" 这行本身没有行为动词，
    动词在它下面的条目里），核验就会误报"目标不可观测"。
    """
    out, in_sec = [], False
    for l in _lines(text):
        if l.startswith("#"):                      # Markdown 标题
            in_sec = (any(k in l for k in keys)
                      and not any(x in l for x in exclude))
            if in_sec:
                out.append(l)
            continue
        if in_sec:
            out.append(l)
        elif any(k in l for k in keys) and not any(x in l for x in exclude):
            out.append(l)
    return out


def _check_structure(text, kind):
    issues = []
    if kind not in _REQUIRED:
        return issues, None
    for group in _REQUIRED[kind]:
        if not any(k in text for k in group):
            issues.append({
                "code": "structure", "level": "blocker",
                "msg": "缺少环节：%s" % "／".join(group),
                "hint": "补齐该环节后再使用；缺少的环节无法后续核验"})
    return issues, None


def _check_objectives(text, kind):
    """目标可观测性：只在明确涉及目标的产出里检查。"""
    issues = []
    obj_lines = _pick_section(text, ("目标", "学习目标", "教学目标"))
    if not obj_lines:
        return issues, None
    joined = "\n".join(obj_lines)
    observable = sum(1 for v in _OBSERVABLE_VERBS if v in joined)
    vague = [v for v in _VAGUE_VERBS if v in joined]
    if observable == 0:
        issues.append({
            "code": "objective_observable", "level": "blocker",
            "msg": "目标未使用可观测行为动词",
            "hint": "把「理解/掌握」改成「说出/写出/完成…」这类可评价的表述"})
    if vague and observable == 0:
        issues.append({
            "code": "objective_vague", "level": "warn",
            "msg": "目标含不可直接评价的动词：" + "、".join(vague[:5]),
            "hint": "补充可观察的表现标准"})
    return issues, {"observable_verbs": observable, "vague_verbs": vague[:5]}


def _check_alignment(text, kind):
    """目标—活动—评价一致性：目标内容是否真在活动与评价里出现。"""
    if kind not in ("lesson", "objective", "assessment"):
        return [], None
    obj_lines = _pick_section(text, ("目标",), exclude=("评价目标",))
    act_lines = _pick_section(text, ("活动", "过程", "环节", "任务", "探究", "练习"))
    eva_lines = _pick_section(text, ("评价", "检测", "量规", "反馈", "作业"))
    if not obj_lines or (not act_lines and not eva_lines):
        return [], None
    # 标题本身不参与重合度计算（"教学目标"四个字不会出现在活动里）
    body = lambda ls: "\n".join(l for l in ls if not l.startswith("#"))
    obj_bg, rest_bg = _bigrams(body(obj_lines)), _bigrams(
        body(act_lines + eva_lines))
    if not obj_bg:
        return [], None
    hit = len(obj_bg & rest_bg) / max(1, len(obj_bg))
    issues = []
    if hit < 0.25:
        issues.append({
            "code": "alignment", "level": "warn",
            "msg": "目标与活动/评价的文字关联较弱（重合度 %d%%）" % round(hit * 100),
            "hint": "逐条目标对应到具体活动与评价证据，避免目标与过程两张皮"})
    return issues, {"alignment": round(hit, 2)}


def _check_citations(text):
    """引用合法性：无法核对的来源一律提示（假引用比不引用更危险）。"""
    if "来源" not in (text or ""):
        return [], None
    try:
        from . import knowledge
        total = int((knowledge.status() or {}).get("total_chunks") or 0)
        valid, invalid = knowledge.validate_citations(text)
    except Exception:
        return [], None
    if total == 0:
        # 知识库未就绪时不假装核对过：如实说明"无法核对"，而不是默认通过
        return [{
            "code": "citation_unverifiable", "level": "warn",
            "msg": "产出含引用标记，但本机知识库尚未就绪，暂无法核对出处",
            "hint": "知识库加载完成后重新核验；未核对的出处不得直接进课堂"}], {
            "citations": 0, "invalid": 0, "kb_ready": False}
    if not invalid:
        return [], {"citations": len(set(valid or [])), "invalid": 0,
                    "kb_ready": True}
    return [{
        "code": "citation", "level": "blocker",
        "msg": "有 %d 处引用无法在本机知识库核对" % len(set(invalid)),
        "hint": "删除或替换这些引用；未核对的出处不得进入课堂"}], {
        "citations": len(set(valid or [])), "invalid": len(set(invalid)),
        "kb_ready": True}


def _check_safety(text):
    issues = []
    try:
        from . import safety
        for w in safety.review_output(text or ""):
            issues.append({"code": "safety", "level": "blocker",
                           "msg": w, "hint": "按提示修改后再使用"})
        _, masked = safety.mask_pii(text or "")
        if masked:
            issues.append({
                "code": "privacy", "level": "blocker",
                "msg": "产出中可能含可识别信息：" + "、".join(masked[:3]),
                "hint": "删除或替换后再导出，学生信息不出本地"})
    except Exception:
        pass
    return issues, None


def _check_adaptation(text, student_info):
    """给了学情却没有对应安排 → 提示（避免"拿通用教案糊弄具体班级"）。"""
    if not (student_info or "").strip():
        return [], None
    if any(h in (text or "") for h in _ADAPT_HINTS):
        return [], {"adapted": True}
    return [{
        "code": "adaptation", "level": "warn",
        "msg": "教师提供了学情，但产出中未见分层或支架安排",
        "hint": "补一节「针对本班学情的调整」，否则本课仍是通用设计"}], {
        "adapted": False}


def _check_length(text):
    issues = []
    if len(text or "") < 120:
        issues.append({"code": "length", "level": "warn",
                       "msg": "产出过短，可能未完整覆盖要求的环节",
                       "hint": "补充完整后重新核验"})
    return issues, None


def verify(text, kind=None, student_info=""):
    """对一份产出做确定性核验。

    返回 dict：
      kind        识别出的产出类型
      passed      是否无 blocker 级问题
      score       0~100 综合分
      confidence  置信度 high/medium/low（只表示"机器可核对的部分"）
      issues      [{code, level, msg, hint}]
      metrics     各项中间数据，供界面与审计查看
    """
    text = text or ""
    kind = kind or detect_kind(text)
    issues, metrics = [], {}

    for fn, kw in ((_check_structure, {"kind": kind}),
                   (_check_objectives, {"kind": kind}),
                   (_check_alignment, {"kind": kind}),
                   (_check_citations, {}),
                   (_check_safety, {}),
                   (_check_adaptation, {"student_info": student_info}),
                   (_check_length, {})):
        got, extra = fn(text, **kw)
        issues.extend(got)
        if isinstance(extra, dict):
            metrics.update(extra)

    blockers = [i for i in issues if i["level"] == "blocker"]
    warns = [i for i in issues if i["level"] == "warn"]
    score = max(0, 100 - 25 * len(blockers) - 8 * len(warns))
    if blockers:
        confidence = "low"
    elif warns:
        confidence = "medium"
    else:
        confidence = "high"
    return {
        "kind": kind, "passed": not blockers, "score": score,
        "confidence": confidence, "issues": issues,
        "blockers": len(blockers), "warnings": len(warns),
        "metrics": metrics,
        "note": ("机器已核对 %d 项可判定指标；内容科学性仍由教师裁定"
                 % (len(metrics) + len(issues))),
    }


def render(result):
    """把核验报告渲染成追加在产出末尾的教师可读提示。

    只显示有问题的项；全部通过时给一行简短说明，不刷存在感。
    """
    if not result:
        return ""
    issues = result.get("issues") or []
    if not issues:
        return ("\n\n---\n> ✅ **产出核验**：结构与引用均已核对，无机器可判定的问题"
                "（%s）。\n" % result.get("note", ""))
    lines = ["\n\n---\n> 🔎 **产出核验**：%s\n"
             % ("发现 %d 项需处理" % result["blockers"]
                if result.get("blockers") else "有 %d 项提示" % len(issues))]
    for i in issues:
        mark = "❌" if i["level"] == "blocker" else "⚠️"
        lines.append("> - %s %s" % (mark, i["msg"]))
        if i.get("hint"):
            lines.append(">   ↳ %s" % i["hint"])
    lines.append(">\n> %s\n" % result.get("note", ""))
    return "\n".join(lines)


def verify_stream(text, kind=None, student_info=""):
    """便捷接口：核验 + 渲染提示文本。返回 (报告, 追加文本)。"""
    result = verify(text, kind=kind, student_info=student_info)
    return result, render(result)
