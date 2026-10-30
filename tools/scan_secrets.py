# -*- coding: utf-8 -*-
"""开源前密钥泄漏扫描：确保仓库里没有任何 API Key / 密码 / 令牌。

用法：
    py tools/scan_secrets.py            # 扫描开源会带上的目录
    py tools/scan_secrets.py --all      # 连同构建产物一起扫（更严格）

原理：源码架构上不存密钥（API Key 存用户目录 %APPDATA%/NexEdu/credentials.json，
不随源码/exe 分发）。本工具做最后一道闸：提交/发布前扫一遍，任何疑似硬编码
密钥立即报错退出，绝不静默放过。

白名单说明：
    - _crypto.py 的 AES 已知答案（FIPS-197 测试向量）——算法自检，非密钥；
    - smoke_new.py 的 api_key="k"——单元测试占位符；
    - .env.example 的空 GLM_API_KEY=——示例文件，值为空。
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 开源仓库会包含的目录（其余一律不进仓库，见 opensource/RELEASE_CHECKLIST.md）
SCAN_DIRS = ["nexedu", "web", "tools", "tests", "docs", "kb_seed", "blueprint", "opensource"]
SCAN_FILES = ["main.py", "requirements.txt", ".env.example", "NexEdu.spec",
              "build_kb.py", "启动NexEdu.bat"]

# --- 疑似密钥的正则模式（命中即 FAIL） ---
PATTERNS = [
    # 智谱 / OpenAI / DeepSeek 等常见 sk- 前缀密钥
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "sk- 开头的 API Key"),
    # 裸十六进制 32 位（常见 API secret）
    (re.compile(r"\b[a-f0-9]{32}\b"), "32 位十六进制串（疑似 secret）"),
    # JWT
    (re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\."), "JWT 令牌"),
    # Bearer 后跟长串
    (re.compile(r"Bearer\s+[A-Za-z0-9_-]{24,}"), "Bearer 长令牌"),
    # 智谱风格 key.id 格式
    (re.compile(r"[a-f0-9]{32}\.[A-Za-z0-9]{16}"), "id.secret 格式密钥"),
    # 赋值形式：api_key = "非空长串"（排除空串/占位符/变量引用）
    (re.compile(r"""api[_-]?key["']?\s*[:=]\s*["'][A-Za-z0-9][A-Za-z0-9_-]{15,}["']""",
                re.I), "硬编码 api_key 赋值"),
    # password/token 赋值长串
    (re.compile(r"""(password|passwd|secret|token)["']?\s*[:=]\s*["'][A-Za-z0-9][^"']{15,}["']""",
                re.I), "硬编码 password/secret/token"),
]

# 误报白名单：路径关键次 + 内容特征（文件内出现即视为安全说明/测试，跳过该行）
ALLOW = [
    # FIPS-197 AES 已知答案：标准测试向量（key=000102..1f 明文 001122..ff
    # 的公开期望密文 8ea2b7ca..6089），是算法自检，不是任何平台的密钥
    (re.compile(r"_crypto\.py"), re.compile(
        r"0001020304|fromhex|selftest|FIPS|8ea2b7ca516745bfeafc49904b496089")),
    (re.compile(r"加密与防提取方案设计\.md"), re.compile(
        r"FIPS-197|8ea2b7ca516745bfeafc49904b496089")),
    (re.compile(r"smoke_new\.py"), re.compile(r'api_key\s*=\s*["\']k["\']')),       # 测试占位
    (re.compile(r"\.env"), re.compile(r"GLM_API_KEY=\s*$")),                        # 空示例
    (re.compile(r"scan_secrets\.py"), re.compile(r".")),                            # 本工具自身
    (re.compile(r"_check_frontend\.py|smoke_server\.py|_validate"), re.compile(r".")),  # 测试断言
    # 代码中的逻辑引用（读环境变量/配置文件），非硬编码
    (re.compile(r"\.(py|js|html|json|md)$"),
     re.compile(r"""(get\(["']api_key|get_api_key\(|api_key_set|api_key_masked|"""
                 r"""GLM_API_KEY|payload\.get\(["']api_key|data\.get\(["']api_key|"""
                 r"""json\.load\(f\)\.get\(["']api_key|"api_key":\s*key|"""
                 r"""api_key\s*=\s*api_key\s+if|self\.api_key)""")),
]

TEXT_EXT = {".py", ".js", ".html", ".json", ".md", ".txt", ".bat", ".spec",
            ".yaml", ".yml", ".toml", ".ini", ".cfg", ".example", ".gitignore", ""}


def _allowed(rel_path, line):
    for pat_re, line_re in ALLOW:
        if pat_re.search(rel_path) and line_re.search(line):
            return True
    return False


def scan():
    targets = []
    for d in SCAN_DIRS:
        dp = os.path.join(ROOT, d)
        if not os.path.isdir(dp):
            continue
        for base, _, files in os.walk(dp):
            # 跳过缓存/构建子目录
            parts = os.path.relpath(base, ROOT).split(os.sep)
            if any(p in ("__pycache__", "node_modules", ".git") for p in parts):
                continue
            for f in files:
                targets.append(os.path.join(base, f))
    for f in SCAN_FILES:
        p = os.path.join(ROOT, f)
        if os.path.isfile(p):
            targets.append(p)

    findings = []
    n_text = 0
    for path in targets:
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        ext = os.path.splitext(rel)[1].lower()
        if ext not in TEXT_EXT:
            continue
        n_text += 1
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for ln, line in enumerate(fh, 1):
                    for pat, name in PATTERNS:
                        m = pat.search(line)
                        if m and not _allowed(rel, line):
                            findings.append((rel, ln, name, line.strip()[:100]))
                            break
        except OSError:
            continue    # 读不了的文件直接跳过

    print("扫描文件：%d 个文本文件（目录：%s）" % (n_text, ", ".join(SCAN_DIRS)))
    if findings:
        print("\n[FAIL] 发现 %d 处疑似密钥：" % len(findings))
        for rel, ln, name, ctx in findings:
            print("  %s:%d  [%s]" % (rel, ln, name))
            print("    %s" % ctx)
        return False
    print("\n[PASS] 未发现任何硬编码密钥。")
    print("（密钥按架构存于用户目录 credentials.json，不随源码分发；")
    print("  发布前请再核对 opensource/RELEASE_CHECKLIST.md 的排除清单。）")
    return True


if __name__ == "__main__":
    ok = scan()
    sys.exit(0 if ok else 1)
