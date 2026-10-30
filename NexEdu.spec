# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_data_files

# 关于字节码加密：PyInstaller 自 6.0 起**移除**了 --key / block_cipher
# （PyInstaller.archive.pyz_crypto.PyiBlockCipher 会直接抛 RemovedCipherFeatureError）。
# 因此字节码加密改由 PyArmor 承担（见 tools/build_obf.py 与文档 P1），
# 本 spec 不再使用任何 cipher 参数。

# pywebview 及其依赖装在项目内 _vendor 目录（见 main.py 顶部说明）
_VENDOR = os.path.join(SPECPATH, '_vendor')
sys.path.insert(0, _VENDOR)

# P1：PyArmor 加密产物。构造 build/pkg/{main.py, nexedu/} 并以它作为**主脚本目录**——
# 只加 pathex 不够：PyInstaller 会优先用主脚本所在目录解析包，否则仍会打包源码版。
# 注：当前 PyArmor 为 trial 许可，无法加密超大脚本（experts_data.py / skilllib.py），
# 故只加密核心模块 _crypto / assets / guard / workflow（密钥与工作流所在处）。
_PKG = os.path.join(SPECPATH, 'build', 'pkg')
_USE_PKG = os.path.isdir(os.path.join(_PKG, 'nexedu'))
if _USE_PKG:
    sys.path.insert(0, _PKG)
    print('[INFO] 使用 PyArmor 加密包：%s' % _PKG)

# 注意：web/index.html **不再明文随包分发**，改由加密资源包提供（见 tools/pack_assets.py）
datas = [
    ('web/favicon.ico', 'web'),
    ('web/assets', 'web/assets'),
    ('nexedu/data/assets.pak', 'nexedu/data'),
    # 随包分发的本地词向量（第 2 档能力直接内置，无需教师额外安装）：
    # 词表 + 近邻表约 0.3MB，完整词向量矩阵刻意不打包
    ('nexedu/data/embed_vocab.json', 'nexedu/data'),
    ('nexedu/data/embed_idf.f32', 'nexedu/data'),
    # 课标知识点先修关系（模板 + 示例，教师/教研可编辑补充）
    ('nexedu/data/curriculum.json', 'nexedu/data'),
    # pywebview 运行时数据：js 注入脚本 + WebView2 互操作 DLL + WebBrowserInterop
    # pythonnet：.NET runtime 程序集；clr_loader：本机桥接 DLL（保持包内相对路径）
]
datas += collect_data_files('webview')
datas += collect_data_files('pythonnet')
datas += collect_data_files('clr_loader')

hiddenimports = [
    'nexedu.svgchart',       # 图表 SVG 导出（server 内为函数级延迟导入，显式声明防漏打包）
    'nexedu.verify',         # 产出核验（agents/server 内均为延迟导入）
    'nexedu.classroom',      # 班级学情档案（延迟导入）
    'nexedu.options',        # 多方案择优（延迟导入）
    'nexedu.experts_data',   # 41 位专家矩阵（数据 + 提示词渲染）
    'nexedu.skilllib',       # 内置技能库（127 领域 + 50 通用）
    'nexedu._crypto',        # 内置 AEAD（提示词加密 / 资源包解密）
    'nexedu.assets',         # 加密资源包加载器
    'pyarmor_runtime_000000',  # PyArmor 运行时（加密模块 import 它）
    'pypdf',
    'webview',
    'webview.guilib',
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
    'webview.platforms.mshtml',
    'webview.platforms.win32',
    'bottle',
    'proxy_tools',
    'pythonnet',
    'clr_loader',
    'typing_extensions',
]

a = Analysis(
    [os.path.join(_PKG, 'main.py')] if _USE_PKG else ['main.py'],
    pathex=([_PKG] if _USE_PKG else []) + [SPECPATH, _VENDOR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NexEdu',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)
