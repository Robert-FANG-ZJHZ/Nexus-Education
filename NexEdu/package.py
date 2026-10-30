"""专家 / 技能包（Package）：导出与导入。

让教研组的成果能**带着走**：一位教师调好的技能（如「我校作业分层规范」）
可以导出成一个 JSON 文件，发给同组教师导入即可复用，不必各自重抄一遍。

导出内容
--------
- **skills**：本地自定义技能（可被导入并真正生效）；
- **experts**：专家矩阵目录（**只读参考**，用于教研对齐；内置专家提示词随程序
  编译分发，不允许被外部包改写，以防提示词注入）；
- **workflows**：内置工作流目录（只读参考）；
- **tools**：当前可用工具清单（含 MCP 外接工具，只读参考）；
- **triggers**：触发器配置（可选导入，导入后需教师自行启用）。

导入策略
--------
- 技能按「名称 + 提示词」去重，已存在则跳过，**不覆盖**教师已有的本地修改；
- 触发器按名称去重，默认停用，需教师确认后再启用；
- 专家目录只做记录，不落地为可执行的提示词覆盖。

提示词定制包（2026-10-06 新增）
-----------------------------
教研员改好的提示词（workspace/prompts/*.md）此前用本机特征加密，
拷到别的老师机器上解不开。现在提供**口令加密**的提示词包导出/导入：
导出时按口令派生密钥加密，导入时输入同一口令即可，不再绑定设备——
校本定制从此可以跨机分发。
"""

import base64
import hashlib
import json
import os
import time

from . import _crypto, agents, config, guard, skills, tools, triggers, workflow

FORMAT = "nexedu-package"
VERSION = 1

PROMPT_DIR = os.path.join(config.WORKSPACE_DIR, "prompts")
_PP_MAGIC = "NEXEDU-PP1:"
_PP_SALT = b"NEXEDU-PROMPT-PACK-SALT-v1"


def export_package(include=None, passphrase=None):
    """导出一个可交换的包（dict）。

    passphrase 非空且 include 含 "prompts" 时，附带口令加密的提示词包。
    """
    include = include or ["experts", "skills", "workflows", "tools", "triggers"]
    pkg = {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": time.time(),
        "app": "山海智绘 NexEdu",
    }
    if "experts" in include:
        pkg["experts"] = [{
            "id": e["id"], "name": e["name"], "cn": e["cn"], "en": e.get("en", ""),
            "desc": e["desc"], "outputs": list(e.get("outputs") or []),
            "read_only": True,
        } for e in agents.list_experts()]
    if "skills" in include:
        pkg["skills"] = skills.list_skills()
    if "workflows" in include:
        pkg["workflows"] = workflow.list_workflows()
    if "tools" in include:
        pkg["tools"] = [{"name": n,
                         "desc": s.get("desc", ""),
                         "args": s.get("args", {}),
                         "required": s.get("required", []),
                         "side_effect": bool(s.get("side_effect")),
                         "external": n.startswith("mcp_")}
                        for n, s in sorted(tools.TOOLS.items())]
    if "triggers" in include:
        pkg["triggers"] = triggers.STORE.list()
    if "prompts" in include and passphrase:
        pkg["prompt_pack"] = export_prompt_pack(passphrase)
    return pkg


def import_package(data, import_skills=True, import_triggers=False,
                   import_prompts=False, passphrase=""):
    """导入一个包。返回统计结果（不做静默覆盖）。"""
    if not isinstance(data, dict):
        return {"ok": False, "error": "包格式不正确"}
    if data.get("format") and data.get("format") != FORMAT:
        return {"ok": False, "error": "不是 NexEdu 技能包：%s" % data.get("format")}

    stat = {"skills_added": 0, "skills_skipped": 0,
            "triggers_added": 0, "triggers_skipped": 0,
            "experts_seen": 0, "errors": []}

    for e in (data.get("experts") or []):
        if isinstance(e, dict) and e.get("id"):
            stat["experts_seen"] += 1
    # 内置专家提示词随程序编译分发，外部包不允许改写（防提示词注入）
    if stat["experts_seen"]:
        stat["errors"].append(
            "包内含 %d 位专家目录，已作为参考记录；内置专家提示词不支持外部改写"
            % stat["experts_seen"])

    if import_skills:
        exist = {(s.get("name") or "").strip() for s in skills.list_skills()}
        for s in (data.get("skills") or []):
            if not isinstance(s, dict):
                continue
            name = (s.get("name") or "").strip()
            prompt = (s.get("prompt") or s.get("content") or "").strip()
            if not name or not prompt:
                stat["skills_skipped"] += 1
                continue
            if name in exist:
                stat["skills_skipped"] += 1
                continue
            try:
                skills.create_skill(name, s.get("desc") or "", prompt)
                exist.add(name)
                stat["skills_added"] += 1
            except Exception as ex:
                stat["errors"].append("技能「%s」导入失败：%s" % (name, ex))

    if import_triggers:
        have = {t.get("name") for t in triggers.STORE.list()}
        for t in (data.get("triggers") or []):
            if not isinstance(t, dict):
                continue
            name = (t.get("name") or "").strip()
            if not name or name in have:
                stat["triggers_skipped"] += 1
                continue
            try:
                t = dict(t)
                t["enabled"] = False      # 导入默认停用，需教师确认后启用
                triggers.STORE.create(t)
                have.add(name)
                stat["triggers_added"] += 1
            except Exception as ex:
                stat["errors"].append("触发器「%s」导入失败：%s" % (name, ex))

    if import_prompts and data.get("prompt_pack"):
        try:
            r = import_prompt_pack(data["prompt_pack"], passphrase)
            stat["prompts_imported"] = r.get("files", 0)
        except Exception as ex:
            stat["errors"].append("提示词包导入失败：%s" % ex)

    stat["ok"] = True
    return stat


def dump_path():
    """默认导出落盘目录（workspace/packages）。"""
    return os.path.join(config.WORKSPACE_DIR, "packages")


# ---------------- 提示词定制包（口令加密，跨机分发） ----------------

def _pp_keys(passphrase):
    material = hashlib.sha256(
        (passphrase or "").encode("utf-8") + _PP_SALT).digest()
    return _crypto.derive_keys(material)


def export_prompt_pack(passphrase):
    """把 workspace/prompts/*.md（解密后）口令加密打包。

    返回字符串 "NEXEDU-PP1:<base64(AES-256-CTR+HMAC)>"。
    口令与密钥都不落盘——导入方必须输入同一口令。
    """
    if not passphrase:
        raise ValueError("缺少导出口令")
    files = {}
    if os.path.isdir(PROMPT_DIR):
        for fn in sorted(os.listdir(PROMPT_DIR)):
            if not fn.endswith(".md"):
                continue
            try:
                with open(os.path.join(PROMPT_DIR, fn), "r",
                          encoding="utf-8") as f:
                    raw = f.read()
            except OSError:
                continue
            content = guard.deobfuscate(raw)
            if content:
                files[fn] = content
    if not files:
        raise ValueError("本机还没有已导出的提示词文件（workspace/prompts/ 为空），"
                         "无需打包分发")
    payload = json.dumps({"format": "nexedu-prompt-pack", "version": 1,
                          "files": files}, ensure_ascii=False).encode("utf-8")
    k, mk = _pp_keys(passphrase)
    blob = _crypto.encrypt(k, mk, payload)
    return _PP_MAGIC + base64.b64encode(blob).decode("ascii")


def import_prompt_pack(data, passphrase):
    """解密并导入提示词包。返回 {"files": n}。

    写入 workspace/prompts/{name}.md 时用本机加密（guard.obfuscate），
    与现有的「导出提示词可编辑」机制一致，重启后生效。
    """
    if not isinstance(data, str) or not data.startswith(_PP_MAGIC):
        raise ValueError("不是有效的提示词包")
    if not passphrase:
        raise ValueError("缺少导入口令")
    try:
        k, mk = _pp_keys(passphrase)
        raw = _crypto.decrypt(k, mk, base64.b64decode(data[len(_PP_MAGIC):]))
    except ValueError:
        raise ValueError("口令错误或提示词包已损坏")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("提示词包内容损坏")
    files = obj.get("files") or {}
    if not isinstance(files, dict):
        raise ValueError("提示词包内容损坏")
    os.makedirs(PROMPT_DIR, exist_ok=True)
    n = 0
    for fn, content in files.items():
        name = os.path.basename(fn or "")
        if not name.endswith(".md") or not isinstance(content, str):
            continue
        try:
            with open(os.path.join(PROMPT_DIR, name), "w",
                      encoding="utf-8") as f:
                f.write(guard.obfuscate(content))
            n += 1
        except OSError:
            continue
    return {"files": n}
