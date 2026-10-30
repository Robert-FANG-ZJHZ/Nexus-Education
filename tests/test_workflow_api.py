# -*- coding: utf-8 -*-
"""工作流引擎接口冒烟测试：进程内启动服务，走真实 HTTP 路由。

不消耗 API Key（NEXEDU_FORCE_DEMO=1 演示模式），不依赖 shell。

用法：
  "C:/Users/FANG/.workbuddy/binaries/python/versions/3.13.12/python.exe" tests/test_workflow_api.py
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["NEXEDU_FORCE_DEMO"] = "1"     # 演示模式：不联网、不消耗额度
os.environ["NEXEDU_OPEN_BROWSER"] = "0"
os.environ["NEXEDU_RATE_MAX"] = "0"       # 关闭限流，避免测试被挡

from nexedu import config, lessons, server          # noqa: E402

PORT = 8799
BASE = "http://127.0.0.1:%d" % PORT
TOKEN = {"v": ""}
PAGE = {"v": ""}        # 页面票据：先加载页面才能调任何接口

_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  → " + str(detail)) if detail and not cond else ""))


def _hdr(extra=None):
    h = {"X-NexEdu-Token": TOKEN["v"], "X-NexEdu-Page": PAGE["v"]}
    if extra:
        h.update(extra)
    return h


def get(path):
    req = urllib.request.Request(BASE + path, headers=_hdr())
    return json.loads(urllib.request.urlopen(req, timeout=90).read()
                      .decode("utf-8", "ignore"))


def post_json(path, payload):
    req = urllib.request.Request(
        BASE + path, headers=_hdr({"Content-Type": "application/json"}),
        data=json.dumps(payload).encode("utf-8"), method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=90).read()
                      .decode("utf-8", "ignore"))


def post_stream(path, payload):
    """POST 并逐行解析 NDJSON，返回事件列表。"""
    req = urllib.request.Request(
        BASE + path, headers=_hdr({"Content-Type": "application/json"}),
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


def status_code(path, payload=None, with_token=True):
    """返回 HTTP 状态码；出错时返回异常码。"""
    h = _hdr({"Content-Type": "application/json"}) if with_token else \
        {"Content-Type": "application/json"}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, headers=h, data=data, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30)
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def main():
    print("=" * 62)
    print("工作流引擎接口冒烟测试（演示模式）")
    print("=" * 62)

    srv, port = server.make_server("127.0.0.1", PORT)
    if port != PORT:
        print("端口被占用，改用 %d" % port)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(1.2)

    try:
        # 模拟真实客户端：先加载页面取回服务端注入的页面票据
        page = urllib.request.urlopen(BASE + "/", timeout=30)\
            .read().decode("utf-8", "ignore")
        m = re.search(r"__NEXEDU_PAGE__='([0-9a-f]+)'", page)
        PAGE["v"] = m.group(1) if m else ""
        TOKEN["v"] = get("/api/status")["token"]
        check("⓪ 服务启动并取到令牌", bool(TOKEN["v"]))

        # ---- 1. 工作流列表 ----
        j = get("/api/workflows")
        ws = j.get("workflows", [])
        check("① /api/workflows 返回 3 条", len(ws) == 3, len(ws))
        check("① 含 w1-lesson", any(w["id"] == "w1-lesson" for w in ws))
        w1 = [w for w in ws if w["id"] == "w1-lesson"][0]
        check("① w1 节点含专家与标题",
              len(w1["nodes"]) == 5 and w1["nodes"][0]["expert"] == "nexaim",
              w1["nodes"][:2])

        # ---- 2. 全自动运行（一键备课走的同一条路） ----
        evs = post_stream("/api/workflow/run", {
            "workflow": "w1-lesson", "subject": "语文", "grade": "初中",
            "topic": "背影", "student_info": "基础薄弱", "auto": True})
        st = [e for e in evs if e.get("type") == "workflow_start"]
        check("② 全自动：收到 workflow_start", bool(st))
        check("② 全自动：auto=True", st and st[0].get("auto") is True)
        ns = [e for e in evs if e.get("type") == "node_start"]
        check("② 全自动：4 个产出节点", len(ns) == 4, len(ns))
        cp = [e for e in evs if e.get("type") == "checkpoint"]
        check("② 全自动：检查点自动放行",
              len(cp) == 1 and cp[0].get("auto") is True, cp)
        check("② 全自动：无 paused", not [e for e in evs if e.get("type") == "paused"])
        arts = [e for e in evs if e.get("type") == "artifact"]
        check("② 全自动：导出 docx+pptx",
              sorted(a["kind"] for a in arts) == ["docx", "pptx"],
              [a["kind"] for a in arts])
        done = [e for e in evs if e.get("type") == "done"]
        check("② 全自动：done 且 status=done",
              bool(done) and done[-1].get("status") == "done", done[-1:])
        run_id = (st[0].get("run_id") if st else None)
        check("② 返回 run_id", bool(run_id), run_id)

        # ---- 3. 按 id 查询运行 ----
        d = get("/api/workflow/run?id=" + urllib.parse.quote(run_id))
        check("③ 查询运行：status=done", d.get("status") == "done", d.get("status"))
        check("③ 查询运行：进度 5/5", d.get("current") == 5, d.get("current"))
        check("③ 查询运行：工件落盘",
              d.get("artifacts") and all(
                  os.path.exists(a["path"]) for a in d["artifacts"]),
              d.get("artifacts"))

        # ---- 4. 交互模式：检查点暂停 ----
        evs2 = post_stream("/api/workflow/run", {
            "workflow": "w2-unit", "subject": "语文", "grade": "初中",
            "topic": "济南的冬天", "auto": False})
        pz = [e for e in evs2 if e.get("type") == "paused"]
        check("④ 交互模式：暂停并给出 run_id",
              bool(pz) and bool(pz[0].get("run_id")), pz[:1])
        check("④ 交互模式：暂停时不导出工件",
              not [e for e in evs2 if e.get("type") == "artifact"])
        rid2 = pz[0]["run_id"] if pz else None
        d2 = get("/api/workflow/run?id=" + urllib.parse.quote(rid2))
        check("④ 交互模式：状态 waiting", d2.get("status") == "waiting",
              d2.get("status"))

        # ---- 5. 续跑 ----
        evs3 = post_stream("/api/workflow/resume",
                           {"run_id": rid2, "decision": "approve"})
        done3 = [e for e in evs3 if e.get("type") == "done"]
        check("⑤ 续跑：完成", bool(done3) and done3[-1].get("status") == "done",
              done3[-1:])
        d3 = get("/api/workflow/run?id=" + urllib.parse.quote(rid2))
        check("⑤ 续跑：状态 done", d3.get("status") == "done", d3.get("status"))
        check("⑤ 续跑：补齐工件", len(d3.get("artifacts", [])) >= 1,
              d3.get("artifacts"))

        # ---- 6. 历史运行列表 ----
        runs = get("/api/workflow/runs").get("runs", [])
        check("⑥ 历史运行含本次两条",
              {run_id, rid2} <= {r["run_id"] for r in runs},
              [r["run_id"] for r in runs][:4])

        # ---- 7. 一键备课原接口仍可用（向后兼容）----
        evs4 = post_stream("/api/lesson", {
            "subject": "语文", "grade": "初中", "topic": "春",
            "student_info": "", "skills": []})
        check("⑦ /api/lesson 仍可用（section+done）",
              bool([e for e in evs4 if e.get("type") == "section"])
              and bool([e for e in evs4 if e.get("type") == "done"]),
              [e.get("type") for e in evs4][:6])

        # ---- 8. 校验与鉴权 ----
        check("⑧ 缺课题返回 400",
              status_code("/api/workflow/run",
                          {"workflow": "w1-lesson", "topic": ""}) == 400)
        check("⑧ 未知工作流返回 400",
              status_code("/api/workflow/run",
                          {"workflow": "nope", "topic": "背影"}) == 400)
        check("⑧ 无令牌被拒绝",
              status_code("/api/workflow/run",
                          {"workflow": "w1-lesson", "topic": "背影"},
                          with_token=False) in (401, 403),
              status_code("/api/workflow/run",
                          {"workflow": "w1-lesson", "topic": "背影"},
                          with_token=False))

    finally:
        srv.shutdown()
        srv.server_close()
        # 清理测试教案（按完整标题精确匹配）
        try:
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
