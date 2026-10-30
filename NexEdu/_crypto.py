# -*- coding: utf-8 -*-
"""内置轻量 AEAD：AES-256-CTR + HMAC-SHA256（Encrypt-then-MAC），零第三方依赖。

为什么自己实现而不是引 cryptography / pyaes：
- 本项目运行时坚持零第三方依赖（打包为单文件 exe），引 C 扩展会增加打包面与体积；
- CTR 模式只需要 AES 的**加密**方向（不需要逆 cipher），实现与审计面减半；
- 认证直接复用标准库 hmac/hashlib，避免自造 GCM 的 GF(2^128) 坑。

用途：提示词 / 工作流 / 打包资源的静态加密与设备绑定（见 guard.obfuscate）。

边界：这是**提高提取成本**的工程手段，不是对抗逆向者的密码学保证——
本地桌面程序的密钥必然存在于用户机器上，目标是"离机即废 + 不可随手阅读"。
"""

import hashlib
import hmac
import os
import zlib

__all__ = ["encrypt", "decrypt", "fast_encrypt", "fast_decrypt",
           "device_material", "derive_keys", "selftest"]

# --------------------------------------------------------------------------
# AES-256 加密核心（纯 Python；仅需 encrypt 方向）
# --------------------------------------------------------------------------

def _build_sbox():
    """按 GF(2^8) 逆元 + 仿射变换生成 S-box（不硬编码长表，避免抄错）。"""
    sbox = [0] * 256
    p = q = 1
    while True:
        # p *= 3
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        # q /= 3
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) \
              ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_SBOX = _build_sbox()
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D)
_NK, _NR = 8, 14          # AES-256：8 字密钥、14 轮


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a & 0xFF


def _expand_key(key):
    w = [list(key[4 * i:4 * i + 4]) for i in range(_NK)]
    for i in range(_NK, 4 * (_NR + 1)):
        t = list(w[i - 1])
        if i % _NK == 0:
            t = t[1:] + t[:1]                      # RotWord
            t = [_SBOX[b] for b in t]              # SubWord
            t[0] ^= _RCON[i // _NK - 1]            # Rcon
        elif i % _NK == 4:
            t = [_SBOX[b] for b in t]              # AES-256 额外 SubWord
        w.append([w[i - _NK][j] ^ t[j] for j in range(4)])
    return w


def _encrypt_block(w, block):
    """单块加密。state 采用列主序：s[4*c + r] 表示第 r 行第 c 列。"""
    s = list(block)

    def add_round_key(rnd):
        for c in range(4):
            base = 4 * c
            wk = w[rnd * 4 + c]
            s[base] ^= wk[0]
            s[base + 1] ^= wk[1]
            s[base + 2] ^= wk[2]
            s[base + 3] ^= wk[3]

    add_round_key(0)
    for rnd in range(1, _NR + 1):
        s = [_SBOX[b] for b in s]                                   # SubBytes
        s = [s[4 * ((c + r) % 4) + r] for c in range(4) for r in range(4)]   # ShiftRows
        if rnd != _NR:                                              # MixColumns（末轮不做）
            out = [0] * 16
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                t = a0 ^ a1 ^ a2 ^ a3
                out[4 * c]     = a0 ^ t ^ _xtime(a0 ^ a1)
                out[4 * c + 1] = a1 ^ t ^ _xtime(a1 ^ a2)
                out[4 * c + 2] = a2 ^ t ^ _xtime(a2 ^ a3)
                out[4 * c + 3] = a3 ^ t ^ _xtime(a3 ^ a0)
            s = out
        add_round_key(rnd)
    return bytes(s)


def _ctr(key, nonce12, data):
    """AES-256-CTR：nonce(12B) || counter(4B, 大端) 作为计数器块。"""
    w = _expand_key(key)
    out = bytearray()
    counter = 0
    for off in range(0, len(data), 16):
        ks = _encrypt_block(w, nonce12 + counter.to_bytes(4, "big"))
        chunk = data[off:off + 16]
        out.extend(a ^ b for a, b in zip(chunk, ks))
        counter += 1
    return bytes(out)


# --------------------------------------------------------------------------
# Encrypt-then-MAC：密文格式 = nonce(12) || ciphertext || mac(32)
# --------------------------------------------------------------------------

_AAD = b"NEXEDU-CTR-HMAC-V1"


def encrypt(key, mac_key, plaintext):
    nonce = os.urandom(12)
    ct = _ctr(key, nonce, plaintext)
    mac = hmac.new(mac_key, _AAD + nonce + ct, hashlib.sha256).digest()
    return nonce + ct + mac


def decrypt(key, mac_key, blob):
    if len(blob) < 12 + 32:
        raise ValueError("ciphertext too short")
    nonce, ct, mac = blob[:12], blob[12:-32], blob[-32:]
    expect = hmac.new(mac_key, _AAD + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expect, mac):
        raise ValueError("mac mismatch (wrong device key or tampered data)")
    return _ctr(key, nonce, ct)


# --------------------------------------------------------------------------
# 大文件"快速模式"：zlib 压缩 + 大整数 XOR
#
# 为什么需要第二种模式：纯 Python AES 约 125 KB/s，12 MB 的知识库解密要 96 秒，
# 启动时完全不可接受。这里改用 C 实现的 hashlib 生成密钥流、再用**大整数异或**
# （int.from_bytes / to_bytes 都是 C 实现），12 MB 约 1 秒，且压缩后体积更小。
# 代价：流密码强度弱于 AES-CTR，定位是"防随手提取"，不用于高价值小资产。
# --------------------------------------------------------------------------

_FAST_MAGIC = b"NXF1"


def _keystream(seed, n):
    """用 SHA-256 迭代生成 n 字节密钥流（hashlib 是 C 实现，很快）。"""
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out += hashlib.sha256(seed + b"|ks|" + ctr.to_bytes(8, "big")).digest()
        ctr += 1
    return bytes(out[:n])


def fast_encrypt(mac_key, data):
    body = zlib.compress(data, 6)
    if not body:
        return _FAST_MAGIC + b""
    ks = _keystream(mac_key, len(body))
    body = (int.from_bytes(body, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(body), "big")
    mac = hmac.new(mac_key, _FAST_MAGIC + body, hashlib.sha256).digest()
    return _FAST_MAGIC + mac + body


def fast_decrypt(mac_key, blob):
    if not blob.startswith(_FAST_MAGIC) or len(blob) < 4 + 32:
        raise ValueError("bad fast blob")
    mac, body = blob[4:36], blob[36:]
    if not hmac.compare_digest(hmac.new(mac_key, _FAST_MAGIC + body,
                                        hashlib.sha256).digest(), mac):
        raise ValueError("mac mismatch (wrong key or tampered data)")
    if not body:
        return b""
    ks = _keystream(mac_key, len(body))
    comp = (int.from_bytes(body, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(body), "big")
    return zlib.decompress(comp)


# --------------------------------------------------------------------------
# 设备绑定密钥派生
# --------------------------------------------------------------------------

def _machine_guid():
    """Windows 机器 GUID（比 COMPUTERNAME 稳定且不易被随手改写）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as k:
            return str(winreg.QueryValueEx(k, "MachineGuid")[0])
    except Exception:
        return ""


def device_material(salt=b""):
    """设备密钥材料：换机即变 → 密文离机即废。"""
    parts = [_machine_guid()]
    try:
        import platform
        parts += [platform.node(), platform.machine()]
    except Exception:
        pass
    seed = "|".join(p for p in parts if p) or "nexedu-fallback"
    if salt:
        seed += "|" + (salt.decode("utf-8", "ignore")
                       if isinstance(salt, bytes) else str(salt))
    return hashlib.sha256(seed.encode("utf-8")).digest()


def derive_keys(material):
    """从密钥材料派生 (加密密钥, 认证密钥)。"""
    return (hashlib.sha256(material + b"|enc").digest(),
            hashlib.sha256(material + b"|mac").digest())


# --------------------------------------------------------------------------
# 自检：用 NIST / FIPS-197 已知向量验证 AES-256 实现正确性
# --------------------------------------------------------------------------

def selftest():
    # FIPS-197 AES-256 已知答案：key=000102..1f, plain=00112233445566778899aabbccddeeff
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    want = "8ea2b7ca516745bfeafc49904b496089"
    got = _encrypt_block(_expand_key(key), pt).hex()
    assert got == want, "AES-256 自检失败: %s != %s" % (got, want)

    # 往返 + 认证 + 篡改检测
    m = device_material(b"t")
    k, mk = derive_keys(m)
    for raw in (b"", b"a", b"x" * 31, b"x" * 32, "中文提示词\n第二行".encode("utf-8")):
        blob = encrypt(k, mk, raw)
        assert decrypt(k, mk, blob) == raw, "往返失败"
        bad = bytearray(blob)
        bad[20] ^= 0x01
        try:
            decrypt(k, mk, bytes(bad))
        except ValueError:
            pass
        else:
            raise AssertionError("篡改未被检出")
    # 快速模式：大文件往返 + 篡改检出
    big = ("知识库切片内容测试 " * 4000).encode("utf-8")
    fb = fast_encrypt(mk, big)
    assert fast_decrypt(mk, fb) == big, "快速模式往返失败"
    try:
        fast_decrypt(mk, fb[:-1] + bytes([fb[-1] ^ 1]))
    except ValueError:
        pass
    else:
        raise AssertionError("快速模式篡改未被检出")

    # 换设备（不同 salt 模拟）应解不开
    k2, mk2 = derive_keys(device_material(b"other"))
    try:
        decrypt(k2, mk2, encrypt(k, mk, b"secret"))
    except ValueError:
        pass
    else:
        raise AssertionError("跨设备仍能解密，绑定失效")
    return True
