"""本地数据维护：备份 / 恢复 / 清痕（本地部署的数据治理入口）。

全部本地完成，不上传任何内容：
- build_backup(password)：workspace + 用户级配置（%APPDATA%/NexEdu）打包为 zip；
  提供口令时用 AES-256-CTR + HMAC 加密（口令派生密钥，换机可解）。
- restore_backup(data, password)：解包并写回，路径穿越防护、单文件上限。
- clear_personal_data()：转赠设备前的清痕（会话/教案/缓存/记忆/自建技能/评测数据）；
  知识库资料保留（属教学资料而非个人数据）。
"""

import base64
import hashlib
import io
import logging
import os
import re
import time
import zipfile

from . import _crypto, config

_log = logging.getLogger("nexedu.maintenance")

_BACKUP_MAGIC = b"NEXEDU-BACKUP1"
_SALT = b"NEXEDU-BACKUP-SALT-v1"
_MAX_ENTRY = 200 * 1024 * 1024        # 单条解压上限（防解压炸弹）


def _pp_keys(password):
    material = hashlib.sha256(
        (password or "").encode("utf-8") + _SALT).digest()
    return _crypto.derive_keys(material)


def _zip_dir(zf, base, arc):
    """把目录递归加入 zip（跳过 .acl-reports 等维护目录）。"""
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in (".acl-reports",)]
        for fn in files:
            p = os.path.join(root, fn)
            try:
                if os.path.getsize(p) > _MAX_ENTRY:
                    continue
            except OSError:
                continue
            rel = os.path.relpath(p, base)
            zf.write(p, arc + "/" + rel.replace("\\", "/"))


def build_backup(password=""):
    """打包全部本地数据。返回 (bytes, 文件名)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1) 用户级配置与凭证（%APPDATA%/NexEdu）
        ud = os.path.dirname(config.credential_path())
        if os.path.isdir(ud):
            _zip_dir(zf, ud, "user")
        # 2) 工作区（workspace）
        if os.path.isdir(config.WORKSPACE_DIR):
            _zip_dir(zf, config.WORKSPACE_DIR, "workspace")
    raw = buf.getvalue()
    name = "nexedu-backup-%s.zip" % time.strftime("%Y%m%d-%H%M%S")
    if password:
        k, mk = _pp_keys(password)
        enc = _crypto.encrypt(k, mk, raw)
        return _BACKUP_MAGIC + base64.b64encode(enc), name + ".enc"
    return raw, name


def restore_backup(data, password=""):
    """从备份恢复。返回统计 dict。"""
    payload = data
    if data.startswith(_BACKUP_MAGIC):
        if not password:
            raise ValueError("该备份已加密，请输入备份口令")
        try:
            k, mk = _pp_keys(password)
            payload = _crypto.decrypt(
                k, mk, base64.b64decode(data[len(_BACKUP_MAGIC):]))
        except ValueError:
            raise ValueError("口令错误或备份已损坏")
    try:
        zf = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        raise ValueError("不是有效的 NexEdu 备份文件")

    stats = {"user_files": 0, "workspace_files": 0}
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.endswith("/") or not name:
            continue
        if info.file_size > _MAX_ENTRY:
            continue
        parts = name.split("/", 1)
        if len(parts) != 2:
            continue
        area, rel = parts
        if area == "user":
            root = os.path.dirname(config.credential_path())
        elif area == "workspace":
            root = config.WORKSPACE_DIR
        else:
            continue
        # 路径穿越防护：任何 .. 段直接跳过
        safe = re.sub(r"(^|/)\.\.(/|$)", "/", rel)
        if safe != rel or rel.startswith("/") or ":" in rel:
            continue
        target = os.path.join(root, safe.replace("/", os.sep))
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
        except OSError as e:
            _log.warning("恢复文件失败 %s: %s", target, e)
            continue
        stats[area + "_files"] += 1
    return stats


def clear_personal_data():
    """清痕：会话/教案/缓存/记忆/自建技能/评测与审计数据；知识库资料保留。"""
    stat = {"sessions": 0, "lessons": 0, "cache": 0, "memory": 0,
            "skills": 0, "logs": 0}
    try:
        from . import sessions
        stat["sessions"] = sessions.clear_all()
    except Exception:
        pass
    try:
        from . import lessons
        stat["lessons"] = lessons.clear_all()
    except Exception:
        pass
    try:
        from . import cache
        stat["cache"] = cache.clear().get("cleared", 0)
    except Exception:
        pass
    ws = config.WORKSPACE_DIR
    mem_dir = os.path.join(ws, "memory")
    if os.path.isdir(mem_dir):
        for fn in os.listdir(mem_dir):
            try:
                os.remove(os.path.join(mem_dir, fn))
                stat["memory"] += 1
            except OSError:
                pass
    core = os.path.join(ws, "MEMORY.md")
    if os.path.exists(core):
        try:
            os.remove(core)
            stat["memory"] += 1
        except OSError:
            pass
    try:
        from . import skills
        stat["skills"] = skills.clear_all()
    except Exception:
        pass
    log_dir = os.path.join(ws, "logs")
    if os.path.isdir(log_dir):
        for fn in os.listdir(log_dir):
            if fn.endswith(".jsonl"):
                try:
                    os.remove(os.path.join(log_dir, fn))
                    stat["logs"] += 1
                except OSError:
                    pass
    try:
        from . import assets
        assets.drop_cache()
    except Exception:
        pass
    _log.info("已清除本地个人数据：%s", stat)
    return stat
