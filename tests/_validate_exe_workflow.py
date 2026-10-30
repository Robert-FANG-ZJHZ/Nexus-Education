# -*- coding: utf-8 -*-
"""打包后的 exe 端到端校验：工作流引擎与一键备课全自动是否真的进了成品。

流程：启动 dist/NexEdu.exe（演示模式、不开浏览器）→ 打真实 HTTP 接口 → 关闭。
不依赖 shell（直接用 subprocess 起进程）。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "NexEdu.exe")
PORT = 8793
BASE = "http://127.0.0.1:%d" % PORT

_RESULTS = []
TICKET = ""      # 页面票据（服务端返回页面时注入，模拟真实客户端才有）


def auth(token):
    """真实客户端的请求头：令牌 + 页面票据（缺票据会被服务端拒绝）。"""
    return {"X-NexEdu-Token": token, "X-NexEdu-Page": TICKET}


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  → " + str(detail)) if detail and not cond else ""))


def page_ticket():
    """模拟真实客户端：加载页面取回服务端注入的票据（第三方脚本拿不到）。"""
    html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode(
        "utf-8", "ignore")
    m = re.search(r"__NEXEDU_PAGE__='([0-9a-f]+)'", html)
    return m.group(1) if m else ""


def wait_ready(timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            # 先取页面票据，再凭票据换 token（与服务端的防爬取校验同路径）
            req = urllib.request.Request(
                BASE + "/api/status",
                headers={"X-NexEdu-Page": TICKET})
            j = json.loads(urllib.request.urlopen(req, timeout=5).read())
            return j.get("token")
        except Exception:
            time.sleep(1)
    return None


def post_stream(path, payload, token):
    req = urllib.request.Request(
        BASE + path,
        headers={"X-NexEdu-Token": token, "X-NexEdu-Page": TICKET,
                 "Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"), method="POST")
    resp = urllib.request.urlopen(req, timeout=300)
    evs = []
    for line in resp:
        line = line.decode("utf-8", "ignore").strip()
        if line:
            try:
                evs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return evs


def main():
    global TICKET
    print("=" * 62)
    print("exe 端到端校验（工作流引擎 + 一键备课全自动）")
    print("=" * 62)
    if not os.path.exists(EXE):
        print("未找到 exe:", EXE)
        return 1

    env = dict(os.environ)
    env["NEXEDU_PORT"] = str(PORT)
    env["NEXEDU_OPEN_BROWSER"] = "0"
    env["NEXEDU_FORCE_DEMO"] = "1"
    proc = subprocess.Popen([EXE], env=env, cwd=os.path.dirname(EXE))
    token = None
    try:
        # 与真实客户端一致：先加载页面取回票据，再凭票据换 token
        for _ in range(30):
            try:
                TICKET = page_ticket()
                if TICKET:
                    break
            except Exception:
                time.sleep(1)
        token = wait_ready()
        check("⓪ exe 启动并可访问", bool(token), "启动超时")
        if not token:
            return 1

        # ① 前端是否已带上一键备课全自动与工作流引擎入口
        html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode(
            "utf-8", "ignore")
        check("① 页面含节点进度条入口", "wfProgress" in html)
        check("① 一键备课按钮已改为全自动", "一键备课 · 全自动" in html)
        check("① 页面含事件渲染器", "wfEventHandler" in html)
        # 本轮改动：取消专家栏 + 并行任务 + 思考过程折叠
        check("① 左侧专家栏已取消", 'id="expertList"' not in html)
        # 本轮改动：任务中心 / 工作流引擎**后台化**——不再有独立视图与导航项，
        # 改为对话页与一键备课页里的 ⚙ 芯片按需启用。
        check("① 任务中心已后台化（无独立视图）", 'id="view-tasks"' not in html)
        check("① 工作流引擎已后台化（无独立导航）", 'data-view="workflow"' not in html)
        # 并发粒度是「会话」：快速对话仍是单窗口，新建对话/历史对话继续追问后
        # 多个会话可各自在后台同时生成，最多 6 个，由侧栏「进行中」管理。
        check("① 对话区为单窗口容器", 'id="messages"' in html)
        check("① 已无多窗口网格（不在一页开六个窗口）", 'id="chatLanes"' not in html)
        check("① 会话并发上限 6 个", "MAX_RUNS = 6" in html)
        check("① 侧栏含「进行中」列表", 'id="runList"' in html)
        check("① 可新建对话", 'id="newChatBtn"' in html)
        check("① 会话可单独停止", "function stopTask" in html)
        # 本轮：滑块（滚动条）、进行中只留运行中的、删对话连带删产出、防爬取
        check("① 页面含滚动条滑块样式", "::-webkit-scrollbar-thumb" in html)
        # 对话区必须能真滚动：flex 子项缺 min-height:0 会被撑开并被外层裁掉
        check("① 对话区锁定视口高度（内容可滚动）", "min-height:0" in html)
        check("① 含回到底部入口", 'id="jumpBottom"' in html)
        check("① 对话内容可一键复制", "function makeCopyBtn" in html)
        check("① 生成失败可原地重试",
              "function addRetryRow" in html and "async function retryTask" in html)
        check("① 宽表格可横向滚动", 'class="md-table"' in html)
        check("① 进行中只列运行中的会话", "暂无正在生成的对话" in html)
        check("① 删除对话会连带删除产出文件", "生成的本地文件" in html)
        # 页面票据：服务端只在返回页面时注入，脚本拿不到就调不通接口
        check("① 页面已注入页面票据",
              bool(re.search(r"__NEXEDU_PAGE__='[0-9a-f]{32}'", html)))
        check("① 请求携带页面票据", "X-NexEdu-Page" in html)
        check("① 任务中心已无抽屉/启用开关",
              'class="task-drawer"' not in html and 'id="tsEnableChip"' not in html)
        check("① 工作流引擎已无启用开关（已内置，无面板）",
              'id="wfEnableChip"' not in html and 'class="wf-embed"' not in html)
        check("① 已取消智能体模式开关", 'id="agentToggle"' not in html)
        check("① 一键备课已去掉闪电图标", "⚡ 一键备课" not in html)
        # 本轮改动：42 位专家矩阵 + 内置技能（不进工坊，只读区）
        check("① 技能工坊含检索框", 'id="skillSearch"' in html)
        check("① 技能工坊含筛选条", 'id="skillGroups"' in html)
        check("① 技能工坊说明内置技能自动调用", "自动调用" in html)
        check("① 页面已无内置技能只读区（内置不进工坊）",
              'id="builtinBody"' not in html and 'id="builtinGroups"' not in html
              and 'toggleBuiltin' not in html)
        check("① 页面含思考折叠区", 'class="think-box"' in html)
        check("① 页面含节点卡片渲染", "function makeNodeCard" in html)
        check("① 页面仍保留后台任务事件通道", "/api/jobs/events" in html)

        # ② 工作流列表（证明 workflow 模块被打包进去）
        req = urllib.request.Request(BASE + "/api/workflows",
                                     headers=auth(token))
        ws = json.loads(urllib.request.urlopen(req, timeout=30).read()).get(
            "workflows", [])
        check("② /api/workflows 返回 3 条", len(ws) == 3, len(ws))
        check("② 含 w1-lesson", any(w["id"] == "w1-lesson" for w in ws),
              [w["id"] for w in ws])

        # ②-2 专家矩阵与内置技能库是否进了成品
        def _get(path):
            r = urllib.request.Request(BASE + path,
                                       headers=auth(token))
            return json.loads(urllib.request.urlopen(r, timeout=30).read())

        ex = _get("/api/experts")
        check("② 专家矩阵 40+ 位进了成品", len(ex) >= 40, len(ex))
        stages = {e.get("stage") for e in ex if e.get("stage")}
        check("② 专家按阶段分组（≥10）", len(stages) >= 10, sorted(stages))
        sk = _get("/api/skills")
        builtin = [s for s in sk if s.get("builtin")]
        check("② 技能工坊不含内置技能（只返回自建）", len(builtin) == 0, len(builtin))
        gj = _get("/api/skills/groups")
        check("② 内置技能 170+ 条进了成品（分组索引）",
              (gj.get("counts") or {}).get("builtin", 0) >= 170,
              gj.get("counts"))
        check("② 技能分组接口可用（≥15 组）",
              len(gj.get("groups") or []) >= 15, len(gj.get("groups") or []))

        # ③ 一键备课走的那条路：W1 全自动
        evs = post_stream("/api/workflow/run", {
            "workflow": "w1-lesson", "subject": "语文", "grade": "初中",
            "topic": "背影", "student_info": "基础薄弱", "auto": True}, token)
        st = [e for e in evs if e.get("type") == "workflow_start"]
        check("③ 全自动启动", bool(st) and st[0].get("auto") is True)
        ns = [e for e in evs if e.get("type") == "node_start"]
        check("③ 4 个产出节点", len(ns) == 4, len(ns))
        cp = [e for e in evs if e.get("type") == "checkpoint"]
        check("③ 检查点自动放行", len(cp) == 1 and cp[0].get("auto") is True, cp)
        arts = [e for e in evs if e.get("type") == "artifact"]
        check("③ 导出 docx+pptx",
              sorted(a["kind"] for a in arts) == ["docx", "pptx"],
              [a["kind"] for a in arts])
        check("③ 工件真实落盘",
              arts and all(os.path.exists(a["path"]) for a in arts),
              [a.get("path") for a in arts])
        done = [e for e in evs if e.get("type") == "done"]
        check("③ 结束状态 done", bool(done) and done[-1].get("status") == "done",
              done[-1:])

        # ④ 交互模式 + 续跑（引擎的检查点机制在成品里同样可用）
        evs2 = post_stream("/api/workflow/run", {
            "workflow": "w2-unit", "subject": "语文", "grade": "初中",
            "topic": "济南的冬天", "auto": False}, token)
        pz = [e for e in evs2 if e.get("type") == "paused"]
        check("④ 交互模式停在检查点", bool(pz) and bool(pz[0].get("run_id")), pz[:1])
        if pz:
            evs3 = post_stream("/api/workflow/resume",
                               {"run_id": pz[0]["run_id"],
                                "decision": "approve"}, token)
            d3 = [e for e in evs3 if e.get("type") == "done"]
            check("④ 续跑完成", bool(d3) and d3[-1].get("status") == "done", d3[-1:])

        # ⑤ 并行任务（新能力在成品里可用）
        def getj(path):
            req = urllib.request.Request(BASE + path,
                                         headers=auth(token))
            return json.loads(urllib.request.urlopen(req, timeout=60).read())

        def postj(path, payload):
            req = urllib.request.Request(
                BASE + path,
                headers=dict(auth(token),
                             **{"Content-Type": "application/json"}),
                data=json.dumps(payload).encode("utf-8"), method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=60).read())

        r1 = postj("/api/jobs/run", {"kind": "workflow", "workflow": "w1-lesson",
                                     "subject": "语文", "grade": "初中",
                                     "topic": "春", "auto": True})
        check("⑤ 提交后台任务成功", r1.get("ok") is True and r1["job"]["id"], r1)
        jid = r1["job"]["id"]
        end = time.time() + 180
        ev = {}
        while time.time() < end:
            ev = getj("/api/jobs/events?id=%s&since=0" % jid)
            if ev.get("status") in ("done", "failed", "cancelled"):
                break
            time.sleep(0.6)
        check("⑤ 后台任务跑完", ev.get("status") == "done", ev.get("status"))
        evs = ev.get("events", [])
        check("⑤ 任务含思考事件",
              any(x.get("type") == "think" for x in evs))
        check("⑤ 任务含节点耗时",
              any(x.get("type") == "node_done" and x.get("ms") is not None
                  for x in evs))
        check("⑤ 任务列表可查",
              any(x["id"] == jid for x in getj("/api/jobs")["jobs"]))
        check("⑤ MCP 状态接口可用", isinstance(getj("/api/mcp")["servers"], list))
        check("⑤ 触发器接口可用", isinstance(getj("/api/triggers")["triggers"], list))
        check("⑤ 技能包接口可用",
              getj("/api/package").get("format") == "nexedu-package")

        # ⑥ 自动路由（快速对话不再需要手选专家）
        evs6 = post_stream("/api/chat", {"expert": "auto",
                                         "message": "帮我设计《春》的教学目标"},
                           token)
        rt = [e for e in evs6 if e.get("type") == "routed"]
        check("⑥ 快速对话自动路由", len(rt) == 1 and rt[0].get("auto") is True,
              rt[:1])

        # ⑦ 原有能力未被破坏
        req = urllib.request.Request(BASE + "/api/lessons/list",
                                     headers=auth(token))
        j = json.loads(urllib.request.urlopen(req, timeout=30).read())
        check("⑦ 教案沙盒接口正常", isinstance(j, list), type(j))
        req = urllib.request.Request(BASE + "/api/experts",
                                     headers=auth(token))
        ex = json.loads(urllib.request.urlopen(req, timeout=30).read())
        check("⑦ 专家矩阵正常（40+ 位）", len(ex) >= 40, len(ex))

        # ⑧ 内置知识库完整内嵌（不依赖桌面源目录）+ 本轮新增能力进了成品
        req = urllib.request.Request(BASE + "/api/kb/status",
                                     headers=auth(token))
        kb = json.loads(urllib.request.urlopen(req, timeout=120).read())
        check("⑧ 内置知识库已完整内嵌（seed ≥ 10000 片）",
              int(kb.get("seed_chunks") or 0) >= 10000,
              "seed=%s total=%s" % (kb.get("seed_chunks"),
                                    kb.get("total_chunks")))
        check("⑧ 未依赖桌面源目录（local 为 0）",
              int(kb.get("local_chunks") or 0) == 0, kb.get("local_chunks"))
        check("⑧ 知识库索引就绪", kb.get("phase") == "ready", kb.get("phase"))

        def post(path, payload):
            data = json.dumps(payload).encode("utf-8")
            r = urllib.request.Request(BASE + path, data=data, method="POST",
                                       headers=dict(auth(token),
                                                    **{"Content-Type":
                                                       "application/json"}))
            return json.loads(urllib.request.urlopen(r, timeout=60).read())

        j = post("/api/verify", {"text": "这是一份很短的教案。", "kind": "lesson"})
        check("⑧ 产出核验进了成品（能判出问题）", j.get("passed") is False,
              j.get("issues"))
        j = post("/api/classroom", {"action": "list"})
        check("⑧ 班级学情接口进了成品", "classes" in j, j)
        j = post("/api/options", {"message": "八年级语文《背影》45分钟"})
        check("⑧ 多方案择优进了成品", (len(j.get("candidates") or [])) == 3,
              len(j.get("candidates") or []))
        check("⑧ 页面含产出核验卡片渲染", "addVerifyCard" in html)
        check("⑧ 页面含班级学情入口", "loadClasses" in html)
        # 本轮界面改动：批量删除对话 / 结果去方框 / 内容默认折叠可展开
        check("⑧ 页面含批量删除入口", 'id="batchDelBtn"' in html)
        check("⑧ 页面含批量删除执行函数", "function deletePickedSessions" in html)
        check("⑧ 批量删除已绑定事件", "$('#batchDelete').onclick" in html)
        check("⑧ 备课结果已去掉外层方框",
              'class="result plain" id="lessonResult"' in html)
        check("⑧ 备课结果已移除标题框", "<h2 data-page-node-id=\"UXYw0fCFN5DTDaATrBpUzW\">备课结果</h2>" not in html
              and "备课结果</h2>" not in html)
        check("⑧ 节点卡片支持默认折叠",
              "card.className = 'node-card' + (fold ? ' collapsed' : '')" in html)
        check("⑧ 仅备课启用折叠（不影响聊天正文）", "ev.expert, ev.index, true)" in html)
        check("⑧ 折叠后隐藏正文与思考区", ".node-card.collapsed .node-out," in html)
        check("⑧ 提供统一开合工具条", "function toggleAllNodes" in html
              and 'id="lessonTools"' in html)
        # 本轮界面文案改动（2026-10-22）：欢迎页 / 备课页 / 技能工坊 / 知识库 / 设置页
        check("⑧ 欢迎页新文案（教学评一体化）",
              "聚焦教学评一体化的教育智能体：目标设计、教学设计、评价设计" in html)
        check("⑧ 欢迎页新文案（请说出您的教学需求）",
              "请说出您的教学需求，系统将为您自动匹配对应专家，自动执行相应任务" in html)
        check("⑧ 欢迎页删除「智能体已就绪」", 'id="agentHint"' not in html)
        check("⑧ 一键备课副标题改为目标-教学-评价",
              "目标设计 - 教学设计 - 评价设计" in html)
        check("⑧ 一键备课删除工作流说明段",
              "由工作流引擎全自动执行" not in html and "成本提示" not in html)
        check("⑧ 我的教案改简名", "我的教案（本地沙盒）" not in html
              and "打开沙盒文件夹" not in html and ">打开文件夹<" in html)
        check("⑧ 技能工坊删除分两类说明", "技能分两类" not in html)
        check("⑧ 知识库我的文档新说明",
              "上传文件，打造你的专属知识库" in html)
        check("⑧ 设置页副标题精简", "选择服务商与模型。" in html
              and "选择服务商与最新模型" not in html)
        check("⑧ 设置页删除模型路由块", "suggestRouting" not in html)
        check("⑧ 设置页删除本地用量块", 'id="usageTip"' not in html)
        check("⑧ 设置页删除协商与复核开关", 'id="flagNegoParallel"' not in html)
        # 工作流引擎：移除面板与开关，内置于智能体自动调用
        check("⑧ 删除「启用工作流引擎」按钮", 'id="wfEnableChip"' not in html)
        check("⑧ 工作流引擎面板已整体删除", 'class="wf-embed"' not in html)
        # 内置技能完整性：源码 177 条，成品必须一条不少
        gj = getj("/api/skills/groups")
        counts = gj.get("counts") or {}
        check("⑧ 内置技能 177 条全部进了成品",
              counts.get("builtin", 0) == 177, counts)
        check("⑧ 技能分组 16 组齐全",
              len(gj.get("groups") or []) == 16, len(gj.get("groups") or []))
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            subprocess.run(["taskkill", "/F", "/IM", "NexEdu.exe"],
                           capture_output=True, timeout=30)
        except Exception:
            pass
        # 清理本次产生的教案（按完整标题精确匹配）
        try:
            sys.path.insert(0, ROOT)
            from nexedu import lessons
            titles = {"初中语文《背影》教学设计",
                      "初中语文《济南的冬天》单元备课方案",
                      "初中语文《春》教学设计"}
            for x in lessons.list_lessons():
                if x["title"] in titles:
                    lessons.delete_lesson(x["id"])
        except Exception:
            pass

    ok = sum(1 for _, c, _ in _RESULTS if c)
    bad = [n for n, c, _ in _RESULTS if not c]
    print("-" * 62)
    print("通过 %d/%d" % (ok, len(_RESULTS)))
    if bad:
        print("失败项：%s" % ", ".join(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
