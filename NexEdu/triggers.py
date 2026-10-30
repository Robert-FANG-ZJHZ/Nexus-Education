"""触发器（Triggers）：让工作流不再只靠人点按钮。

对应蓝图中的「触发」节点，补齐两类外部引擎常见的入口：

1. **定时触发（schedule）**——每周一 07:30 自动生成下周教案初稿这类固定节奏；
   后台守护线程按分钟巡检，到期就提交一个后台任务（走并行任务管理器）。
2. **回调触发（webhook）**——外部系统（成绩系统、作业平台、教研平台）把数据
   POST 进来，自动触发对应工作流，并**把回调数据回流进备课上下文**
   （例如把错题分布喂给「错因补偿」工作流）。

设计原则
--------
- **本地优先**：调度配置与触发记录都落在 workspace 沙盒内，不依赖云端。
- **幂等不重放**：同一分钟同一触发器只触发一次（用 ``last_fire_key`` 记录）。
- **失败可见**：每次触发都生成一个后台任务，状态、耗时、事件全部可查。
- **不阻塞**：调度线程只负责提交任务，真正的生成在任务线程里跑。
"""

import json
import os
import threading
import time
import uuid

from . import config, jobs

TRIGGERS_PATH = os.path.join(config.WORKSPACE_DIR, "triggers.json")

TICK_SECONDS = 20           # 调度巡检间隔
MAX_RUNS = 200              # 触发历史保留条数


def _now_key():
    return time.strftime("%Y-%m-%d %H:%M")


class Store:
    """触发器配置 + 触发历史（单文件持久化，原子写）。"""

    def __init__(self):
        self.triggers = []
        self.runs = []
        self._lock = threading.RLock()
        self.load()

    # ---- 持久化 ----
    def load(self):
        if not os.path.exists(TRIGGERS_PATH):
            return
        try:
            with open(TRIGGERS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        self.triggers = list(d.get("triggers") or [])
        self.runs = list(d.get("runs") or [])[-MAX_RUNS:]

    def save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(TRIGGERS_PATH), exist_ok=True)
                tmp = TRIGGERS_PATH + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"triggers": self.triggers,
                               "runs": self.runs[-MAX_RUNS:]},
                              f, ensure_ascii=False, indent=1)
                os.replace(tmp, TRIGGERS_PATH)
            except OSError:
                pass

    # ---- 增删改查 ----
    def create(self, data):
        with self._lock:
            t = normalize(data)
            # 名称即回调地址的标识，必须唯一：重名自动加序号，
            # 否则 by_name 会命中旧记录（token 不一致，回调永远 403）
            base = t["name"]
            if any(x.get("name") == base for x in self.triggers):
                i = 2
                while any(x.get("name") == "%s-%d" % (base, i)
                          for x in self.triggers):
                    i += 1
                t["name"] = "%s-%d" % (base, i)
            self.triggers.append(t)
            self.save()
        return t

    def update(self, tid, patch):
        with self._lock:
            for t in self.triggers:
                if t["id"] == tid:
                    t.update(normalize(dict(t, **patch), keep_id=t["id"]))
                    self.save()
                    return t
        return None

    def delete(self, tid):
        with self._lock:
            before = len(self.triggers)
            self.triggers = [t for t in self.triggers if t["id"] != tid]
            self.save()
            return len(self.triggers) < before

    def get(self, tid):
        with self._lock:
            for t in self.triggers:
                if t["id"] == tid:
                    return t
        return None

    def by_name(self, name):
        """按名称查找；同名取**最新**的一条（名称已做唯一化，这里再兜一层）。"""
        with self._lock:
            hit = None
            for t in self.triggers:
                if t.get("name") == name:
                    hit = t
        return hit

    def list(self):
        with self._lock:
            return [dict(t) for t in self.triggers]

    def log_run(self, rec):
        with self._lock:
            self.runs.append(rec)
            self.runs = self.runs[-MAX_RUNS:]
            self.save()


def normalize(data, keep_id=None):
    """规整并校验一条触发器配置。"""
    d = data or {}
    kind = (d.get("kind") or "schedule").strip().lower()
    if kind not in ("schedule", "webhook"):
        kind = "schedule"
    sch = d.get("schedule") or {}
    if not isinstance(sch, dict):
        sch = {}
    weekdays = sch.get("weekdays")
    if not isinstance(weekdays, list):
        weekdays = [0, 1, 2, 3, 4, 5, 6]
    weekdays = [int(w) for w in weekdays if str(w).isdigit()
                and 0 <= int(w) <= 6] or [0, 1, 2, 3, 4, 5, 6]
    return {
        "id": keep_id or (d.get("id") or uuid.uuid4().hex[:10]),
        "name": (d.get("name") or "").strip() or ("触发-" + uuid.uuid4().hex[:4]),
        "kind": kind,
        "enabled": bool(d.get("enabled", True)),
        "workflow": (d.get("workflow") or "w1-lesson").strip(),
        "subject": (d.get("subject") or "语文").strip(),
        "grade": (d.get("grade") or "").strip(),
        "topic": (d.get("topic") or "").strip(),
        "student_info": (d.get("student_info") or "").strip(),
        "auto": bool(d.get("auto", True)),
        "schedule": {
            "hour": max(0, min(23, int(sch.get("hour", 7) or 0))),
            "minute": max(0, min(59, int(sch.get("minute", 30) or 0))),
            "weekdays": weekdays,
        },
        "token": (d.get("token") or uuid.uuid4().hex[:12]),
        "last_fire_key": d.get("last_fire_key", ""),
        "last_job": d.get("last_job", ""),
        "note": (d.get("note") or "").strip(),
    }


STORE = Store()


# ---------------------------------------------------------------------------
# 触发执行
# ---------------------------------------------------------------------------
def fire(trigger, payload=None, reason="manual"):
    """按触发器配置提交一个后台任务（webhook 数据会回流进备课上下文）。"""
    extra = ""
    topic = trigger.get("topic") or ""
    student_info = trigger.get("student_info") or ""

    if payload:
        # 回调数据回流：把外部系统给的字段整理成一段人类可读的补充学情，
        # 只写入沙盒内存与本次任务上下文，不做任何外发。
        lines = []
        for k in ("topic", "title", "subject", "grade"):
            v = payload.get(k)
            if isinstance(v, str) and v.strip() and not topic:
                if k == "topic":
                    topic = v.strip()
        for k, v in (payload or {}).items():
            if k in ("topic", "title", "subject", "grade", "token"):
                continue
            if isinstance(v, (str, int, float)):
                lines.append("- %s：%s" % (k, str(v)[:200]))
            elif isinstance(v, (list, dict)):
                try:
                    txt = json.dumps(v, ensure_ascii=False)[:600]
                except (TypeError, ValueError):
                    txt = str(v)[:200]
                lines.append("- %s：%s" % (k, txt))
        if lines:
            extra = "【外部系统回调数据（本次备课请重点参考）】\n" + "\n".join(lines[:20])
            student_info = (student_info + "\n" + extra).strip() if student_info else extra

    if not topic:
        return None, "触发器未配置课题，无法执行"

    factory = jobs.make_workflow_job(
        trigger.get("workflow") or "w1-lesson",
        trigger.get("subject") or "语文",
        trigger.get("grade") or "",
        topic,
        student_info=student_info,
        extra=extra,
        auto=bool(trigger.get("auto", True)))
    job = jobs.MANAGER.submit(
        "workflow",
        "[%s] %s" % (trigger.get("name") or trigger["id"], topic[:24]),
        factory,
        meta={"trigger": trigger["id"], "trigger_name": trigger.get("name"),
              "reason": reason, "workflow": trigger.get("workflow")})

    STORE.log_run({"at": time.time(), "trigger": trigger["id"],
                   "name": trigger.get("name"), "job": job.id,
                   "reason": reason, "topic": topic})
    trigger["last_job"] = job.id
    trigger["last_fire_key"] = _now_key()
    STORE.save()
    return job, ""


def fire_by_name(name, payload=None, reason="webhook"):
    t = STORE.by_name(name)
    if t is None:
        return None, "未找到名为「%s」的触发器" % name
    if not t.get("enabled"):
        return None, "触发器「%s」已停用" % name
    return fire(t, payload=payload, reason=reason)


# ---------------------------------------------------------------------------
# 定时调度：后台守护线程
# ---------------------------------------------------------------------------
_sched = {"thread": None, "stop": threading.Event(), "started": False}


def _due(t, now):
    """判断某条定时触发器是否该在此刻触发。"""
    if not t.get("enabled") or t.get("kind") != "schedule":
        return False
    sch = t.get("schedule") or {}
    if now.tm_hour != int(sch.get("hour", 7)):
        return False
    if now.tm_min != int(sch.get("minute", 30)):
        return False
    if now.tm_wday not in (sch.get("weekdays") or [0, 1, 2, 3, 4, 5, 6]):
        return False
    if t.get("last_fire_key") == _now_key():     # 同一分钟不重复触发
        return False
    return True


def _loop():
    while not _sched["stop"].is_set():
        try:
            now = time.localtime()
            for t in STORE.list():
                if _due(t, now):
                    try:
                        fire(t, reason="schedule")
                    except Exception as e:
                        STORE.log_run({"at": time.time(),
                                       "trigger": t["id"], "job": "",
                                       "reason": "schedule",
                                       "error": str(e)})
        except Exception:
            pass
        _sched["stop"].wait(TICK_SECONDS)


def start_scheduler():
    if _sched["started"]:
        return True
    _sched["stop"].clear()
    _sched["thread"] = threading.Thread(target=_loop, daemon=True,
                                        name="nexedu-scheduler")
    _sched["thread"].start()
    _sched["started"] = True
    return True


def stop_scheduler():
    _sched["stop"].set()
    _sched["started"] = False


def status():
    return {
        "running": bool(_sched["started"]),
        "path": TRIGGERS_PATH,
        "count": len(STORE.list()),
        "triggers": STORE.list(),
        "runs": STORE.runs[-20:],
    }
