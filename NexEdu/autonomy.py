"""自主度分级（把"不做无约束自主"改为"教师可选的低约束自主"）。

立场没有变：**教师始终是最终裁定者**。变的是"每一步都要点确认"这件事——
它可以由教师自己放宽，而不是由我们替教师决定。

四档（默认 1 档）：
  0 谨慎：任何有副作用的动作都先问（写入 / 导出 / 记忆 / 联网）
  1 标准：**只读动作自动执行，写操作先确认**（默认）
  2 放手：沙盒内的写操作也自动执行，但**全程留痕、随时可撤销**
  3 委托：连"是否联网"也自动决定，仍受沙盒边界与审计约束

三条硬边界（任何档位都不可越过）：
  1. **不出沙盒**：写文件只能落在 workspace，禁止工作区之外的路径；
  2. **不静默**：每一次自动动作都要在界面留下记录（含撤销入口）；
  3. **可撤销**：ledger 记录可回滚的动作（导出/保存的文件可删除，记忆可撤回）。

注意：**不做无约束自主**。第 3 档仍然受上述三条约束，不存在"AI 自己决定教学内容"。
"""

import json
import os
import time

from . import config

_PATH = os.path.join(config.WORKSPACE_DIR, "autonomy.json")
_LEDGER_PATH = os.path.join(config.WORKSPACE_DIR, "autonomy_ledger.jsonl")

LEVELS = {
    0: "谨慎：所有写操作与联网都先询问",
    1: "标准：只读自动执行，写操作先确认（默认）",
    2: "放手：沙盒内写操作自动执行，全程留痕可撤销",
    3: "委托：联网也自动决定，仍受沙盒与审计约束",
}

DEFAULT_LEVEL = 1

# 各档位下无需确认的动作（其余一律需确认）
_AUTO = {
    0: set(),
    1: {"search_knowledge", "read_memory", "list_lessons", "get_lesson"},
    2: {"search_knowledge", "read_memory", "list_lessons", "get_lesson",
        "save_lesson", "export_docx", "export_pptx", "note_memory"},
    3: {"search_knowledge", "read_memory", "list_lessons", "get_lesson",
        "save_lesson", "export_docx", "export_pptx", "note_memory",
        "web_search"},
}

_state = {"level": DEFAULT_LEVEL, "kill": False}


def _load():
    if os.path.exists(_PATH):
        try:
            with open(_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except (OSError, json.JSONDecodeError):
            pass
    return {"level": DEFAULT_LEVEL, "kill": False}


def load():
    d = _load()
    _state["level"] = int(d.get("level", DEFAULT_LEVEL))
    _state["kill"] = bool(d.get("kill"))
    return dict(_state)


def save(level=None, kill=None):
    d = _load()
    if level is not None:
        d["level"] = max(0, min(3, int(level)))
    if kill is not None:
        d["kill"] = bool(kill)
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    _state.update(d)
    return dict(_state)


def level():
    if not _state:
        load()
    return int(_state.get("level", DEFAULT_LEVEL))


def kill_switch():
    return bool(_state.get("kill"))


def describe():
    lv = level()
    return {"level": lv, "desc": LEVELS.get(lv, ""), "kill": kill_switch(),
            "auto": sorted(_AUTO.get(lv, set())), "levels": LEVELS}


def can_execute(tool_name, side_effect=False):
    """判断一个动作能否自动执行。返回 (是否放行, 原因)。

    kill 开关打开时，除只读检索外一律不放行——这是给教师的紧急刹车。
    """
    if kill_switch() and tool_name != "search_knowledge":
        return False, "已按下紧急停止，所有自动动作暂停"
    if not side_effect:
        if tool_name in ("web_search",) and level() < 3:
            return False, "联网检索需教师确认（当前自主档位 %d）" % level()
        return True, ""
    if tool_name in _AUTO.get(level(), set()):
        return True, ""
    return False, "当前自主档位下该动作需教师确认（档位 %d）" % level()


# ---------------- 留痕与撤销 ----------------

def record(action, detail, rollback=None):
    """记录一次自动动作（含可回滚信息）。"""
    item = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action,
            "detail": detail, "rollback": rollback or {}, "level": level(),
            "undone": False}
    try:
        os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)
        with open(_LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return item


def ledger(limit=30):
    if not os.path.exists(_LEDGER_PATH):
        return []
    try:
        with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []
    return rows[-limit:]


def undo_last():
    """撤销最近一次未撤销的自动动作（仅限可安全回滚的类型）。"""
    rows = ledger(200)
    for r in reversed(rows):
        if r.get("undone"):
            continue
        rb = r.get("rollback") or {}
        kind = rb.get("kind")
        done = False
        if kind == "file" and rb.get("path"):
            try:
                if os.path.exists(rb["path"]):
                    os.remove(rb["path"])
                    done = True
            except OSError:
                done = False
        elif kind == "memory" and rb.get("path") and rb.get("line"):
            try:                       # 记忆是追加写：删掉最后一行即可
                with open(rb["path"], "r", encoding="utf-8") as f:
                    lines = f.readlines()
                if lines and lines[-1].rstrip("\n") == rb["line"]:
                    with open(rb["path"], "w", encoding="utf-8") as f:
                        f.writelines(lines[:-1])
                    done = True
            except OSError:
                done = False
        else:
            continue                   # 无法安全回滚的跳过，不假装撤销
        try:
            rows_all = ledger(200)
            for x in rows_all:
                if x.get("ts") == r.get("ts") and x.get("action") == r.get("action"):
                    x["undone"] = True
            with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
                for x in rows_all:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return {"undone": done, "action": r.get("action"),
                "detail": r.get("detail")}
    return {"undone": False}
