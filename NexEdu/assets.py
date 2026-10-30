# -*- coding: utf-8 -*-
"""打包资源加载器（P2：构建期资源预加密）。

构建脚本 `tools/pack_assets.py` 把核心资产加密为单个 `assets.pak`：
    web/index.html、kb_seed/chunks.json、nexedu/data/{embed_vocab.json,
    embed_idf.f32, curriculum.json}
打包后 exe 内**不含这些文件的明文**；运行时经本模块解密使用。

密钥分片存放于本模块，其强度来自承载它的代码被加密：
P0（PyInstaller --key 字节码加密）+ P1（PyArmor）。二者配合才有意义——
单独看 pak 的密钥在包内，属于"防随手解压查看"的混淆级保护。

源码运行模式（非 frozen）**始终读原文件**，不读 pak —— 开发时改 index.html
立即生效，不会被 pak 遮蔽。

首次启动需解密（约 380KB，纯 Python AES 约 3 秒），之后复用
`%LOCALAPPDATA%\\NexEdu\\_assets` 缓存，二次启动零开销。
"""

import hashlib
import os
import shutil
import sys

from . import _crypto

# 密钥分片：分散存放，避免一个完整密钥常量被一眼搜到
_S1 = "NexEdu"
_S2 = "-AssetPak"
_S3 = "-2026-v1"

PAK_NAME = "assets.pak"
_MAGIC = b"NEXEDU-PAK1"

_PAK = None          # {name: 密文 bytes}
_PAK_OK = False


def pak_keys():
    """pak 的 (加密密钥, 认证密钥)。构建脚本与运行时共用同一派生方式。"""
    return _crypto.derive_keys(hashlib.sha256((_S1 + _S2 + _S3).encode("utf-8")).digest())


def _frozen():
    return bool(getattr(sys, "frozen", False))


def pak_path():
    """pak 位置：打包后位于 <资源目录>/nexedu/data/assets.pak。"""
    try:
        from . import config
        return os.path.join(config._resource_dir(), "nexedu", "data", PAK_NAME)
    except Exception:
        return ""


def _ensure_pak():
    global _PAK, _PAK_OK
    if _PAK_OK:
        return
    _PAK_OK = True                      # 只尝试一次，失败则永久回退原文件
    path = pak_path()
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if not raw.startswith(_MAGIC):
            return
        pos, entries = len(_MAGIC), {}
        while pos + 6 <= len(raw):
            nlen = int.from_bytes(raw[pos:pos + 2], "big")
            pos += 2
            name = raw[pos:pos + nlen].decode("utf-8")
            pos += nlen
            dlen = int.from_bytes(raw[pos:pos + 4], "big")
            pos += 4
            entries[name] = raw[pos:pos + dlen]
            pos += dlen
        _PAK = entries
    except Exception:
        _PAK = None


def _decrypt(name, frozen_only=False):
    """解密指定条目。

    frozen_only=True：仅打包运行（frozen）时才读 pak —— 用于 web/index.html，
    这样源码模式改前端能立即生效，不被 pak 遮蔽。
    frozen_only=False：pak 存在就读 —— 用于"数据化"资产（专家矩阵 / 内置技能），
    这类数据已完全移出 .py，源码运行同样依赖 pak。
    """
    if frozen_only and not _frozen():
        return None
    _ensure_pak()
    if not _PAK or name not in _PAK:
        return None
    k, mk = pak_keys()
    blob = _PAK[name]
    try:
        # 大文件用快速模式（zlib + 大整数 XOR），小资产用 AES-256-CTR + HMAC
        if blob[:4] == _crypto._FAST_MAGIC:
            return _crypto.fast_decrypt(mk, blob)
        return _crypto.decrypt(k, mk, blob)
    except Exception:
        return None


def has(name):
    """pak 中是否含有该条目。"""
    _ensure_pak()
    return bool(_PAK) and name in _PAK


def read_bytes(name):
    """返回明文 bytes；无该条目返回 None（仅打包运行生效）。"""
    return _decrypt(name, frozen_only=True)


def read_text(name):
    """返回明文文本；无该条目返回 None（仅打包运行生效）。"""
    data = _decrypt(name, frozen_only=True)
    return None if data is None else data.decode("utf-8", "replace")


def read_data(name):
    """读取"数据化"资产：只要 pak 存在就解密（不区分运行模式）。

    用于已把大数据移出 .py 的模块（experts_data / skilllib），
    构建脚本会保证 nexedu/data/assets.pak 存在。
    """
    return _decrypt(name)


def _cache_dir():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    d = os.path.join(base, "NexEdu", "_assets")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return ""
    return d


_SYNCED = False


def _sync_cache():
    """pak 变了（重新打包）就丢弃旧缓存，避免读到上一版明文。"""
    global _SYNCED
    if _SYNCED:
        return
    _SYNCED = True
    p, d = pak_path(), _cache_dir()
    if not p or not d:
        return
    try:
        st = os.stat(p)
        sig = "%d:%d" % (st.st_size, int(st.st_mtime))
    except OSError:
        return
    sp = os.path.join(d, "_pak.sig")
    try:
        old = ""
        if os.path.exists(sp):
            with open(sp, "r", encoding="utf-8") as f:
                old = f.read()
        if old != sig:
            for fn in os.listdir(d):
                if fn != "_pak.sig":
                    try:
                        os.remove(os.path.join(d, fn))
                    except OSError:
                        pass
            with open(sp, "w", encoding="utf-8") as f:
                f.write(sig)
    except OSError:
        pass


def path(name, fallback):
    """返回可打开的文件路径。

    frozen 且 pak 含该条目 → 解密到缓存目录返回缓存路径（首次解密，之后复用）；
    否则返回 fallback（源码模式 / 未打包该资产）。
    """
    if not _frozen():
        return fallback
    _sync_cache()
    data = _decrypt(name)
    if data is None:
        return fallback
    cdir = _cache_dir()
    if not cdir:
        return fallback
    dst = os.path.join(cdir, name.replace("/", "__").replace("\\", "__"))
    try:
        if not os.path.exists(dst) or os.path.getsize(dst) != len(data):
            tmp = dst + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dst)
    except OSError:
        return fallback
    return dst


def drop_cache():
    """清空解密缓存（用于 pak 更新后强制重解）。"""
    cdir = _cache_dir()
    if cdir and os.path.isdir(cdir):
        try:
            shutil.rmtree(cdir)
        except OSError:
            pass
