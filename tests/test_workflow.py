# -*- coding: utf-8 -*-
"""工作流引擎单元测试（mock LLM，不依赖 API Key 与网络）。

覆盖：工作流注册、全自动模式跑通、检查点暂停/续跑/重做、状态机持久化、
上下文黑板（只传摘要）、工件导出、摘要抽取。

用法：
  "C:/Users/FANG/.workbuddy/binaries/python/versions/3.13.12/python.exe" tests/test_workflow.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexedu import agents, lessons, workflow

# ---------------- mock LLM ----------------
FAKE = {
    "nexaim": (
        "### 课时目标叙写\n"
        "1. 学生能准确概括文章主要内容，正确率达 80%。\n"
        "2. 学生能说出作者的写作意图并举例说明。\n\n"
        "## 摘要\n"
        "· 目标一：能准确概括主要内容\n"
        "· 目标二：能说出写作意图\n"
        "· 素养维度：语言建构与运用\n"
    ),
    "nexdes": (
        "### 课时教学设计\n"
        "1. 学习目标：承接上游目标。\n"
        "2. 评价任务（前置）：复述并归纳主题。\n\n"
        "## 摘要\n"
        "· 活动：情境导入→问题链→小组任务\n"
        "· 分层：基础/进阶/挑战三档支架\n"
    ),
    "nexsca": (
        "### 分层作业单\n"
        "基础层：能准确概括主要内容；应用层：迁移简析；创新层：比较评价。\n\n"
        "## 摘要\n"
        "· 作业分基础/应用/创新三层\n"
        "· 每题标注对应目标\n"
    ),
    "coordinator": (
        "### 闭环回流卡\n"
        "缺口：评价证据略少。\n\n"
        "## 摘要\n"
        "· 需补充课堂观察证据\n"
    ),
}


class FakeLLMClient:
    """替换 agents.LLMClient：按 demo_role 返回确定的专家产出。"""

    def __init__(self, *a, **kw):
        self.model = "fake-model"
        self.available = True
        self.force_demo = False

    def chat(self, messages, temperature=0.3, stream=False, max_tokens=None,
             demo_role=None, task=None):
        if demo_role == "critic":
            yield "结论：通过\n问题：无。\n建议：无。"
            return
        body = FAKE.get(demo_role, FAKE["nexaim"])
        if not stream:
            yield body
            return
        for i in range(0, len(body), 21):
            yield body[i:i + 21]


agents.LLMClient = FakeLLMClient

# ---------------- 测试框架 ----------------
_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  → " + str(detail)) if detail and not cond else ""))


def collect(gen):
    evs = []
    for ev in gen:
        evs.append(ev)
    return evs


def types(evs, t):
    return [e for e in evs if e.get("type") == t]


def main():
    print("=" * 62)
    print("工作流引擎单元测试（mock LLM）")
    print("=" * 62)

    # ---- 1. 工作流注册 ----
    wfs = workflow.list_workflows()
    ids = [w["id"] for w in wfs]
    check("① 注册三条工作流", len(wfs) == 3, ids)
    check("① 含 w1-lesson/w2-unit/w4-paper",
          set(ids) == {"w1-lesson", "w2-unit", "w4-paper"}, ids)
    w1 = [w for w in wfs if w["id"] == "w1-lesson"][0]
    check("① W1 五个节点（含检查点）", len(w1["nodes"]) == 5, len(w1["nodes"]))
    check("① W1 标记含检查点", w1["has_checkpoint"] is True)
    check("① W1 产出 docx+pptx", w1["produce"] == ["docx", "pptx"], w1["produce"])

    # ---- 2. 摘要抽取 ----
    s = workflow._summarize(FAKE["nexaim"])
    check("② 摘要抽取到要点", len(s) == 3, s)
    check("② 摘要去掉了 · 前缀", all(not x.startswith("·") for x in s), s)
    s2 = workflow._summarize("### 标题一\n正文\n### 标题二\n正文", max_points=5)
    check("② 无摘要段时回退到标题", len(s2) >= 1, s2)
    check("② 摘要条数不超过 5", len(workflow._summarize(
        "## 摘要\n" + "\n".join("· 要点%d" % i for i in range(20)))) <= 5)

    # ---- 3. 全自动模式（W1） ----
    run = workflow.create_run("w1-lesson", "语文", "初中", "背影",
                              student_info="基础薄弱", auto=True)
    evs = collect(workflow.run_workflow(run.run_id))
    start = types(evs, "workflow_start")[0]
    check("③ 全自动：auto=True", start.get("auto") is True)
    check("③ 全自动：回传 5 个节点", len(start.get("nodes", [])) == 5)
    cp = types(evs, "checkpoint")
    check("③ 全自动：检查点自动放行", len(cp) == 1 and cp[0].get("auto") is True, cp)
    check("③ 全自动：无 paused 事件", len(types(evs, "paused")) == 0)
    nstart = types(evs, "node_start")
    check("③ 全自动：4 个产出节点启动", len(nstart) == 4, len(nstart))
    ndone = types(evs, "node_done")
    check("③ 全自动：节点全部成功",
          len(ndone) == 4 and all(d.get("status") == "ok" for d in ndone),
          [d.get("status") for d in ndone])
    check("③ 全自动：一致性校验事件", len(types(evs, "consistency")) == 1)
    arts = types(evs, "artifact")
    check("③ 全自动：导出 docx+pptx 两件工件",
          sorted(a["kind"] for a in arts) == ["docx", "pptx"],
          [a["kind"] for a in arts])
    done = types(evs, "done")
    check("③ 全自动：结束状态 done",
          bool(done) and done[-1].get("status") == "done", done)

    # 持久化与产物
    loaded = workflow.WorkflowRun.load(run.run_id)
    check("③ 状态持久化：status=done", loaded.status == "done", loaded.status)
    check("③ 状态持久化：current 走完", loaded.current == 5, loaded.current)
    check("③ 黑板留存 4 份产出", len(loaded.blackboard["outputs"]) == 4,
          list(loaded.blackboard["outputs"]))
    check("③ 每份产出都有摘要（≤5）",
          all(0 < len(v) <= 5 for v in loaded.blackboard["summaries"].values()),
          {k: len(v) for k, v in loaded.blackboard["summaries"].items()})
    check("③ 工件文件真实落盘",
          all(os.path.exists(a["path"]) for a in loaded.artifacts),
          [a["path"] for a in loaded.artifacts])

    # ---- 4. 上下文黑板：只把摘要传给下游 ----
    node_des = loaded.wf["nodes"][1]
    ctx = loaded.context_for(node_des)
    check("④ 下游上下文含上游摘要",
          "能准确概括主要内容" in ctx or "目标一" in ctx, ctx[:80])
    check("④ 下游上下文含原始需求", "背影" in ctx and "初中" in ctx)
    check("④ 下游上下文不含上游全文",
          "### 课时目标叙写" not in ctx)

    # ---- 5. 交互模式：检查点暂停 ----
    r2 = workflow.create_run("w1-lesson", "数学", "初中", "一元二次方程",
                             auto=False)
    evs2 = collect(workflow.run_workflow(r2.run_id))
    paused = types(evs2, "paused")
    check("⑤ 交互模式：产出 paused 事件", len(paused) == 1, paused)
    check("⑤ 交互模式：停在检查点下标", paused and paused[0].get("resume_at") == 3,
          paused[0].get("resume_at") if paused else None)
    cp2 = types(evs2, "checkpoint")
    check("⑤ 交互模式：checkpoint 无 auto 标记",
          cp2 and cp2[0].get("auto") is not True, cp2)
    l2 = workflow.WorkflowRun.load(r2.run_id)
    check("⑤ 交互模式：状态 waiting", l2.status == "waiting", l2.status)
    check("⑤ 交互模式：检查点前 3 个节点已完成",
          sum(1 for n in l2.node_states if n["status"] == "ok") == 3,
          [n["status"] for n in l2.node_states])
    check("⑤ 交互模式：暂停时不导出工件", len(l2.artifacts) == 0)
    check("⑤ 运行出现在列表里",
          any(x["run_id"] == r2.run_id for x in workflow.list_runs()))

    # ---- 6. 续跑（approve）----
    evs3 = collect(workflow.resume_workflow(r2.run_id, "approve"))
    done3 = types(evs3, "done")
    check("⑥ 续跑：完成", bool(done3) and done3[-1].get("status") == "done",
          done3[-1] if done3 else None)
    l3 = workflow.WorkflowRun.load(r2.run_id)
    check("⑥ 续跑：状态 done", l3.status == "done", l3.status)
    check("⑥ 续跑：补齐闭环回流节点",
          l3.node_states[4]["status"] == "ok", l3.node_states[4]["status"])
    check("⑥ 续跑：才导出工件", len(l3.artifacts) >= 1, len(l3.artifacts))
    check("⑥ 续跑不重跑已完成节点",
          l3.node_states[0]["status"] == "ok"
          and l3.node_states[2]["status"] == "ok")

    # ---- 7. 重做（redo）：回退上游重跑 ----
    r3 = workflow.create_run("w1-lesson", "语文", "初中", "春", auto=False)
    collect(workflow.run_workflow(r3.run_id))
    before = workflow.WorkflowRun.load(r3.run_id)
    check("⑦ 重做前：停在检查点", before.status == "waiting")
    collect(workflow.resume_workflow(r3.run_id, "redo"))
    after = workflow.WorkflowRun.load(r3.run_id)
    check("⑦ 重做后：回退到检查点再次等待",
          after.status == "waiting" and after.current == 3,
          (after.status, after.current))
    check("⑦ 重做后：上游 sca 重新跑过",
          after.node_states[2]["status"] == "ok"
          and after.blackboard["outputs"].get("sca"))

    # ---- 8. 取消 ----
    r4 = workflow.create_run("w2-unit", "语文", "初中", "第三单元", auto=False)
    collect(workflow.run_workflow(r4.run_id))
    collect(workflow.resume_workflow(r4.run_id, "cancel"))
    l4 = workflow.WorkflowRun.load(r4.run_id)
    check("⑧ 取消：状态 cancelled", l4.status == "cancelled", l4.status)

    # ---- 9. 参数校验 ----
    try:
        workflow.create_run("w1-lesson", "语文", "初中", "")
        check("⑨ 空课题应报错", False, "未抛异常")
    except ValueError:
        check("⑨ 空课题应报错", True)
    try:
        workflow.create_run("nope", "语文", "初中", "背影")
        check("⑨ 未知工作流应报错", False, "未抛异常")
    except ValueError:
        check("⑨ 未知工作流应报错", True)

    # ---- 清理测试产生的教案（按完整标题精确匹配，避免误删真实教案）----
    try:
        titles = {"初中语文《背影》教学设计",
                  "初中数学《一元二次方程》教学设计",
                  "初中语文《春》教学设计"}
        for x in lessons.list_lessons():
            if x["title"] in titles:
                lessons.delete_lesson(x["id"])
        for a in loaded.artifacts:
            if a["kind"] == "pptx" and os.path.exists(a["path"]):
                os.remove(a["path"])
    except Exception:
        pass

    # ---- 汇总 ----
    ok = sum(1 for _, c, _ in _RESULTS if c)
    bad = [n for n, c, _ in _RESULTS if not c]
    print("-" * 62)
    print("通过 %d/%d" % (ok, len(_RESULTS)))
    if bad:
        print("失败项：%s" % ", ".join(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
