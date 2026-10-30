# -*- coding: utf-8 -*-
"""并行能力接口冒烟测试：进程内启动服务，走真实 HTTP 路由。

覆盖：/api/jobs/*（并行任务）、/api/chat 自动路由、/api/mcp、/api/triggers、
/api/hook/<name>（回调触发）、/api/package*（技能包）与鉴权。

不消耗 API Key（NEXEDU_FORCE_DEMO=1 演示模式）。

用法：
  "C:/Users/FANG/.workbuddy/binaries/python/versions/3.13.12/python.exe" tests/test_parallel_api.py
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

os.environ["NEXEDU_FORCE_DEMO"] = "1"
os.environ["NEXEDU_OPEN_BROWSER"] = "0"
os.environ["NEXEDU_RATE_MAX"] = "0"
os.environ["NEXEDU_MCP_AUTOSTART"] = "0"      # 测试不连外部 MCP 服务

from nexedu import server                     # noqa: E402

PORT = 8797
BASE = "http://127.0.0.1:%d" % PORT
TOKEN = {"v": ""}
PAGE = {"v": ""}        # 页面票据：先加载页面才能调任何接口

_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond)))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  → " + str(detail)) if detail and not cond else ""))


def _hdr(extra=None, with_token=True):
    h = {"X-NexEdu-Token": TOKEN["v"],
         "X-NexEdu-Page": PAGE["v"]} if with_token else {}
    if extra:
        h.update(extra)
    return h


def get(path, with_token=True):
    req = urllib.request.Request(BASE + path, headers=_hdr(with_token=with_token))
    return json.loads(urllib.request.urlopen(req, timeout=90).read()
                      .decode("utf-8", "ignore"))


def post_json(path, payload, with_token=True):
    h = _hdr({"Content-Type": "application/json"}, with_token)
    req = urllib.request.Request(BASE + path, headers=h,
                                 data=json.dumps(payload).encode("utf-8"),
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=90).read()
                      .decode("utf-8", "ignore"))


def post_stream(path, payload):
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


def code_of(fn):
    try:
        fn()
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def wait_job(jid, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        ev = get("/api/jobs/events?id=%s&since=0" % jid)
        if ev.get("status") in ("done", "failed", "cancelled"):
            return ev
        time.sleep(0.4)
    return get("/api/jobs/events?id=%s&since=0" % jid)


def main():
    print("=" * 62)
    print("并行能力接口冒烟测试（演示模式）")
    print("=" * 62)

    srv, port = server.make_server("127.0.0.1", PORT)
    if port != PORT:
        print("端口被占用，改用 %d" % port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1.2)

    try:
        # 模拟真实客户端：先加载页面取回服务端注入的页面票据
        page = urllib.request.urlopen(BASE + "/", timeout=30)\
            .read().decode("utf-8", "ignore")
        m = re.search(r"__NEXEDU_PAGE__='([0-9a-f]+)'", page)
        PAGE["v"] = m.group(1) if m else ""
        TOKEN["v"] = get("/api/status")["token"]
        check("⓪ 服务启动并取到令牌", bool(TOKEN["v"]))

        # ---- ① 并行任务：一次开两个 ----
        r1 = post_json("/api/jobs/run", {
            "kind": "workflow", "workflow": "w1-lesson", "subject": "语文",
            "grade": "初中", "topic": "《背影》并行甲", "auto": True})
        r2 = post_json("/api/jobs/run", {
            "kind": "workflow", "workflow": "w2-unit", "subject": "数学",
            "grade": "高中", "topic": "《函数》并行乙", "auto": True})
        check("① 提交任务甲成功", r1.get("ok") is True and r1["job"]["id"], r1)
        check("① 提交任务乙成功", r2.get("ok") is True and r2["job"]["id"], r2)
        j1, j2 = r1["job"]["id"], r2["job"]["id"]
        check("① 两个任务 id 不同", j1 != j2)

        lst = get("/api/jobs")
        ids = [x["id"] for x in lst["jobs"]]
        check("① 任务列表含两个任务", j1 in ids and j2 in ids, ids[:4])
        check("① running 列表非空", len(lst["running"]) >= 1, len(lst["running"]))

        e1 = wait_job(j1)
        e2 = wait_job(j2)
        check("① 任务甲跑完", e1["status"] == "done", e1["status"])
        if e2["status"] != "done":
            print("   任务乙异常事件：", [x for x in e2["events"]
                                          if x.get("type") == "error"][:3])
        check("① 任务乙跑完", e2["status"] == "done", e2["status"])
        evs1 = e1["events"]
        check("① 任务事件含 workflow_start",
              any(x["type"] == "workflow_start" for x in evs1))
        check("① 任务事件含思考事件 think",
              any(x["type"] == "think" for x in evs1))
        check("① 任务事件含节点耗时",
              any(x["type"] == "node_done" and x.get("ms") is not None
                  for x in evs1))
        check("① 任务事件导出工件",
              any(x["type"] == "artifact" for x in evs1))
        check("① 事件带自增序号",
              all("seq" in x for x in evs1))
        # 增量拉取
        part = get("/api/jobs/events?id=%s&since=%d" % (j1, len(evs1) - 2))
        check("① 增量拉取只返回尾部", len(part["events"]) == 2, len(part["events"]))

        # ---- ② 取消任务 ----
        r3 = post_json("/api/jobs/run", {
            "kind": "workflow", "workflow": "w1-lesson", "subject": "语文",
            "grade": "初中", "topic": "《取消测试》", "auto": True})
        j3 = r3["job"]["id"]
        time.sleep(0.3)
        c = post_json("/api/jobs/cancel", {"id": j3})
        check("② 取消请求被接受", c.get("ok") is True, c)
        e3 = wait_job(j3, timeout=60)
        check("② 任务确已取消", e3["status"] == "cancelled", e3["status"])
        bad = code_of(lambda: post_json("/api/jobs/cancel", {"id": "nope"}))
        check("② 取消不存在的任务返回 404", bad == 404, bad)

        # ---- ③ 快速对话自动路由 ----
        evs = post_stream("/api/chat", {"expert": "auto",
                                        "message": "帮我设计《背影》的教学目标"})
        rt = [x for x in evs if x["type"] == "routed"]
        check("③ 自动路由返回 routed 事件", len(rt) == 1, [x["type"] for x in evs][:6])
        check("③ 路由到目标专家", rt and rt[0].get("expert") == "nexaim",
              rt[0] if rt else None)
        check("③ 路由标记了自动", rt and rt[0].get("auto") is True)
        check("③ 输出仍正常结束",
              any(x["type"] == "done" for x in evs))
        dn = [x for x in evs if x["type"] == "done"]
        check("③ done 带耗时", dn and dn[0].get("ms") is not None)

        # 智能体模式：节点级思考事件
        evs = post_stream("/api/agent", {"message": "为《背影》做一次完整备课"})
        check("③ 智能体模式发出 plan", any(x["type"] == "plan" for x in evs))
        check("③ 智能体模式发出 node_start",
              any(x["type"] == "node_start" for x in evs))
        check("③ 智能体模式发出 node_done 且带耗时",
              any(x["type"] == "node_done" and x.get("ms") is not None
                  for x in evs))

        # ---- ④ MCP ----
        st = get("/api/mcp")
        check("④ MCP 状态可查", isinstance(st.get("servers"), list))
        check("④ 未配置时工具数为 0", st.get("tool_count") == 0, st.get("tool_count"))
        saved = post_json("/api/mcp", {"servers": []})
        check("④ 保存空配置不报错", isinstance(saved, dict))

        # ---- ⑤ 触发器 ----
        # 先清掉历史遗留的测试触发器（保证名称唯一、token 匹配）
        for old in get("/api/triggers")["triggers"]:
            if old["name"].startswith("API-"):
                post_json("/api/triggers", {"action": "delete", "id": old["id"]})
        t = post_json("/api/triggers", {
            "name": "API-测试触发", "kind": "webhook", "workflow": "w1-lesson",
            "subject": "语文", "grade": "初中", "topic": "《春》"})
        check("⑤ 创建触发器成功", t.get("ok") is True and t["trigger"]["token"],
              t)
        tg = t["trigger"]
        stt = get("/api/triggers")
        check("⑤ 触发器列表含新建项",
              any(x["id"] == tg["id"] for x in stt["triggers"]))
        check("⑤ 调度线程已启动", stt.get("running") is True)

        # 回调触发（用触发器自带 token 鉴权，不带本地令牌）
        hk = post_json("/api/hook/" + urllib.parse.quote(tg["name"])
                       + "?token=" + tg["token"],
                       {"topic": "《春》", "error_rate": 0.35},
                       with_token=False)
        check("⑤ 回调触发成功", hk.get("ok") is True, hk)
        if hk.get("ok"):
            e = wait_job(hk["job_id"], timeout=120)
            check("⑤ 回调任务跑完", e["status"] == "done", e["status"])
        no_auth = code_of(lambda: post_json(
            "/api/hook/" + urllib.parse.quote(tg["name"]), {"a": 1},
            with_token=False))
        check("⑤ 无 token 回调被拒", no_auth == 403, no_auth)

        fr = post_json("/api/triggers/fire", {"id": tg["id"]})
        check("⑤ 手动触发成功", fr.get("ok") is True, fr)
        # 定时触发判定
        tt = post_json("/api/triggers", {
            "name": "API-定时触发", "kind": "schedule", "workflow": "w1-lesson",
            "subject": "语文", "grade": "初中", "topic": "《济南的冬天》",
            "schedule": {"hour": 23, "minute": 59, "weekdays": [0]}})
        check("⑤ 定时触发器已创建",
              tt["trigger"]["schedule"]["hour"] == 23, tt["trigger"]["schedule"])
        # 清理
        post_json("/api/triggers", {"action": "delete", "id": tg["id"]})
        post_json("/api/triggers", {"action": "delete", "id": tt["trigger"]["id"]})
        after = get("/api/triggers")
        check("⑤ 触发器已清理",
              not any(x["id"] in (tg["id"], tt["trigger"]["id"])
                      for x in after["triggers"]))

        # ---- ⑥ 技能包 ----
        pkg = get("/api/package")
        check("⑥ 导出包格式正确", pkg.get("format") == "nexedu-package")
        check("⑥ 包内含专家目录", len(pkg.get("experts") or []) >= 4)
        imp = post_json("/api/package/import", {
            "package": {"format": "nexedu-package",
                        "skills": [{"name": "接口测试技能", "prompt": "p"}]}})
        check("⑥ 导入技能成功", imp.get("skills_added") == 1, imp)
        from nexedu import skills as skmod
        for s in skmod.list_skills():
            if s["name"] == "接口测试技能":
                skmod.delete_skill(s["id"])

        # ---- ⑦ 鉴权 ----
        for p in ("/api/jobs", "/api/mcp", "/api/triggers", "/api/package"):
            c = code_of(lambda p=p: get(p, with_token=False))
            check("⑦ 无令牌访问 %s 被拒" % p, c == 403, c)

        print("-" * 62)
        ok_n = sum(1 for _, c in _RESULTS if c)
        print("通过 %d/%d" % (ok_n, len(_RESULTS)))
        return 0 if ok_n == len(_RESULTS) else 1
    finally:
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
