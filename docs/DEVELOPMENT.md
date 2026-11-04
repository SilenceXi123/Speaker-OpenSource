# 开发、验证与打包

本公开基线为 Speaker 2.0.19（2026-11-04）。先完成根目录 README 的 Python 环境、npm 依赖及 `.env` 配置。不要提交 `.env`、数据库、日志、音视频或厂商凭据。

## 运行与验证

```bash
bash script/build_and_run.sh --start
# http://127.0.0.1:14173/；隔离后端 18787
bash script/build_and_run.sh --stop

(cd backend && venv/bin/python -m pytest -q)
node --test scripts/tests/*.test.mjs
node scripts/check-ui-surface.mjs
npm run release:check
```

修改 `src/frontend/` 后执行 `npm run desktop:web`。根目录 `app.js`、`workbench.css` 与 `desktop-web/` 是生成物，不直接修改。

后端使用 SQLite，本地任务与媒体有保留期及可用空间保护。测试若被低磁盘空间保护拦截，应先检查真实环境，不降低生产保护阈值来凑通过。

## macOS 客户端

需要 Node.js、Rust（含 `aarch64-apple-darwin` 和 `x86_64-apple-darwin` 目标）、完整 Xcode 及支持 macOS 26 SDK 的 Swift 编译器。Tauri 通用前置条件见[官方说明](https://v2.tauri.app/start/prerequisites/)；本项目额外的 macOS 26 SDK 要求来自 Apple SpeechAnalyzer 桥接构建。主应用最低运行版本仍为 macOS 12，原生识别助手只在兼容设备启用。

离线字幕核心使用现有的 `scripts/mac/build_offline_core.py`，构建依赖 **PyInstaller**（GPL 加发布例外），只用于打包，不是服务器常驻依赖。它将 Python 字幕处理和字典数据封装为客户端资源，不附模型权重；输出架构取决于所用 Python 环境。当前公开包使用 arm64 核心。

```bash
# 安装仅用于构建的工具，保持服务器依赖不变
backend/venv/bin/python -m pip install pyinstaller
python3 scripts/mac/build_offline_core.py \
  --python backend/venv/bin/python --pyinstaller-path backend/venv/lib/python3.12/site-packages

# 默认本机 8787；其他团队服务器可在构建时指定该变量
SPEAKER_API_BASE=http://127.0.0.1:8787/api/v1 npm run tauri:dev
SPEAKER_API_BASE=http://127.0.0.1:8787/api/v1 npm run tauri:build:universal
```

标准产物为 `dist/Speaker客户端.zip`。构建会生成 Swift 识别助手；离线核心必须提前构建。非 macOS 桌面打包未验收，网页及后端可独立运行。

正式发行需使用自己的签名、公证和更新公钥；公开测试配置关闭自动更新制品，不携带原内部更新公钥或更新地址；应用标识为 `com.silence.speaker-open-source`，与原团队客户端的数据目录分开。不要把测试包宣称为 Apple 已公证的软件。

## 公开发行约定

- 开源仓库独立于原私有运维历史，从脱敏源码快照开始。
- 安装包放 GitHub Release，不提交进 Git；附版本、构建标记、SHA256 与已验证范围。
- 源码不附 `.env`、用户数据、生产地址、云模型权重、私有回滚包或内网部署记录。
- 每次客户端资源变更需重新生成前端并打包；后端业务变更需运行相关回归，列明部署相对路径及重启要求。
- 问题报告附脱敏复现信息；贡献范围优先覆盖真实后期流程，不把项目过滤当成完整鉴权。
