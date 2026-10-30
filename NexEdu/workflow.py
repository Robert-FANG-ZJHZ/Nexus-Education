"""工作流引擎（Workflow Engine）。

把「一次性备课闭环」升级为**可编排、可中断、可复用**的备课流水线
（对应设计文档《NexEdu 工作流方案》）。

核心概念
--------
- **工作流（Workflow）**：一组有序节点，描述「为完成一个教学目标要经历哪些环节」。
- **节点（Node）**：最小执行单元，绑定一个专家 + 一类产出（目标/设计/评价/回流…）。
- **工件（Artifact）**：节点产出的具体内容（教案、试卷、量规…），带版本落盘。
- **上下文黑板（Blackboard）**：贯穿全流程的共享信息；遵循设计文档原则——
  **传递结论，不传递全文**（每个节点只接收上游的结构化摘要）。
- **检查点（Checkpoint）**：需要教师确认/选择的节点；交互模式下暂停等待，
  **全自动模式（auto=True）下自动放行**，全程无人值守跑完整条流水线。
- **状态机 + 持久化**：draft → running → waiting → running → done / failed；
  每个节点独立记录状态，失败只影响该节点；重启/断网后从持久化状态恢复续跑。

与已有能力的关系
----------------
- 节点的实际生成复用 ``agents.run_agent``（流式生成 + 工具 + 六维自检）；
- 一致性校验复用 ``agents.alignment_score``（确定性、零额外模型调用）；
- 工件导出复用 ``lessons``（Markdown + Word/PPT 双格式落盘）；
- 引用可追溯、安全复核、用量统计由被复用的底层自动承载。

本模块零第三方依赖，可直接 import 做单元测试（用 mock 替换 LLMClient 即可）。
"""

import json
import os
import re
import time
import traceback
import uuid

from . import agents, config, knowledge, lessons, memory

WORKFLOW_DIR = os.path.join(config.WORKSPACE_DIR, "workflows")


def _wf_dir():
    os.makedirs(WORKFLOW_DIR, exist_ok=True)
    return WORKFLOW_DIR


# ---------------------------------------------------------------------------
# 节点目标模板填充（只替换已知占位符，未知花括号原样保留，避免与 Markdown 冲突）
# ---------------------------------------------------------------------------
_PLACE = re.compile(r"\{(\w+)\}")


def _fill(tpl, **kw):
    def _repl(m):
        return str(kw.get(m.group(1), m.group(0)))
    return _PLACE.sub(_repl, tpl)


# 追加到每个「产出型」节点末尾，要求模型给出结构化摘要，供下游衔接使用。
_SUMMARY_INSTR = (
    "\n\n最后，请在回答末尾以「## 摘要」起头，给出不超过 5 条核心结论要点"
    "（每条以「· 」开头，单条不超过 60 字），用于后续环节衔接——只写结论，不写过程。"
)


def _summarize(text, max_points=5):
    """从节点产出抽取 ≤5 条要点，作为下游上下文（传递结论，不传递全文）。

    优先取显式「## 摘要」段落；缺失时回退到标题与加粗/列表首句。
    """
    if not text:
        return []
    out = []
    m = re.search(r"##\s*摘要\b([\s\S]*?)(?=\n##\s|\Z)", text)
    if m:
        for line in m.group(1).splitlines():
            s = line.strip().lstrip("-*•·").strip()
            if s and not s.startswith("#"):
                out.append(s[:80])
    else:
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#"):
                out.append(s.lstrip("#").strip()[:80])
            elif (s.startswith("**") or s.startswith("-") or s.startswith("*")) \
                    and len(s) > 4:
                out.append(s.lstrip("*- ").strip("*")[:80])
    # 去重并截断
    seen, final = set(), []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            final.append(s)
        if len(final) >= max_points:
            break
    return final


# ---------------------------------------------------------------------------
# 工作流定义
# ---------------------------------------------------------------------------
# 节点字段说明：
#   id          节点唯一标识（也是黑板里的 ctx_key）
#   title       展示标题
#   expert      专家 id（nexaim/nexdes/nexsca/coordinator）；检查点节点留空
#   goal        目标模板，支持 {subject}{grade}{topic}{student_info}{ctx} 占位
#   tools       允许的工具（空列表=纯生成）
#   critique    是否触发六维自检
#   checkpoint  True 表示检查点：交互模式暂停，全自动模式自动放行
#   produce     仅用于装配节点：装配后导出的工件类型

def _w1_goal_aim(s):
    return ("请为以下课时完成目标设计。\n"
            "学科：{subject}；学段：{grade}；课题/单元：{topic}\n"
            "学情补充：{student_info}\n"
            "请按你的工作流依次输出：\n"
            "1.《课时目标叙写》——可观测目标（主体＋行为动词＋行为条件＋表现标准），标注素养维度；\n"
            "2. 目标质量自检（一致性/层次性/可观测性/可评价性/素养适切性/学情适配性）；\n"
            "3.《目标-评价对应表》——目标与评价证据的对应关系与缺口提示。") + s


def _w1_goal_des(s):
    return ("基于上游目标体系，做评价前置的教学设计。\n"
            "学科：{subject}；学段：{grade}；课题：{topic}\n\n"
            "【上游目标体系摘要】\n{ctx}\n\n"
            "请按「目标—评价—活动」逆向输出：\n"
            "1.《课时教学设计》——课题、课时、目标、评价任务、学习活动（情境/问题链/小组任务）、"
            "分层支架（基础/进阶/挑战）、资源、作业、反思；\n"
            "2.《评价任务单》——任务名称、对应目标、评价标准、反馈路径；\n"
            "3.《课堂观察记录表》要点。") + s


def _w1_goal_sca(s):
    return ("基于以下目标体系与评价任务，完成作业与评价设计。\n"
            "学科：{subject}；学段：{grade}；课题：{topic}\n\n"
            "【上游摘要】\n{ctx}\n\n"
            "请输出：\n"
            "1.《目标-题目对应表》；\n"
            "2.《分层作业单》（基础/应用/创新三层，每题标注对应目标）；\n"
            "3.《评价量规》；\n"
            "4. 错因预判与补偿练习建议、作业负担监测（时长与题量预警）。") + s


def _w1_goal_backflow(s):
    return ("以下是「{grade}{subject}《{topic}》」的三专家协作成果摘要，"
            "请以编导身份输出不超过 300 字的《闭环回流卡》：\n"
            "1. 目标-教学-作业-评价链条的缺口提示（最多 3 条）；\n"
            "2. 教师确认要点（使用前需要人工确认/修改的关键处）；\n"
            "3. 二次备课与追踪建议（1-2 条）。\n"
            "要求：只做汇总不重写内容。\n\n"
            "【上游摘要】\n{ctx}") + s


def _assemble_w1(run):
    bb = run.blackboard["outputs"]
    clean = lambda t: knowledge.strip_bad_citations(t or "")
    c = run.ctx
    title = ((c["grade"] + c["subject"] + "《" + c["topic"] + "》教学设计")
             if c["grade"] else (c["subject"] + "《" + c["topic"] + "》教学设计"))
    body = "\n\n".join([
        "## 一、课时目标（NexAim）\n\n" + clean(bb.get("aim", "")),
        "## 二、教学设计（NexDes）\n\n" + clean(bb.get("des", "")),
        "## 三、作业与评价（NexSca）\n\n" + clean(bb.get("sca", "")),
        "## 四、闭环回流\n\n" + clean(bb.get("backflow", "")),
    ])
    return title, body


def _w4_goal_blueprint(s):
    return ("请为以下范围设计一套练习/试卷的命题蓝图。\n"
            "学科：{subject}；学段：{grade}；范围/单元：{topic}\n"
            "学情补充：{student_info}\n\n"
            "请输出：\n"
            "1. 知识点范围梳理（含课标/教材对应要求）；\n"
            "2.《双向细目表》——按「知识点 × 认知层级（识记/理解/应用/综合）× 题型」"
            "给出题量与分值分布建议。") + s


def _w4_goal_items(s):
    return ("基于以下命题蓝图，完成逐题设计。\n"
            "学科：{subject}；学段：{grade}；范围：{topic}\n\n"
            "【命题蓝图摘要】\n{ctx}\n\n"
            "请按双向细目表逐题设计，输出：\n"
            "1.《分层题单》——基础/应用/创新三层，每题含题干、参考答案、对应知识点与认知层级；\n"
            "2. 难度与负担检查（时长、题量、梯度是否合理）；\n"
            "3. 需要教师增删的题目清单建议。") + s


def _w4_goal_assemble(s):
    return ("以下是命题成果摘要，请以编导身份整理为**可直接使用的试卷与参考答案**。\n"
            "学科：{subject}；学段：{grade}；范围：{topic}\n\n"
            "【上游摘要】\n{ctx}\n\n"
            "请输出：\n"
            "一、《试卷》——按题型分块，题干清晰、预留作答空间；\n"
            "二、《参考答案与评分标准》——逐题给出答案与评分要点。") + s


def _assemble_w4(run):
    bb = run.blackboard["outputs"]
    clean = lambda t: knowledge.strip_bad_citations(t or "")
    c = run.ctx
    title = ((c["grade"] + c["subject"] + "《" + c["topic"] + "》练习卷")
             if c["grade"] else (c["subject"] + "《" + c["topic"] + "》练习卷"))
    body = "\n\n".join([
        "## 一、命题蓝图（NexAim）\n\n" + clean(bb.get("blueprint", "")),
        "## 二、逐题设计（NexSca）\n\n" + clean(bb.get("items", "")),
        "## 三、试卷与参考答案（编导装配）\n\n" + clean(bb.get("assemble", "")),
    ])
    return title, body


def _w2_goal_unit(s):
    return ("请为以下单元做整体目标设计。\n"
            "学科：{subject}；学段：{grade}；单元：{topic}\n"
            "学情补充：{student_info}\n\n"
            "请输出：\n"
            "1. 单元核心素养目标（可分解、可观测）；\n"
            "2. 单元目标链（单元→各课时）；\n"
            "3. 单元评价总体设计方向。") + s


def _w2_goal_lessons(s):
    return ("基于以下单元整体目标，做课时拆分设计。\n"
            "学科：{subject}；学段：{grade}；单元：{topic}\n\n"
            "【单元目标摘要】\n{ctx}\n\n"
            "请输出各课时（建议 3-5 课时）的概要：每课时含目标、重难点、核心活动与评价任务，"
            "并说明课时之间的衔接逻辑。") + s


def _w2_goal_eval(s):
    return ("基于以下单元目标与课时设计，做单元检测设计。\n"
            "学科：{subject}；学段：{grade}；单元：{topic}\n\n"
            "【上游摘要】\n{ctx}\n\n"
            "请输出：\n"
            "1. 单元检测双向细目（覆盖各课时核心目标）；\n"
            "2. 分层检测题（基础/应用/综合）；\n"
            "3. 单元评价量规与复盘建议。") + s


def _w2_goal_assemble(s):
    return ("以下是单元备课成果摘要，请以编导身份汇总为单元备课方案。\n"
            "学科：{subject}；学段：{grade}；单元：{topic}\n\n"
            "【上游摘要】\n{ctx}\n\n"
            "请输出《单元备课方案》——单元目标、课时安排表、单元检测要点、"
            "教师确认与二次备课建议（不超过 400 字）。") + s


def _assemble_w2(run):
    bb = run.blackboard["outputs"]
    clean = lambda t: knowledge.strip_bad_citations(t or "")
    c = run.ctx
    title = ((c["grade"] + c["subject"] + "《" + c["topic"] + "》单元备课方案")
             if c["grade"] else (c["subject"] + "《" + c["topic"] + "》单元备课方案"))
    body = "\n\n".join([
        "## 一、单元整体目标（NexAim）\n\n" + clean(bb.get("unit_goal", "")),
        "## 二、课时拆分设计（NexDes）\n\n" + clean(bb.get("lessons", "")),
        "## 三、单元检测设计（NexSca）\n\n" + clean(bb.get("unit_eval", "")),
        "## 四、单元备课方案（编导装配）\n\n" + clean(bb.get("assemble", "")),
    ])
    return title, body


WORKFLOWS = {
    "w1-lesson": {
        "id": "w1-lesson",
        "title": "单课时备课",
        "category": "备课",
        "desc": "目标定向（NexAim）→ 教学设计（NexDes）→ 作业与评价（NexSca）→ "
                "确认 → 闭环回流 → 自动导出教案 Word + PPT。最常用，复用现有四环专家。",
        "nodes": [
            # 自检只在进入「教学方案确认」检查点前的最后一个成果节点做一次：
            # 三个节点都自检会多花 6 次模型调用，而中间产出本来就要被后续环节承接校验。
            {"id": "aim", "title": "① 目标定向 · NexAim", "expert": "nexaim",
             "goal": _w1_goal_aim(_SUMMARY_INSTR), "tools": [], "backfill": True,
             "critique": False},
            {"id": "des", "title": "② 教学设计 · NexDes", "expert": "nexdes",
             "goal": _w1_goal_des(_SUMMARY_INSTR), "tools": [], "backfill": True,
             "critique": False},
            {"id": "sca", "title": "③ 作业与评价 · NexSca", "expert": "nexsca",
             "goal": _w1_goal_sca(_SUMMARY_INSTR), "tools": [], "backfill": True,
             "critique": True},
            {"id": "confirm", "title": "教学方案确认", "checkpoint": True,
             "title_full": "④ 教学方案确认（检查点）"},
            {"id": "backflow", "title": "④ 闭环回流 · 编导", "expert": "coordinator",
             "goal": _w1_goal_backflow(_SUMMARY_INSTR), "tools": [], "critique": False},
        ],
        "assemble": _assemble_w1,
        "produce": ["docx", "pptx"],
    },
    "w2-unit": {
        "id": "w2-unit",
        "title": "单元备课",
        "category": "备课",
        "desc": "单元整体目标 → 课时拆分（串行衔接）→ 单元检测设计 → 确认 → 汇总导出。",
        "nodes": [
            {"id": "unit_goal", "title": "① 单元整体目标 · NexAim", "expert": "nexaim",
             "goal": _w2_goal_unit(_SUMMARY_INSTR), "tools": [], "backfill": True,
             "critique": False},
            {"id": "lessons", "title": "② 课时拆分设计 · NexDes", "expert": "nexdes",
             "goal": _w2_goal_lessons(_SUMMARY_INSTR), "tools": [], "backfill": True,
             "critique": False},
            {"id": "unit_eval", "title": "③ 单元检测设计 · NexSca", "expert": "nexsca",
             "goal": _w2_goal_eval(_SUMMARY_INSTR), "tools": [], "backfill": True,
             "critique": True},
            {"id": "confirm", "title": "单元方案确认", "checkpoint": True,
             "title_full": "④ 单元方案确认（检查点）"},
            {"id": "assemble", "title": "④ 单元备课方案 · 编导", "expert": "coordinator",
             "goal": _w2_goal_assemble(_SUMMARY_INSTR), "tools": [], "critique": False},
        ],
        "assemble": _assemble_w2,
        "produce": ["docx"],
    },
    "w4-paper": {
        "id": "w4-paper",
        "title": "试卷/练习设计",
        "category": "命题",
        "desc": "知识点范围与双向细目表 → 逐题设计（分层/难度/负担）→ 确认 → "
                "装配试卷与参考答案并导出。",
        "nodes": [
            {"id": "blueprint", "title": "① 命题蓝图 · NexAim", "expert": "nexaim",
             "goal": _w4_goal_blueprint(_SUMMARY_INSTR), "tools": [], "backfill": True,
             "critique": False},
            {"id": "items", "title": "② 逐题设计 · NexSca", "expert": "nexsca",
             "goal": _w4_goal_items(_SUMMARY_INSTR), "tools": [], "backfill": True,
             "critique": True},
            {"id": "confirm", "title": "试卷方案确认", "checkpoint": True,
             "title_full": "③ 试卷方案确认（检查点）"},
            {"id": "assemble", "title": "③ 装配试卷 · 编导", "expert": "coordinator",
             "goal": _w4_goal_assemble(_SUMMARY_INSTR), "tools": [], "critique": False},
        ],
        "assemble": _assemble_w4,
        "produce": ["docx"],
    },
}


# ---------------------------------------------------------------------------
# 运行态（WorkflowRun）
# ---------------------------------------------------------------------------
class WorkflowRun:
    """一次工作流运行的持久化状态。"""

    def __init__(self, run_id, wf_id, ctx, auto):
        self.run_id = run_id
        self.wf_id = wf_id
        self.wf = WORKFLOWS[wf_id]
        self.ctx = ctx
        self.auto = auto
        self.status = "draft"          # draft/running/waiting/done/failed
        self.current = 0               # 下一个要执行的节点下标
        self.node_states = [
            {"id": n["id"], "title": n.get("title_full") or n["title"],
             "expert": n.get("expert") or "", "status": "pending",
             "summary": [], "ms": 0, "thoughts": 0}
            for n in self.wf["nodes"]
        ]
        self.blackboard = {"outputs": {}, "summaries": {}}
        self.artifacts = []
        self.created_at = time.time()
        self.finished_at = None

    # ---- 序列化 ----
    def to_dict(self):
        return {
            "run_id": self.run_id,
            "wf_id": self.wf_id,
            "ctx": self.ctx,
            "auto": self.auto,
            "status": self.status,
            "current": self.current,
            "node_states": self.node_states,
            "blackboard": self.blackboard,
            "artifacts": self.artifacts,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }

    def save(self):
        path = os.path.join(_wf_dir(), self.run_id + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    @classmethod
    def load(cls, run_id):
        path = os.path.join(_wf_dir(), run_id + ".json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if d.get("wf_id") not in WORKFLOWS:
            return None
        obj = cls.__new__(cls)
        obj.run_id = d["run_id"]
        obj.wf_id = d["wf_id"]
        obj.wf = WORKFLOWS[d["wf_id"]]
        obj.ctx = d["ctx"]
        obj.auto = d["auto"]
        obj.status = d["status"]
        obj.current = d["current"]
        obj.node_states = d["node_states"]
        obj.blackboard = d.get("blackboard", {"outputs": {}, "summaries": {}})
        obj.artifacts = d.get("artifacts", [])
        obj.created_at = d.get("created_at", time.time())
        obj.finished_at = d.get("finished_at")
        return obj

    # ---- 上下文黑板：只向下游传递摘要 ----
    def context_for(self, node):
        parts = []
        for ns in self.node_states:
            if ns["status"] != "ok":
                continue
            if ns["id"] == node["id"]:
                break
            summ = ns.get("summary") or []
            if summ:
                parts.append("【上游：%s】\n" % ns["title"]
                             + "\n".join("· " + s for s in summ))
        c = self.ctx
        parts.append("【原始需求】学科：%s；学段：%s；课题/单元：%s；学情：%s"
                     % (c.get("subject", ""), c.get("grade", ""),
                        c.get("topic", ""), c.get("student_info") or "无"))
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def list_workflows():
    """返回所有工作流的可序列化元信息（不含函数对象）。"""
    out = []
    for wf in WORKFLOWS.values():
        out.append({
            "id": wf["id"],
            "title": wf["title"],
            "category": wf.get("category", ""),
            "desc": wf.get("desc", ""),
            "nodes": [{"id": n["id"], "title": n.get("title_full") or n["title"],
                       "expert": n.get("expert") or ""}
                      for n in wf["nodes"]],
            "has_checkpoint": any(n.get("checkpoint") for n in wf["nodes"]),
            "produce": wf.get("produce", []),
        })
    return out


def create_run(wf_id, subject, grade, topic, student_info="", extra="",
               skills=None, auto=False):
    if wf_id not in WORKFLOWS:
        raise ValueError("未知工作流：%s" % wf_id)
    if not (topic or "").strip():
        raise ValueError("请填写课题/单元")
    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    ctx = {
        "subject": (subject or "语文").strip(),
        "grade": (grade or "").strip(),
        "topic": topic.strip(),
        "student_info": (student_info or "").strip(),
        "extra": extra or "",
        "skills": skills or [],
    }
    run = WorkflowRun(run_id, wf_id, ctx, auto)
    run.status = "running"
    run.save()
    return run


def list_runs(limit=30):
    """已保存的运行列表（按时间倒序）。"""
    d = _wf_dir()
    out = []
    try:
        files = [f for f in os.listdir(d) if f.endswith(".json")]
    except OSError:
        return out
    for fn in files:
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                d0 = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if d0.get("wf_id") not in WORKFLOWS:
            continue
        out.append({
            "run_id": d0["run_id"],
            "wf_id": d0["wf_id"],
            "wf_title": WORKFLOWS[d0["wf_id"]]["title"],
            "status": d0["status"],
            "auto": d0["auto"],
            "topic": d0.get("ctx", {}).get("topic", ""),
            "created_at": d0.get("created_at", 0),
            "current": d0.get("current", 0),
            "total": len(d0.get("node_states", [])),
            "ms": sum((ns.get("ms") or 0) for ns in d0.get("node_states", [])),
        })
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return out[:limit]


def get_run(run_id):
    run = WorkflowRun.load(run_id)
    if run is None:
        return None
    return run.to_dict()


# ---------------------------------------------------------------------------
# 节点执行
# ---------------------------------------------------------------------------
# 「思考过程」事件类型：这些是过程信息（工具调用/观察/自检/重规划），
# 与面向教师的正文 delta 区分开——前端用不同颜色渲染，并可按节点折叠。
THINK_TYPES = ("tool", "observe", "critique_start", "critique_delta",
               "critique", "repair", "plan")

# 思考事件的中文标记，供前端直接展示
THINK_LABEL = {
    "tool": "调用工具",
    "observe": "工具结果",
    "critique_start": "开始自检",
    "critique_delta": "自检意见",
    "critique": "自检结论",
    "repair": "动态重规划",
    "plan": "执行计划",
    "delegate": "委派子智能体",
}


def _execute_node(run, node, idx):
    """执行单个节点，yield 事件。复用 agents.run_agent 的流式生成 + 自检 + 工具。

    事件分为两类：
      - ``delta``          面向教师的**正文输出**（最终成果）
      - ``think``/工具事件  **思考过程**（计划/取证/自检/重规划），带耗时与节点下标
    """
    t0 = time.time()

    def _ms():
        return int((time.time() - t0) * 1000)

    run.node_states[idx]["status"] = "running"
    run.node_states[idx]["ms"] = 0
    run.node_states[idx]["thoughts"] = 0
    run.save()
    yield {"type": "node_start", "index": idx, "id": node["id"],
           "title": node.get("title_full") or node["title"],
           "expert": node.get("expert") or "",
           "started_at": t0}

    # ① 计划（思考过程首个条目）：本节点的角色、可用工具、是否自检
    tools = node.get("tools") or []
    expert = agents.get_expert(node["expert"])
    plan_text = "角色：%s（%s）" % (expert.get("name") or node["expert"],
                                   expert.get("cn") or "")
    plan_text += "｜工具：%s" % ("、".join(tools) if tools else "无")
    plan_text += "｜自检：%s" % ("开启" if node.get("critique") else "关闭")
    run.node_states[idx]["thoughts"] += 1
    yield {"type": "think", "index": idx, "node": node["id"],
           "kind": "plan", "label": THINK_LABEL["plan"],
           "text": plan_text, "ms": _ms()}

    ctx = run.context_for(node)
    prompt = _fill(node["goal"], ctx=ctx, **run.ctx)
    extra = run.ctx.get("extra", "")
    acc = []
    try:
        kb_query = " ".join(x for x in (run.ctx.get("subject", ""),
                                        run.ctx.get("grade", ""),
                                        run.ctx.get("topic", "")) if x)
        for ev in agents.run_agent(
                node["expert"], prompt, extra=extra,
                tools=tools,
                critique=bool(node.get("critique")),
                kb_query=kb_query,
                backfill=bool(node.get("backfill"))):
            if ev.get("type") == "delta":
                acc.append(ev["text"])
                yield ev
            elif ev.get("type") in THINK_TYPES:
                # 过程事件统一打上节点坐标与耗时，供前端按节点聚合/折叠
                out = dict(ev)
                out["index"] = idx
                out["node"] = node["id"]
                out["ms"] = _ms()
                out["label"] = THINK_LABEL.get(ev["type"], "思考")
                if ev.get("type") != "critique_delta":
                    run.node_states[idx]["thoughts"] += 1
                yield out
        full = "".join(acc)
    except Exception as e:  # 单节点失败不拖垮整条流水线
        run.node_states[idx]["status"] = "failed"
        run.node_states[idx]["error"] = str(e)
        run.node_states[idx]["ms"] = _ms()
        # 只落盘、不下发：保留栈尾便于排查偶发失败
        try:
            run.node_states[idx]["tb"] = traceback.format_exc()[-1500:]
        except Exception:
            pass
        run.save()
        yield {"type": "node_done", "index": idx, "id": node["id"],
               "status": "failed", "error": str(e), "ms": _ms()}
        yield {"type": "error", "message": "节点「%s」执行失败：%s"
               % (node.get("title_full") or node["title"], e)}
        return

    summary = _summarize(full)
    run.node_states[idx]["status"] = "ok"
    run.node_states[idx]["summary"] = summary
    run.node_states[idx]["ms"] = _ms()
    run.blackboard["outputs"][node["id"]] = full
    run.blackboard["summaries"][node["id"]] = summary
    run.save()
    yield {"type": "node_done", "index": idx, "id": node["id"],
           "status": "ok", "summary": summary, "ms": _ms(),
           "chars": len(full), "thoughts": run.node_states[idx]["thoughts"]}


def _reset_downstream(run, from_idx):
    """重跑某个节点时，清除它与其后所有节点的产物，避免陈旧上下文污染。"""
    for i in range(from_idx, len(run.node_states)):
        node = run.wf["nodes"][i]
        run.node_states[i]["status"] = "pending"
        run.node_states[i]["summary"] = []
        run.blackboard["outputs"].pop(node["id"], None)
        run.blackboard["summaries"].pop(node["id"], None)


# ---------------------------------------------------------------------------
# 主运行循环（生成器）
# ---------------------------------------------------------------------------
def run_workflow(run_id, resume=False, should_stop=None):
    """运行（或续跑）一次工作流，yield NDJSON 事件。

    调用方负责把事件逐行写入响应流；本函数是纯生成器，不触碰 HTTP。

    should_stop: 可选的 ``callable() -> bool``，返回 True 时立即中断
    （供并行任务管理器取消任务使用）。中断点只在节点边界生效，
    不会把某个节点砍成半截内容。
    """
    run = WorkflowRun.load(run_id)
    if run is None:
        yield {"type": "error", "message": "运行不存在或已被清理"}
        return

    if resume:
        if run.status == "waiting":
            run.status = "running"

    t_start = time.time()

    yield {
        "type": "workflow_start",
        "run_id": run.run_id,
        "id": run.wf_id,
        "title": run.wf["title"],
        "auto": run.auto,
        "topic": run.ctx.get("topic", ""),
        "nodes": [{"id": n["id"],
                   "title": n.get("title_full") or n["title"],
                   "expert": n.get("expert") or "",
                   "checkpoint": bool(n.get("checkpoint"))}
                  for n in run.wf["nodes"]],
    }

    n = len(run.wf["nodes"])
    idx = run.current
    while idx < n:
        if should_stop and should_stop():
            run.status = "cancelled"
            run.save()
            yield {"type": "done", "status": "cancelled", "run_id": run.run_id,
                   "ms": int((time.time() - t_start) * 1000)}
            return
        node = run.wf["nodes"][idx]
        run.current = idx
        is_checkpoint = bool(node.get("checkpoint"))

        # 检查点：交互模式暂停等待，全自动模式自动放行
        if is_checkpoint:
            if not run.auto:
                run.status = "waiting"
                run.save()
                yield {"type": "checkpoint", "index": idx, "id": node["id"],
                       "title": node.get("title_full") or node["title"],
                       "prompt": _fill(node.get("goal", ""), **run.ctx),
                       "run_id": run.run_id}
                yield {"type": "paused", "run_id": run.run_id, "resume_at": idx}
                return
            yield {"type": "checkpoint", "index": idx, "id": node["id"],
                   "title": node.get("title_full") or node["title"], "auto": True}

        # 执行产出型节点
        if node.get("expert"):
            for ev in _execute_node(run, node, idx):
                yield ev
            if run.node_states[idx]["status"] == "failed":
                run.status = "failed"
                run.finished_at = time.time()
                run.save()
                yield {"type": "done", "status": "failed", "run_id": run.run_id,
                       "ms": int((time.time() - t_start) * 1000)}
                return

            # 一致性校验：sca 节点完成后，与 aim 输出比对（确定性指标）
            if node["id"] == "sca" and "aim" in run.blackboard["outputs"]:
                try:
                    align = agents.alignment_score(
                        run.blackboard["outputs"]["aim"],
                        run.blackboard["outputs"].get("des", "")
                        + "\n" + run.blackboard["outputs"]["sca"])
                    if align is not None:
                        yield {"type": "consistency", "score": align,
                               "threshold": agents.ALIGN_THRESHOLD}
                except Exception:
                    pass

        idx += 1

    # 全部节点完成 → 装配并导出工件
    run.current = n              # 进度显示 5/5，而不是停在最后一个节点下标
    run.status = "done"
    run.finished_at = time.time()
    run.save()
    for ev in _finalize(run):
        yield ev
    yield {"type": "done", "status": "done", "run_id": run.run_id,
           "ms": int((time.time() - t_start) * 1000),
           "node_ms": {ns["id"]: ns.get("ms", 0) for ns in run.node_states}}


def _finalize(run):
    """装配最终工件并落盘（Word/PPT），yield artifact 事件。"""
    title, content = run.wf["assemble"](run)
    meta = {
        "subject": run.ctx.get("subject", ""),
        "grade": run.ctx.get("grade", ""),
        "topic": run.ctx.get("topic", ""),
        "workflow": run.wf_id,
        "run_id": run.run_id,
    }
    for kind in run.wf.get("produce", []):
        try:
            if kind == "docx":
                saved = lessons.save_lesson(title, content, meta)
                path = (saved.get("output_docx")
                        or os.path.join(lessons.LESSONS_DIR, saved.get("docx", "")))
                art = {"kind": "docx", "name": saved.get("docx"),
                       "path": path,
                       "dir": saved.get("output_dir") or lessons.LESSONS_DIR}
            elif kind == "pptx":
                data = lessons.export_pptx_bytes(title, content)
                safe = title.replace("/", "_").replace("\\", "_")[:40]
                path = os.path.join(lessons.LESSONS_DIR, safe + ".pptx")
                with open(path, "wb") as f:
                    f.write(data)
                art = {"kind": "pptx", "name": os.path.basename(path),
                       "path": path, "dir": lessons.LESSONS_DIR}
            else:
                continue
            run.artifacts.append(art)
            run.save()
            yield {"type": "artifact", "kind": art["kind"], "name": art["name"],
                   "path": art["path"], "dir": art["dir"]}
        except Exception as e:
            yield {"type": "error", "message": "导出%s失败：%s" % (kind, e)}


# ---------------------------------------------------------------------------
# 续跑 / 人工介入
# ---------------------------------------------------------------------------
def resume_workflow(run_id, decision, feedback=None, should_stop=None):
    """对处于 waiting（检查点）的运行做人工决策，返回续跑生成器。

    decision:
      approve  确认通过，继续下一个节点
      redo     重做当前检查点之前的产出节点（回退到上游重跑）
      cancel   取消运行
    """
    run = WorkflowRun.load(run_id)
    if run is None or run.status != "waiting":
        yield {"type": "error", "message": "没有处于等待确认的运行"}
        return

    idx = run.current
    node = run.wf["nodes"][idx]

    if decision == "cancel":
        run.status = "cancelled"
        run.save()
        yield {"type": "done", "status": "cancelled", "run_id": run.run_id}
        return

    if decision == "approve":
        run.node_states[idx]["status"] = "ok"
        run.node_states[idx]["feedback"] = feedback or ""
        run.current = idx + 1
        run.status = "running"
        run.save()
    elif decision == "redo":
        # 回退到当前检查点之前的最后一个产出节点重跑
        prev = idx - 1
        while prev >= 0 and not run.wf["nodes"][prev].get("expert"):
            prev -= 1
        if prev < 0:
            # 没有可重做的上游节点，退化为重跑确认
            run.current = idx
        else:
            _reset_downstream(run, prev)
            run.current = prev
        run.status = "running"
        run.save()
    else:
        yield {"type": "error", "message": "未知决策：%s" % decision}
        return

    for ev in run_workflow(run.run_id, resume=True, should_stop=should_stop):
        yield ev


def note_memory(run):
    """把一次完成的运行记入教师长期记忆（跨工作流延续风格）。"""
    try:
        c = run.ctx if isinstance(run, WorkflowRun) else run.get("ctx", {})
        memory.record(
            c.get("subject", ""), c.get("grade", ""), c.get("topic", ""),
            "工作流备课（%s，%s模式）"
            % (run.wf_id if isinstance(run, WorkflowRun) else run.get("wf_id", ""),
               "全自动" if (run.auto if isinstance(run, WorkflowRun) else run.get("auto"))
               else "交互"))
    except Exception:
        pass
