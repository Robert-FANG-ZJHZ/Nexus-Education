"""任务规划（Agent Loop 的「规划」环节，设计文档 §7）。

把教师的自然语言需求解析为**可解释的执行计划**：走哪个专家、要不要先查资料、
要不要保存产出、要不要自检。计划会在界面上展示给教师，便于理解与干预——
教育场景里"黑箱自主"并不可取，可控的自主才有价值。

规划以规则为主（确定性、可解释、零额外模型调用），
模型只负责执行环节的内容生成。
"""

import re

from . import agents

# 意图线索
_LOOKUP_KW = ["之前", "上次", "历史", "以前", "已保存", "我之前", "参考我",
              "查一下", "有没有做过", "回顾"]
_SAVE_KW = ["保存", "存档", "存到", "入库", "导出", "生成 word", "生成word",
            "下载 word", "存下来"]
_REVIEW_KW = ["检查", "修改", "优化", "改进", "诊断", "评价这份", "看看这份",
              "哪里不对", "是否合理", "复核"]
_KB_KW = ["课标", "教材", "课文", "参考", "依据", "资料", "原文", "案例"]
# 明确要课件时，才给 PPT 工具（避免每次备课都多带一个用不上的工具）
_PPT_KW = ["课件", "PPT", "ppt", "幻灯片", "演示文稿", "投屏", "上课用的"]

# 各策略默认允许使用的工具
_TOOLS_BY_STRATEGY = {
    "lookup": ["list_lessons", "get_lesson", "search_knowledge"],
    "pipeline": ["search_knowledge", "save_lesson"],
    "single": ["search_knowledge"],
}

_CRITIQUE_OUTPUTS = ("目标", "教案", "教学设计", "作业", "量规", "评价", "题单")


def _hit(text, keywords):
    return [k for k in keywords if k in text]


def _needs_critique(text):
    """产出结构化成果时才值得自检（省去无谓的模型调用）。"""
    return any(k in text for k in _CRITIQUE_OUTPUTS)


def plan(message, expert="auto", student_info=""):
    """生成执行计划。

    返回 dict：
      strategy  single / lookup / pipeline
      expert    主责专家 id
      steps     [{title, expert, goal, tools, critique}]
      summary   一句话说明（界面展示）
    """
    text = (message or "").strip()
    route = expert if expert not in (None, "", "auto") else agents.route(text)

    if route == "lesson":
        strategy = "pipeline"
    elif _hit(text, _LOOKUP_KW):
        strategy = "lookup"
    else:
        strategy = "single"

    tools = list(_TOOLS_BY_STRATEGY.get(strategy, ["search_knowledge"]))
    if _hit(text, _SAVE_KW) and "save_lesson" not in tools:
        tools.append("save_lesson")
    if _hit(text, _REVIEW_KW) and "get_lesson" not in tools:
        tools.insert(0, "get_lesson")
    if _hit(text, _PPT_KW) and "export_pptx" not in tools:
        tools.append("export_pptx")

    critique = _needs_critique(text) or bool(_hit(text, _REVIEW_KW))

    # 元认知（Phase 3.2）：历史数据显示另一个（成本相当的）策略返工率明显更低时，
    # 主动换策略，并把换的原因写进计划——换策略必须让教师看见，不能悄悄改。
    metacog = None
    try:
        from . import metacog as _mc
        sug = _mc.suggest(strategy, route)
        if sug:
            metacog = sug
            strategy = sug["to"]
            tools = list(_TOOLS_BY_STRATEGY.get(strategy, tools))
    except Exception:
        pass

    if strategy == "pipeline":
        steps = _pipeline_steps(text, tools, critique)
        summary = "拆解为「目标 → 教学 → 评价 → 回流」四步闭环"
    elif strategy == "lookup":
        main = "coordinator" if route == "lesson" else route
        steps = [
            {"title": "检索历史产出与知识库", "expert": main,
             "goal": "先查教案沙盒、记忆与知识库，避免凭空生成",
             "tools": [t for t in tools if t in
                       ("list_lessons", "get_lesson", "search_knowledge")],
             "critique": False},
            {"title": "基于检索结果产出", "expert": main,
             "goal": text[:120], "tools": tools, "critique": critique},
        ]
        summary = "先查历史教案与知识库，再作答"
    else:
        steps = [{
            "title": "调用专家作答", "expert": route, "goal": text[:120],
            "tools": tools, "critique": critique,
        }]
        summary = "由%s直接作答" % agents.get_expert(route)["name"]

    if student_info:
        for s in steps:
            s["goal"] += "（学情：%s）" % student_info[:80]

    if metacog:
        summary += f"（元认知：{metacog['reason']}）"
    return {"strategy": strategy, "expert": route, "steps": steps,
            "summary": summary, "critique": critique, "metacog": metacog}


# ---------------- 任务分解（把"选模板"升级为"分解-调度"）----------------

# 复合请求信号：出现这些说法时，单一环节/一次备课撑不住，需要拆成多个子任务
_COMPOUND_KW = ["单元", "整册", "全册", "多节课", "课时1", "第1课时", "系列课",
                "整单元", "一个学期", "这几课", "以下几课"]
_STEP_KW = ["先", "然后", "接着", "再", "最后", "分几步", "第一步"]


def is_compound(text):
    t = text or ""
    return (any(k in t for k in _COMPOUND_KW)
            or (sum(1 for k in _STEP_KW if k in t) >= 2))


def decompose(message, expert="auto", max_subtasks=6):
    """把复合请求拆成子任务列表（每个自带状态机字段）。

    返回 [{"title","goal","expert","critique","status","depends"}]。
    status 走 pending → running → done/failed（由 tasks.py 驱动），
    失败可重试、成功后解锁后继——这才是"长程推进"，而不是定时重跑一遍。

    分解策略刻意保守：识别不出结构时返回空列表，交给原有单步流程，
    **不为了显得会规划而硬拆**。

    critique 一律为 False：子任务由后续汇总环节统一复核。
    若每个子任务都自检，N 个子任务就多花 2N 次模型调用，长程任务会慢到不可用。
    """
    text = (message or "").strip()
    if not is_compound(text):
        return []

    subs, subject_hint = [], ""
    m = re.search(r"([\u4e00-\u9fff]{2,8})单元", text)
    if m:
        subject_hint = m.group(1)

    # 单元备课：按课时拆
    m = re.search(r"(\d+)\s*(?:个)?课时", text)
    n_lessons = int(m.group(1)) if m else 0
    if n_lessons and 2 <= n_lessons <= max_subtasks:
        for i in range(1, n_lessons + 1):
            subs.append({
                "title": f"第 {i} 课时教学设计",
                "goal": f"{text}（第 {i} 课时）",
                "expert": "nexdes", "critique": False, "status": "pending",
                "depends": [f"第 {i - 1} 课时教学设计"] if i > 1 else [],
            })
        subs.insert(0, {"title": "单元整体目标", "goal": f"{text} 的单元整体目标",
                        "expert": "nexaim", "critique": False, "status": "pending",
                        "depends": []})
        return subs

    # 多步指令：按"先/然后/接着/再/最后"切
    parts = re.split(r"(?:先|然后|接着|再|最后)[，、：:]?\s*", text)
    parts = [p.strip(" ，,、。") for p in parts if len(p.strip()) >= 3]
    if len(parts) >= 2:
        for i, p in enumerate(parts[:max_subtasks]):
            subs.append({
                "title": f"步骤 {i + 1}",
                "goal": p, "expert": agents.route(p), "critique": False,
                "status": "pending",
                "depends": [f"步骤 {i}"] if i > 0 else [],
            })
        return subs

    # 只识别出单元信号但没说几课时：给一个可调整的两段式
    if subject_hint:
        return [
            {"title": "单元整体目标", "goal": f"{subject_hint}单元的整体目标",
             "expert": "nexaim", "critique": False, "status": "pending",
             "depends": []},
            {"title": "单元评价设计", "goal": f"{subject_hint}单元的作业与评价",
             "expert": "nexsca", "critique": False, "status": "pending",
             "depends": ["单元整体目标"]},
        ]
    return []


def repair(plan=None, failure="", failed_tool="", critique_exhausted=False,
           tools=None):
    """动态重规划 v1：执行受阻时**改变策略**，而不是原样重试。

    原 planner 只在任务开始前生成一次计划，执行中遇到阻碍只能重试，这是
    「规划层」最大的短板。本函数按失败类型给出降级方案：
      - 工具失败     → 摘掉该工具，改用不依赖它的路径；
      - 证据/依据不足 → 要求先取证再作答；
      - 自检多轮不过 → 缩小产出范围（只给核心成果）。

    plan 可省略（run_agent 这种单步场景只传 tools 即可）。

    返回 dict：{"tools": 新工具列表, "note": 追加给模型的目标补充语,
                "action": 面向教师的说明, "changed": 是否发生了改变}
    """
    cur = list(tools or [])
    if plan:
        for s in (plan.get("steps") or []):
            cur = list(s.get("tools") or cur)
            break
    note, action, changed = "", "", False

    if failed_tool:
        if failed_tool in cur:
            cur = [t for t in cur if t != failed_tool]
            changed = True
        action = "工具「%s」执行失败，已改为不依赖它的方案" % failed_tool
        if failed_tool == "search_knowledge":
            note += ("（知识库检索不可用，请基于通用教学规范作答，"
                     "并在末尾说明未取到校本资料。）")
        else:
            note += "（该工具不可用，请直接给出成果，不要再调用它。）"

    if ("证据不足" in (failure or "")) or ("依据不足" in (failure or "")):
        if "search_knowledge" not in cur:
            cur.insert(0, "search_knowledge")
            changed = True
        note += "（请先检索知识库取证，再据证作答，不要凭空断言。）"
        action = (action + "；" if action else "") + "证据不足，已要求先取证再作答"

    if critique_exhausted:
        note += ("（自检多轮仍未通过，请只输出最核心的成果，"
                 "省略扩展说明与修饰性表述。）")
        action = (action + "；" if action else "") + "自检多轮未通过，已缩小产出范围"
        changed = True

    return {"tools": cur, "note": note, "action": action, "changed": changed}


def _pipeline_steps(text, tools, critique):
    subject, grade, topic = _parse_lesson_args(text)
    base = [
        ("① 目标定向", "nexaim",
         "把课标与教材要求转化为可观测、可评价的课时目标"),
        ("② 教学设计", "nexdes",
         "按「目标—评价—活动」逆向设计，评价任务前置"),
        ("③ 作业与评价", "nexsca",
         "目标对齐的分层作业、量规与负担监测"),
        ("④ 闭环回流", "coordinator",
         "汇总缺口提示、教师确认要点与二次备课建议"),
    ]
    steps = []
    for title, expert, goal in base:
        detail = goal
        if topic:
            detail += f"（{subject}{grade}《{topic}》）"
        # 自检只在最后一个成果环节做一次。
        # 真实教训：四步里三步都自检，等于每次备课多花 3×（自检 + 全文重写）
        # ≈ 6 次额外模型调用，实测一次完整备课要十几分钟才出结果。
        # 前两步的产出会被后续环节承接校验，末尾统一自检即可。
        steps.append({"title": title, "expert": expert, "goal": detail,
                      "tools": tools if expert != "coordinator" else [],
                      "critique": expert == "nexsca"})
    return steps


def _parse_lesson_args(text):
    """从自然语言里尽力解析学科/学段/课题，供计划展示用。"""
    subject = ""
    grade = ""
    topic = ""
    m = re.search(r"(语文|数学|英语|科学|历史|地理|道德与法治|物理|化学|生物)",
                  text)
    if m:
        subject = m.group(1)
    m = re.search(r"(小学|初中|高中)?(一年级|二年级|三年级|四年级|五年级|六年级|"
                  r"七年级|八年级|九年级|高一|高二|高三)", text)
    if m:
        grade = (m.group(1) or "") + m.group(2)
    m = re.search(r"《(.+?)》", text)
    if m:
        topic = m.group(1).strip()
    return subject, grade, topic
