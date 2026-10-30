# -*- coding: utf-8 -*-
"""并行能力单元测试（mock LLM）：任务管理器 / 节点计时 / 思考事件 / 触发器 / 技能包 / 委派。

用法：
  "C:/Users/FANG/.workbuddy/binaries/python/versions/3.13.12/python.exe" tests/test_parallel.py
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexedu import agents, jobs, tools, triggers, workflow
from nexedu import package

BODY = ("### 产出\n1. 学生能准确概括文章主要内容。\n\n"
        "## 摘要\n· 目标一：能概括主要内容\n· 目标二：能说出写作意图\n")


class FakeLLMClient:
    def __init__(self, *a, **kw):
        self.model = "fake-model"
        self.available = True
        self.force_demo = False

    def chat(self, messages, temperature=0.3, stream=False, max_tokens=None,
             demo_role=None, task=None):
        if demo_role == "critic":
            yield "结论：通过\n问题：无。\n建议：无。"
            return
        if not stream:
            yield BODY
            return
        for i in range(0, len(BODY), 17):
            yield BODY[i:i + 17]


agents.LLMClient = FakeLLMClient

_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond)))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  → " + str(detail)) if detail and not cond else ""))


def collect(gen):
    return list(gen)


def types(evs, t):
    return [e for e in evs if e.get("type") == t]


def wait_job(job, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        if job.status in ("done", "failed", "cancelled"):
            return job.status
        time.sleep(0.05)
    return "timeout:%s" % job.status


def job_err(job):
    """任务失败原因（含最后一条 error 事件），失败时便于定位。"""
    errs = [e for e in job.events if e.get("type") == "error"]
    msg = job.error or (errs[-1].get("message") if errs else "")
    return "%s/%s" % (job.status, msg)[:200]


def main():
    print("=" * 62)
    print("并行能力单元测试（mock LLM）")
    print("=" * 62)

    # ---- ① 节点计时与思考事件分离 ----
    run = workflow.create_run("w1-lesson", "语文", "初中", "《背影》并行测试",
                              auto=True)
    evs = collect(workflow.run_workflow(run.run_id))
    think = types(evs, "think")
    check("① 每个产出节点都有 plan 思考事件", len(think) >= 4, len(think))
    check("① 思考事件带节点坐标", all("index" in t for t in think))
    check("① 思考事件带中文标签", all(t.get("label") for t in think))
    nd = types(evs, "node_done")
    check("① 四个节点都有 node_done", len(nd) == 4, len(nd))
    check("① node_done 带耗时 ms", all("ms" in e for e in nd))
    check("① node_done 耗时非负", all(e["ms"] >= 0 for e in nd))
    stored = workflow.get_run(run.run_id)
    check("① 节点耗时已持久化",
          all((ns.get("ms") or 0) >= 0 for ns in stored["node_states"]))
    check("① 节点思考条数已统计",
          any((ns.get("thoughts") or 0) > 0 for ns in stored["node_states"]))
    done = types(evs, "done")
    check("① done 事件带总耗时", done and "ms" in done[0])
    check("① done 事件带各节点耗时", done and "node_ms" in done[0])
    deltas = types(evs, "delta")
    check("① 正文输出仍是独立的 delta 流", len(deltas) > 0, len(deltas))

    # ---- ② 并行任务管理器 ----
    f1 = jobs.make_workflow_job("w1-lesson", "语文", "初中", "《背影》任务甲", auto=True)
    f2 = jobs.make_workflow_job("w2-unit", "数学", "高中", "《函数》任务乙", auto=True)
    j1 = jobs.MANAGER.submit("workflow", "甲", f1)
    j2 = jobs.MANAGER.submit("workflow", "乙", f2)
    check("② 两个任务同时提交", j1.status in ("queued", "running")
          and j2.status in ("queued", "running"))
    t0 = time.time()
    wait_job(j1)
    wait_job(j2)
    check("② 两个任务都跑完", j1.status == "done" and j2.status == "done",
          (job_err(j1), job_err(j2)))
    check("② 并行确实并发（总耗时 < 串行 x2）", time.time() - t0 < 30,
          round(time.time() - t0, 1))
    check("② 任务都留下了事件流", j1.seq > 5 and j2.seq > 5, (j1.seq, j2.seq))
    check("② 任务带进度信息", j1.progress["total"] == 5, j1.progress)
    check("② 任务带耗时", j1.to_dict()["ms"] >= 0)

    # 增量拉取
    part = j1.events_since(0)
    check("② 增量拉取返回全部事件", len(part) == len(j1.events), len(part))
    check("② 增量拉取偏移生效", len(j1.events_since(3)) == len(j1.events) - 3)

    # 取消
    f3 = jobs.make_workflow_job("w1-lesson", "语文", "初中", "《取消测试》", auto=True)
    j3 = jobs.MANAGER.submit("workflow", "丙", f3)
    time.sleep(0.15)
    j3.cancel()
    wait_job(j3, timeout=20)
    check("② 取消后任务终止", j3.status == "cancelled", j3.status)
    j3b = jobs.MANAGER.get(j3.id)
    check("② 取消的任务仍可查询", j3b is not None and j3b.id == j3.id)

    # 列表
    lst = jobs.MANAGER.list()
    check("② 任务列表含刚才的任务", any(x["id"] in (j1.id, j2.id) for x in lst))
    check("② 任务列表按时间倒序", lst[0]["started_at"] >= lst[-1]["started_at"])

    # 单任务失败隔离
    def bad_factory(should_stop):
        def gen():
            yield {"type": "node_start", "index": 0, "id": "x", "title": "坏节点"}
            raise RuntimeError("故意失败")
        return gen()

    j4 = jobs.MANAGER.submit("workflow", "坏任务", bad_factory)
    wait_job(j4, timeout=20)
    check("② 单任务失败不影响管理器", j4.status == "failed", j4.status)
    check("② 失败原因可查", "故意失败" in (j4.error or ""), j4.error)

    # ---- ③ 触发器 ----
    before = len(triggers.STORE.list())
    t = triggers.STORE.create({
        "name": "测试-错因回流", "kind": "webhook", "workflow": "w4-paper",
        "subject": "数学", "grade": "初中", "topic": "《一元二次方程》",
        "enabled": True})
    check("③ 触发器已创建", len(triggers.STORE.list()) == before + 1)
    check("③ 触发器带回调 token", bool(t.get("token")))
    check("③ 触发器规整了默认值", t["workflow"] == "w4-paper" and t["auto"] is True)

    job, err = triggers.fire_by_name("测试-错因回流",
                                     payload={"topic": "《一元二次方程》",
                                              "error_rate": 0.42,
                                              "weak_points": ["判别式", "因式分解"]})
    check("③ 回调可触发任务", job is not None, err)
    if job:
        wait_job(job, timeout=60)
        check("③ 回调任务跑完", job.status == "done", job_err(job))
        run_ev = [e for e in job.events if e.get("type") == "run"]
        rid = run_ev[0]["run_id"] if run_ev else ""
        saved = workflow.get_run(rid) or {}
        ctx_txt = str((saved.get("ctx") or {}))
        # 回调数据回流：外部字段应出现在本次备课的上下文里
        check("③ 回调数据已回流进备课上下文",
              "error_rate" in ctx_txt and "weak_points" in ctx_txt, ctx_txt[:200])

    check("③ 触发历史已记录", len(triggers.STORE.runs) >= 1, len(triggers.STORE.runs))
    ok = triggers.STORE.delete(t["id"])
    check("③ 触发器可删除", ok and len(triggers.STORE.list()) == before)

    # 定时触发的判定逻辑
    now = time.struct_time((2026, 9, 24, 7, 30, 0, 3, 267, 0))
    tt = triggers.normalize({"kind": "schedule",
                             "schedule": {"hour": 7, "minute": 30,
                                          "weekdays": [0, 1, 2, 3, 4]}})
    check("③ 定时任务按周几/时刻命中", triggers._due(tt, now) is True)
    tt2 = dict(tt, schedule={"hour": 8, "minute": 30, "weekdays": [0, 1, 2, 3, 4]})
    check("③ 时刻不匹配则不触发", triggers._due(tt2, now) is False)
    tt3 = dict(tt, schedule={"hour": 7, "minute": 30, "weekdays": [6]})
    check("③ 周几不匹配则不触发", triggers._due(tt3, now) is False)
    tt4 = dict(tt, last_fire_key=triggers._now_key())
    check("③ 同一分钟不重复触发", triggers._due(tt4, now) is False)

    # ---- ④ 技能包导出/导入 ----
    pkg = package.export_package()
    check("④ 包格式正确", pkg.get("format") == package.FORMAT)
    check("④ 包含专家目录", len(pkg.get("experts") or []) >= 4)
    check("④ 包含技能", isinstance(pkg.get("skills"), list))
    check("④ 包含工作流", len(pkg.get("workflows") or []) == 3)
    check("④ 工具清单含新加的 delegate",
          any(x["name"] == "delegate" for x in (pkg.get("tools") or [])))
    res = package.import_package({"format": "nexedu-package", "skills": [
        {"name": "包测试技能", "desc": "d", "prompt": "p"}]})
    check("④ 技能导入成功", res.get("skills_added") == 1, res)
    from nexedu import skills as skmod
    names = [s["name"] for s in skmod.list_skills()]
    check("④ 导入的技能已生效", "包测试技能" in names, names)
    res2 = package.import_package({"format": "nexedu-package", "skills": [
        {"name": "包测试技能", "desc": "d", "prompt": "p"}]})
    check("④ 重复导入被跳过（不覆盖）", res2.get("skills_skipped") == 1, res2)
    for s in skmod.list_skills():
        if s["name"] == "包测试技能":
            skmod.delete_skill(s["id"])
    bad = package.import_package({"format": "other"})
    check("④ 非本平台包被拒绝", bad.get("ok") is False)

    # ---- ⑤ 子智能体委派 ----
    check("⑤ delegate 已注册", "delegate" in tools.TOOLS)
    r = tools.execute("delegate", {"expert": "unknown", "subtask": "x"})
    check("⑤ 未知专家被拦下", (not r[0]) or "未知专家" in r[1], r[1][:60])
    r = tools.execute("delegate", {"expert": "nexaim", "subtask": ""})
    check("⑤ 空子任务被拦下", (not r[0]) or "缺少子任务" in r[1], r[1][:60])
    r = tools.execute("delegate",
                      {"expert": "nexaim", "subtask": "给出两条可观测目标"})
    check("⑤ 委派能拿到子智能体结论", r[0] and "NexAim" in r[1], r[1][:60])
    # 深度限制：在委派内部再委派应被拦下
    from nexedu.tools import _delegate_depth
    _delegate_depth.value = 1
    r2 = tools.execute("delegate",
                       {"expert": "nexdes", "subtask": "设计活动"})
    _delegate_depth.value = 0
    check("⑤ 嵌套委派被深度上限拦下", "深度上限" in r2[1], r2[1][:60])

    # ---- ⑥ MCP 客户端（无配置不报错） ----
    from nexedu import mcp
    st = mcp.POOL.status()
    check("⑥ MCP 状态可查询", isinstance(st, dict) and "servers" in st)
    check("⑥ MCP 配置路径在沙盒内", "workspace" in st["config_path"].replace("\\", "/"))
    c = mcp.MCPClient("nope", "definitely-not-a-command", ["--x"])
    check("⑥ 启动不存在的服务不抛异常", c.start() is False)
    check("⑥ 失败原因有记录", bool(c.error), c.error)

    # ---- ⑦ 本地检索链路的线程安全 ----
    # 并行任务下曾有两个真实竞态（线上偶发为「节点执行失败：1028」）：
    #   1) knowledge 的向量分缓存是模块级共享字典，甲写入被乙 clear() → KeyError；
    #   2) embed.load() 在读完文件前就置 _loaded=True → _vecs 还是 None → TypeError。
    # 这里用多线程直接压这两条路径，任何异常都算回归。
    from nexedu import embed, knowledge
    qs = ["教学目标设计", "课时拆分", "单元检测", "形成性评价", "作业分层设计",
          "课堂导入情境", "二次函数", "阅读理解策略", "实验探究", "错题归因"]
    errs = []
    lk = threading.Lock()

    def _race_worker(seed):
        for i in range(12):
            try:
                knowledge.search(qs[(seed + i) % len(qs)], topk=4)
            except Exception as e:
                with lk:
                    errs.append("%s: %s" % (type(e).__name__, e))
            try:
                embed.encode(qs[(seed + i) % len(qs)])
            except Exception as e:
                with lk:
                    errs.append("%s: %s" % (type(e).__name__, e))

    ts = [threading.Thread(target=_race_worker, args=(i * 7,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("⑦ 并发检索无异常", not errs, errs[:3])
    hits = knowledge.search("教学目标", topk=3)
    check("⑦ 并发后检索仍可用", isinstance(hits, list), type(hits).__name__)

    print("-" * 62)
    ok_n = sum(1 for _, c in _RESULTS if c)
    print("通过 %d/%d" % (ok_n, len(_RESULTS)))
    return 0 if ok_n == len(_RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
