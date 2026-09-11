# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('captcha_recognizer', 'captcha_recognizer')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('shapely')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # pandas 不是本工具的依赖，但如果打包机上恰好装了它，某个 hook 会把它整包拖进来
    # （CI 的干净环境没有，本地有，于是两边产物体积对不上）。显式排除，保证两边一致。
    excludes=['matplotlib', 'PyQt5', 'PySide6', 'pandas'],
    noarchive=False,
    optimize=0,
)

# 摘掉 cv2 自带的 ffmpeg 视频后端（未压缩 30.9 MB，压进包 12.9 MB）。
# 本工具只做截屏 + 模板匹配，不读视频：源码里没有 VideoCapture/VideoWriter，
# 唯一的文件读取是 cv2.imread（读图片，走 libjpeg/png，不碰 ffmpeg）。
# 已确认它不在 cv2.pyd 的 PE 导入表里 —— 是运行时按需加载的插件，缺了不影响 import cv2。
# 注意 TOC 元组是 (dest_name, src_name, typecode)，所以取 b[0] 比对目标名。
a.binaries = [b for b in a.binaries if "opencv_videoio_ffmpeg" not in b[0].lower()]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='auto_login_vision',
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
    icon=['assets/app.ico'],
)
