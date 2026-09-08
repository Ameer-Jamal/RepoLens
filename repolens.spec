# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

datas = [
    ('repolens_default_config.json', '.'),
]

hiddenimports = [
    'PyQt5',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'pygments',
    'pygments.lexers',
    'pygments.formatters',
    'pygments.styles',
    'mcp',
    'mcp.server',
    'mcp.server.fastmcp',
    'requests',
    'services',
    'models',
    'ui',
    'headless_config',
    'ConfigManager',
]
hiddenimports += collect_submodules('services')
hiddenimports += collect_submodules('models')
hiddenimports += collect_submodules('ui')
hiddenimports += collect_submodules('pygments.lexers')
hiddenimports += collect_submodules('pygments.formatters')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'numpy'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RepoLens',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RepoLens',
)

app = BUNDLE(
    coll,
    name='RepoLens.app',
    icon=None,
    bundle_identifier='com.repolens.app',
    info_plist={
        'CFBundleName': 'RepoLens',
        'CFBundleDisplayName': 'RepoLens',
        'CFBundleIdentifier': 'com.repolens.app',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': 'True',
    },
)
