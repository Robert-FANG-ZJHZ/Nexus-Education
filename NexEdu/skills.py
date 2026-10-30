"""自定义技能（Skill）管理 + 内置技能库：创建 / 列出 / 删除，持久化到本地 JSON。

技能是教师可自行创建的可复用能力片段（名称 + 描述 + 指令），
可在对话时勾选启用，作为附加指令注入专家 System Prompt。

两类技能：
- **内置技能**（`skilllib`）：随程序分发的 177 条（127 教学领域 + 50 通用能力），
  只读、**不出现在技能工坊**，由对应专家在需要时自动调用
  （`agents.build_system` → `skilllib.pick_for`）；
- **自定义技能**：教师自建，保存在 `workspace/skills.json`，可增删改，
  在技能工坊勾选后作为附加指令注入专家 System Prompt。
"""

import json
import os
import uuid

from . import config
from . import skilllib

SKILLS_PATH = os.path.join(config.WORKSPACE_DIR, "skills.json")

BUILTIN_IDS = skilllib.BUILTIN_IDS


def _load():
    if os.path.exists(SKILLS_PATH):
        try:
            with open(SKILLS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save(skills):
    os.makedirs(config.WORKSPACE_DIR, exist_ok=True)
    with open(SKILLS_PATH, "w", encoding="utf-8") as f:
        json.dump(skills, f, ensure_ascii=False, indent=2)


def list_custom():
    """教师自建技能（不含内置库）。"""
    return _load()


def list_builtin():
    """内置技能库（127 教学领域 + 50 通用能力）。"""
    return skilllib.list_builtin()


def list_user():
    """教师自建技能（技能工坊只展示这一类）。"""
    return [dict(s, builtin=False) for s in list_custom()]


def list_skills():
    """技能工坊与注入使用的技能清单：**仅教师自建**。

    系统内置技能（skilllib 177 条）不进工坊、不供勾选，
    由对应专家在需要时自动调用，见 ``agents.build_system``。
    """
    return list_user()


def list_all():
    """内置 + 自建（内部自检/统计用，不下发给前端列表）。"""
    return list_builtin() + list_user()


def count_summary():
    return {"builtin": len(list_builtin()), "custom": len(list_custom())}


def is_builtin(skill_id):
    return skill_id in BUILTIN_IDS


def get(skill_id):
    """按 id 取技能（内置优先）。"""
    s = skilllib.get(skill_id)
    if s:
        item = dict(s)
        item["prompt"] = skilllib.render_prompt(s)
        item["builtin"] = True
        return item
    for s in _load():
        if s.get("id") == skill_id:
            return dict(s, builtin=False)
    return None


def create_skill(name, desc, prompt):
    name = (name or "").strip()
    prompt = (prompt or "").strip()
    if not name or not prompt:
        raise ValueError("技能名称与指令不能为空")
    skills = _load()
    skill = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "desc": (desc or "").strip(),
        "prompt": prompt,
    }
    skills.append(skill)
    _save(skills)
    return skill


def delete_skill(skill_id):
    if is_builtin(skill_id):
        raise ValueError("内置技能由系统自带、不在技能工坊中，无需也无法删除")
    skills = [s for s in _load() if s.get("id") != skill_id]
    _save(skills)


def clear_all():
    """清空教师自建技能（清痕入口）。返回清除数量。"""
    n = len(_load())
    _save([])
    return n


def render_enabled(skills):
    """把勾选启用的自建技能渲染为附加指令文本。

    内置技能不走这里（它们由专家自动调用），故此处只接受自建技能；
    历史数据里若混有内置 id，按 id 判重后静默忽略。
    """
    items = [s for s in (skills or []) if not is_builtin(s.get("id"))]
    if not items:
        return ""
    lines = ["# 已启用的自建技能（附加指令，须在不违背专家职责的前提下执行）"]
    for s in items:
        lines.append("- " + (s.get("prompt") or s.get("desc") or s.get("name") or ""))
    return "\n".join(lines)
