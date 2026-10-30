"""并行任务管理器（Job Manager）。

让多个备课/智能体任务**真正同时跑**：每个任务在独立线程里执行，主线程与
HTTP 线程不被阻塞；前端通过轮询增量拉取事件，可同时盯多个任务的进度。

设计要点
--------
- **任务即事件流**：每个任务维护一条有序事件序列（沿用 NDJSON 事件协议），
  前端用 ``since=<序号>`` 增量拉取，天然支持「中途加入观看」。
- **可取消**：任务持有一个 ``threading.Event``；引擎在**节点边界**检查它，
  不把某个节点砍成半截内容。
- **失败隔离**：单个任务抛异常只影响该任务，其余任务继续。
- **有界**：任务数与单任务事件数都有上限，长时间挂机不会撑爆内存。

本模块零第三方依赖，可独立单元测试。
"""

import threading
import time
import uuid

MAX_JOBS = 100            # 保留的最近任务条数（更久的自动淘汰）
MAX_EVENTS = 5000         # 单任务事件上限（超出后只更新计数，不再存全文）
MAX_PARALLEL = 6          # 同时运行的任务数上限（超出排队）

_UID = [0]


def _next_id():
    _UID[0] += 1
    return "%s-%d" % (time.strftime("%H%M%S"), _UID[0])


class Job:
    """一个后台任务。"""

    def __init__(self, job_id, kind, title, meta=None):
        self.id = job_id
        self.kind = kind                # workflow / agent / lesson / chat
        self.title = title or ""
        self.meta = meta or {}
        self.events = []
        self.lock = threading.RLock()
        self.cancel_flag = threading.Event()
        self.thread = None
        self.status = "queued"          # queued/running/done/failed/cancelled
        self.started_at = time.time()
        self.ended_at = None
        self.error = ""
        self.result = {}
        self.progress = {"current": 0, "total": 0}
        self.dropped = 0                # 因超出上限被丢弃的事件数

    # ---- 事件 ----
    def emit(self, ev):
        if not isinstance(ev, dict):
            return
        with self.lock:
            ev = dict(ev)
            ev["seq"] = len(self.events) + self.dropped
            if len(self.events) < MAX_EVENTS:
                self.events.append(ev)
            else:
                self.dropped += 1
        self._track(ev)

    def _track(self, ev):
        """从事件流里提炼进度与终态（避免额外查询引擎状态）。"""
        t = ev.get("type")
        if t == "workflow_start":
            self.progress["total"] = len(ev.get("nodes") or [])
        elif t == "node_start":
            self.progress["current"] = max(self.progress["current"],
                                           int(ev.get("index") or 0) + 1)
            self.meta["node"] = ev.get("title") or ""
        elif t == "node_done":
            self.meta["node_ms"] = (self.meta.get("node_ms") or {})
            self.meta["node_ms"][ev.get("id") or ev.get("node") or ""] = \
                ev.get("ms") or 0
        elif t == "done":
            self.status = ev.get("status") or "done"
            self.result = {k: v for k, v in ev.items() if k != "type"}
        elif t == "error":
            self.error = ev.get("message") or ""

    # ---- 查询 ----
    def events_since(self, since=0):
        with self.lock:
            return list(self.events[since:])

    @property
    def seq(self):
        with self.lock:
            return len(self.events) + self.dropped

    def to_dict(self):
        with self.lock:
            running = self.status in ("queued", "running")
            return {
                "id": self.id,
                "kind": self.kind,
                "title": self.title,
                "status": self.status,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "ms": int(((self.ended_at or time.time()) - self.started_at) * 1000),
                "seq": self.seq,
                "progress": dict(self.progress),
                "error": self.error,
                "result": self.result,
                "parallel": running,
                "meta": {k: v for k, v in self.meta.items() if k != "node_ms"},
            }

    def cancel(self):
        self.cancel_flag.set()
        if self.status in ("queued", "running"):
            self.status = "cancelled"
        return True

    @property
    def should_stop(self):
        return self.cancel_flag.is_set


class JobManager:
    """任务池：提交、查询、取消。"""

    def __init__(self):
        self._jobs = {}
        self._order = []
        self._lock = threading.RLock()
        self._running = 0

    # ---- 提交 ----
    def submit(self, kind, title, factory, meta=None):
        """提交一个后台任务。

        factory: ``callable(should_stop) -> iterable[event dict]``
        返回任务对象（已启动线程）。
        """
        jid = _next_id()
        job = Job(jid, kind, title, meta)
        with self._lock:
            self._jobs[jid] = job
            self._order.append(jid)
            self._trim()
        job.thread = threading.Thread(target=self._run, args=(job, factory),
                                      daemon=True, name="job-" + jid)
        job.thread.start()
        return job

    # ---- 执行体 ----
    def _run(self, job, factory):
        # 并发闸口：超过上限先排队，避免一次性开十几个任务把模型额度打满
        waited = 0
        while self._running >= MAX_PARALLEL and waited < 120:
            time.sleep(0.5)
            waited += 0.5
            if job.cancel_flag.is_set():
                job.status = "cancelled"
                job.ended_at = time.time()
                return
        with self._lock:
            self._running += 1
        job.status = "running"
        try:
            for ev in factory(job.should_stop):
                if job.cancel_flag.is_set():
                    break
                job.emit(ev)
            if job.status == "running":     # 生成器没给终态就收尾为 done
                job.status = "done"
        except Exception as e:              # 单任务失败不影响其它任务
            job.status = "failed"
            job.error = str(e)
            job.emit({"type": "error", "message": "任务执行失败：%s" % e})
        finally:
            job.ended_at = time.time()
            if job.status not in ("done", "failed", "cancelled"):
                job.status = "done"
            with self._lock:
                self._running -= 1

    # ---- 查询 ----
    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit=40):
        with self._lock:
            jobs = [self._jobs[i] for i in reversed(self._order)
                    if i in self._jobs]
        return [j.to_dict() for j in jobs[:limit]]

    def active(self):
        with self._lock:
            return [j.to_dict() for j in
                    (self._jobs[i] for i in reversed(self._order)
                     if i in self._jobs)
                    if j.status in ("queued", "running")]

    def cancel(self, job_id):
        job = self.get(job_id)
        if job is None:
            return False
        job.cancel()
        return True

    def _trim(self):
        """淘汰最旧的任务，只保留最近 MAX_JOBS 条（已完成的优先淘汰）。"""
        while len(self._order) > MAX_JOBS:
            old = self._order.pop(0)
            j = self._jobs.get(old)
            if j is not None and j.status in ("queued", "running"):
                self._order.insert(0, old)      # 还在跑的不能被淘汰
                break
            self._jobs.pop(old, None)


MANAGER = JobManager()


# ---------------------------------------------------------------------------
# 便捷工厂：把常见入口包装成可并行任务
# ---------------------------------------------------------------------------
def make_workflow_job(wf_id, subject, grade, topic, student_info="",
                      extra="", skills=None, auto=True):
    """构造一个工作流任务的 factory（延迟 import，避免与 server 形成循环）。"""
    from . import workflow

    def factory(should_stop):
        run = workflow.create_run(wf_id, subject, grade, topic,
                                  student_info=student_info, extra=extra,
                                  skills=skills or [], auto=auto)

        def gen():
            yield {"type": "run", "run_id": run.run_id}
            for ev in workflow.run_workflow(run.run_id, should_stop=should_stop):
                yield ev
            try:
                workflow.note_memory(run)
            except Exception:
                pass

        return gen()

    return factory


def make_agent_job(message, expert="auto", extra="", skills=None,
                   history=None):
    """构造一个智能体任务的 factory（规划 → 逐步执行）。"""
    def factory(should_stop):
        from . import agents, planner, workflow

        def gen():
            plan = planner.plan(message, expert, "")
            yield {"type": "plan", "plan": {
                "strategy": plan["strategy"],
                "summary": plan["summary"],
                "steps": [{"title": s["title"], "expert": s["expert"]}
                          for s in plan["steps"]],
            }}
            outputs = []
            for idx, step in enumerate(plan["steps"], 1):
                if should_stop and should_stop():
                    yield {"type": "done", "status": "cancelled"}
                    return
                t0 = time.time()
                yield {"type": "node_start", "index": idx - 1,
                       "id": "step%d" % idx, "title": step["title"],
                       "expert": step["expert"]}
                goal = step.get("goal") or message
                if outputs:
                    prev = "\n\n".join(
                        "【前序：%s】\n%s" % (t, agents._clip(o, 1200))
                        for t, o in outputs[-2:])
                    goal = goal + "\n\n" + prev
                acc = []
                for ev in agents.run_agent(step["expert"], goal,
                                           history=history, extra=extra,
                                           tools=step.get("tools") or [],
                                           critique=bool(step.get("critique"))):
                    if ev.get("type") == "delta":
                        acc.append(ev["text"])
                        yield ev
                    elif ev.get("type") in workflow.THINK_TYPES:
                        out = dict(ev)
                        out["index"] = idx - 1
                        out["label"] = workflow.THINK_LABEL.get(ev["type"], "思考")
                        yield out
                outputs.append((step["title"], "".join(acc)))
                yield {"type": "node_done", "index": idx - 1,
                       "id": "step%d" % idx, "status": "ok",
                       "ms": int((time.time() - t0) * 1000)}
            yield {"type": "done", "status": "done"}

        return gen()

    return factory
