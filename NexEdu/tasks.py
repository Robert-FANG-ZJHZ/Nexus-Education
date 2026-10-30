"""长程任务队列（设计文档 Phase 2.4）。

定位：**按需触发 + 本地持久化**，不做常驻调度进程——常驻进程会带来资源占用与
隐私顾虑，而教师的真实需求（"每周一提醒备下周的课"）用"启动即检查 + 定时轮询"
就能满足。

能力：
- 一次性任务（指定日期时间）与周期任务（每天 / 每周某天的某个时刻）；
- 重启后未完成任务可恢复（running 状态在启动时重置为 pending，断点续跑）；
- 进度可见：执行过程中逐步写入 progress 与 steps，界面可查；
- 执行结果落库（教案 id / 提示），教师可回看。

所有数据都在 workspace/tasks.json，不联网、不依赖第三方调度库。
"""

import datetime
import json
import os
import threading
import time
import uuid

from . import config

_PATH = os.path.join(config.WORKSPACE_DIR, "tasks.json")
_LOCK = threading.Lock()
_SCHEDULER = {"thread": None, "stop": False}


# ---------------- 存储 ----------------

def _load():
    if os.path.exists(_PATH):
        try:
            with open(_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "tasks" in data:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"tasks": []}


def _save(data):
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PATH)          # 原子写，避免断电写坏


def _next_run(sched, now=None):
    """计算下次触发时间（本地时间）。sched: {type, at, weekday, date}。"""
    now = now or datetime.datetime.now()
    hh, mm = (sched.get("at") or "08:00").split(":")[:2]
    hh, mm = int(hh), int(mm)
    t = sched.get("type") or "once"
    if t == "once":
        d = sched.get("date")
        if d:
            y, m, dd = [int(x) for x in d.split("-")[:3]]
            return datetime.datetime(y, m, dd, hh, mm)
        return now
    if t == "daily":
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return nxt if nxt > now else nxt + datetime.timedelta(days=1)
    if t == "weekly":
        want = int(sched.get("weekday") or 0)     # 周一=0
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        delta = (want - now.weekday()) % 7
        nxt = nxt + datetime.timedelta(days=delta)
        return nxt if nxt > now else nxt + datetime.timedelta(days=7)
    return now


def add(title, kind="lesson", subject="", grade="", topic="", student_info="",
        sched=None, depends=None, plan=None, max_retry=2):
    """新增任务。kind: lesson（跑一键备课）/ remind（只提醒）/ step（子任务）。

    depends：依赖的任务标题列表——依赖未完成则本任务等待（子任务调度）。
    plan：子任务清单（来自 planner.decompose），用于长程推进的分解-衔接。
    """
    with _LOCK:
        data = _load()
        t = {
            "id": uuid.uuid4().hex[:10],
            "title": title or "未命名任务",
            "kind": kind,
            "subject": subject, "grade": grade, "topic": topic,
            "student_info": student_info,
            "sched": sched or {"type": "once", "at": "08:00"},
            "status": "pending",
            "progress": 0, "steps": [], "result": "",
            "depends": list(depends or []),
            "plan": plan or None,
            "retries": 0, "max_retry": int(max_retry or 0),
            "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "last_run": "", "next_run": "",
        }
        t["next_run"] = _next_run(t["sched"]).strftime("%Y-%m-%d %H:%M")
        data["tasks"].append(t)
        _save(data)
        return t


def list_tasks(status=None):
    data = _load()
    out = data["tasks"]
    if status:
        out = [t for t in out if t.get("status") == status]
    return sorted(out, key=lambda x: x.get("next_run") or "")


def get(tid):
    for t in _load()["tasks"]:
        if t.get("id") == tid:
            return t
    return None


def _update(tid, **kw):
    with _LOCK:
        data = _load()
        for t in data["tasks"]:
            if t.get("id") == tid:
                t.update(kw)
                _save(data)
                return t
    return None


def cancel(tid):
    return _update(tid, status="cancelled")


def remove(tid):
    with _LOCK:
        data = _load()
        data["tasks"] = [t for t in data["tasks"] if t.get("id") != tid]
        _save(data)
        return True


def resume_interrupted():
    """启动恢复：把上次执行中被打断的 running 重置为 pending（断点续跑）。"""
    n = 0
    with _LOCK:
        data = _load()
        for t in data["tasks"]:
            if t.get("status") == "running":
                t["status"] = "pending"
                t["progress"] = 0
                n += 1
        if n:
            _save(data)
    return n


# ---------------- 执行 ----------------

def run_task(tid, sink=None):
    """执行一个任务。kind=lesson 时跑一键备课；kind=remind 只记录提醒。

    sink: 可选回调，接收每个事件（供界面/测试观察进度）。
    """
    t = get(tid)
    if not t:
        return {"error": "任务不存在"}
    if t.get("status") == "cancelled":
        return {"error": "任务已取消"}

    _update(tid, status="running", progress=0, steps=[],
            last_run=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

    if t.get("kind") == "remind":
        _update(tid, status="done", progress=100,
                result=f"提醒：{t.get('title')}",
                next_run=_next_run(t.get("sched") or {}).strftime("%Y-%m-%d %H:%M"))
        return {"ok": True, "reminded": True}

    # lesson：跑四环闭环，逐步记录进度
    from . import agents
    steps, text_buf, total = [], [], 0
    try:
        for ev in agents.orchestrate_lesson(
                t.get("subject") or "", t.get("grade") or "",
                t.get("topic") or t.get("title") or "",
                student_info=t.get("student_info") or ""):
            if sink:
                sink(ev)
            et = ev.get("type")
            if et == "section":
                steps.append(ev.get("title") or "")
                total += 1
            elif et == "delta":
                text_buf.append(ev.get("text") or "")
            elif et == "artifact":
                steps.append("已生成 " + (ev.get("name") or "Word"))
            # 进度按已完成的环节数估算，上限 95%（落库后才算 100%）
            _update(tid, steps=steps, progress=min(95, total * 20))
    except Exception as e:
        # 失败重试（指数退避由调度线程的时间间隔自然实现）：长程任务最怕的是
        # 一次网络抖动就整条链断掉，这里给有限的自动重试，超限才标记失败。
        tries = int(t.get("retries") or 0)
        if tries < int(t.get("max_retry") or 0):
            _update(tid, status="pending", retries=tries + 1,
                    result=f"第 {tries + 1} 次执行失败（{e}），将自动重试")
            return {"error": str(e), "retry": tries + 1}
        _update(tid, status="failed", result=f"执行失败：{e}")
        return {"error": str(e)}

    _update(tid, status="done", progress=100,
            result=f"已完成 {len(steps)} 个环节，产出约 {sum(len(x) for x in text_buf)} 字",
            next_run=_next_run(t.get("sched") or {}).strftime("%Y-%m-%d %H:%M"))
    return {"ok": True, "steps": steps}


def _deps_ready(t, tasks):
    """依赖是否都已完成（子任务调度：先完成前序，再跑后继）。"""
    deps = t.get("depends") or []
    if not deps:
        return True
    done = {x.get("title") for x in tasks if x.get("status") == "done"}
    return all(d in done for d in deps)


def due_tasks():
    """到期待执行且依赖已满足的任务。"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    all_t = list_tasks()
    return [t for t in all_t
            if t.get("status") == "pending"
            and (t.get("next_run") or "") <= now
            and _deps_ready(t, all_t)]


def blocked_tasks():
    """已到期但依赖未完成的任务（界面可显示"等待前序"）。"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    all_t = list_tasks()
    return [t["id"] for t in all_t
            if t.get("status") == "pending"
            and (t.get("next_run") or "") <= now
            and not _deps_ready(t, all_t)]


# ---------------- 调度 ----------------

def tick():
    """检查并执行到期任务（由后台线程或启动时调用）。"""
    ran = []
    for t in due_tasks():
        sched_type = (t.get("sched") or {}).get("type")
        r = run_task(t["id"])
        ran.append({"id": t["id"], "title": t.get("title"), "result": r})
        # 一次性任务执行完即归档（不重复触发）
        if sched_type == "once" and not r.get("error"):
            _update(t["id"], status="done")
    return ran


def start_scheduler(interval=60):
    """启动后台轮询线程（进程内，非常驻系统服务）。"""
    if _SCHEDULER["thread"] and _SCHEDULER["thread"].is_alive():
        return False
    _SCHEDULER["stop"] = False

    def loop():
        while not _SCHEDULER["stop"]:
            try:
                tick()
            except Exception:
                pass                      # 调度异常不得影响主服务
            time.sleep(interval)

    th = threading.Thread(target=loop, daemon=True, name="nexedu-tasks")
    _SCHEDULER["thread"] = th
    th.start()
    return True


def stop_scheduler():
    _SCHEDULER["stop"] = True
