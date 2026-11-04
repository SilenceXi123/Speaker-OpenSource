# Speaker

**把节目音频变成可校对、可交付到 Premiere 的说话人字幕。**

Speaker 是面向影视后期团队的开源工具，包含 macOS 客户端、FastAPI 后端和 JSON CLI。它把转写、说话人校对、台本核对、人名条匹配及字幕导出放在同一工作流中，也可独立用于扒词和声音复刻。

[下载测试版](https://github.com/SilenceXi123/Speaker-OpenSource/releases) · [使用说明](docs/USAGE.md) · [开发与构建](docs/DEVELOPMENT.md) · [MIT 许可证](LICENSE)

## 功能

| 能力 | 可以完成的工作 |
| --- | --- |
| 项目工作区 | 按节目组织任务、台本、词库、声音及历史记录。 |
| 云端转写 | 通过阿里云 Fun-ASR 转写节目音频并区分说话人；可指定人数、语言提示与词表。 |
| 本机转写 | 在兼容的 macOS 26 设备上选择 Apple SpeechAnalyzer；断网时明显提示离线模式，可在本机保存、编辑和导出。 |
| 说话人校对 | 音频试听、批量命名、逐句改说话人、改字及主动清理语气词。 |
| 台本与字幕质检 | 导入 TXT、Markdown、SRT、DOCX、PDF 台本；结合时长、断句、疑似异常和参考文本辅助复核。可选 DeepSeek 云端校对。 |
| 人名条与 Premiere 交付 | 匹配同名 PNG，导出 Premiere 图形字幕 XML、带姓名/纯字幕 SRT，以及工程包。 |
| 扒词 | 导出带时间与人物信息的 TXT、SRT、DOCX，供划稿、编导和剪辑使用。 |
| 声音复刻 | 上传本次参考音频，自选 Qwen-Audio 3.1 TTS Next 或 CosyVoice 3.5 Flash，输入语气提示，生成多个版本并试听。 |
| 下载后整批清理 | 成功保存选定声音版本后，清理同项目、同参考音频关联的参考、临时音色和所有生成版本；取消保存/试听不清理，未下载数据以 72 小时到期兜底。 |
| 声纹辅助 | 声纹样本只服务说话人识别，与声音复刻参考音频分开；识别引擎需另行配置。 |
| CLI | 创建项目、提交/轮询任务、长音频自动分段合并，以及完全离线的 SRT → Premiere XML 转换。 |

本项目可以独立使用。目前对外确认的剪辑交付方式是 XML/SRT 等文件；尚未实现 Premiere 修改时间线后自动回传同步，也没有声称本机识别具备多人说话人分离。

## 下载与运行

当前公开测试版为 **2.0.19**，在 [Releases](https://github.com/SilenceXi123/Speaker-OpenSource/releases) 下载 `Speaker-2.0.19-macOS12-universal-open-source-test.zip`，解压后将应用放入“应用程序”。

- 客户端面向 **macOS 12+，Intel / Apple Silicon**。测试包为临时签名，尚未经过 Apple 公证；仅在信任本仓库并核对 SHA256 后，通过系统“隐私与安全性”批准打开。
- 公开包默认连接 **`http://127.0.0.1:8787/api/v1`**，不含原团队内网地址、API Key 或可共享使用的云端服务。
- 云端转写、校对、声音复刻需要自行启动后端并配置对应服务；模型、OSS 等费用由使用者向厂商支付。
- Apple 本机识别需要 **macOS 26、受支持的语言/硬件及系统识别资源**。客户端检查能力；本次公开包附带的离线字幕工作程序为 **arm64**，因此不能将 Intel 客户端可启动理解为 Intel 离线识别已验收。

## 快速开始：自己运行后端

准备 **Python 3.12、Node.js 20+、FFmpeg**。以下命令适用于 macOS/Linux 开发环境；桌面安装包的构建另见[构建说明](docs/DEVELOPMENT.md)。

```bash
git clone https://github.com/SilenceXi123/Speaker-OpenSource.git
cd Speaker-OpenSource
python3.12 -m venv backend/venv
backend/venv/bin/python -m pip install -r backend/requirements.txt
npm ci
cp .env.example .env
```

按 `.env` 中注释填写自己的配置，不需要的能力可保持关闭：

| 功能 | 主要配置 |
| --- | --- |
| 云端转写 | `DASHSCOPE_API_KEY`、`DASHSCOPE_ASR_MODEL`，以及 OSS 区域、桶、访问密钥和可供云模型读取的地址。 |
| 云端校对 | `SUBTITLE_ENHANCER_ENABLED=true`，配置 `SUBTITLE_ENHANCER_PROVIDER/API_KEY/BASE_URL/MODEL`。模型名需为账号当前可用模型。 |
| Next 声音复刻 | `VOICE_CLONING_ENABLED=true`、`DASHSCOPE_API_KEY`、`SFM_WORKSPACE_ID`、OSS；业务空间 ID 与 API Key 列表编号不同。 |
| Flash 声音复刻 | `VOICE_CLONING_ENABLED=true`、`DASHSCOPE_API_KEY`、OSS，并开通对应模型。 |
| 声纹辅助 | 默认关闭；`VOICEPRINT_PROVIDER` 和外部识别引擎按需安装，不包含模型权重。 |

从仓库根目录启动：

```bash
backend/venv/bin/python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8787
```

浏览器访问 `http://127.0.0.1:8787/api/v1/health` 检查服务，再启动客户端。首次会初始化本机数据库；不会自动获得云服务账号。

后端面向**可信本机或受控团队内网**，普通项目/任务接口尚无完整的多租户身份鉴权。不要直接暴露到公网；团队部署应配置受控网络和入口认证，详见[使用说明](docs/USAGE.md#数据与部署边界)。

没有 Xcode 或不构建客户端时，也可启动隔离的本机网页 Alpha：

```bash
bash script/build_and_run.sh --start
# 页面：http://127.0.0.1:14173/  API：http://127.0.0.1:18787
bash script/build_and_run.sh --stop
```

网页模式使用云端转写，不提供 Apple 原生识别桥接。隔离 Alpha 的数据目录为 `backend/data/alpha-v2/`，与默认后端数据库分开。

## 日常操作

1. 创建或选择节目项目，进入“生成字幕”或“生成扒词”。
2. 上传音频，选择云端/可用的本机识别；按需附加台本、人数等提示，创建任务。
3. 到校对页试听、改字、命名及逐句调整说话人。台本只是纠错证据；歌词替换需明确开启并可靠对齐。
4. 按人物姓名匹配同名 PNG，导出 Premiere XML、SRT 或扒词文稿。
5. 在 Premiere 导入 XML 后，继续精修图形文字与人名条；保留媒体和图片的有效路径。

更多内容见[使用说明](docs/USAGE.md)，包括长音频、声音复刻、离线模式和失败排查。

## CLI / 自动化

CLI 使用同一个后端；标准输出返回 JSON，失败返回非零退出码。默认 API 为本机 Alpha `http://127.0.0.1:18787`；连接上述 8787 后端时显式指定：

```bash
./speaker-cli --api-base http://127.0.0.1:8787 project list
./speaker-cli --api-base http://127.0.0.1:8787 project create --name "节目测试"
./speaker-cli --api-base http://127.0.0.1:8787 task create \
  --project-id PROJECT_ID --file /绝对路径/节目.wav --kind subtitle --wait
./speaker-cli --api-base http://127.0.0.1:8787 task create \
  --project-id PROJECT_ID --file /绝对路径/长录音.wav --auto-split --wait
./speaker-cli subtitle srt-to-xml \
  --input /绝对路径/字幕.srt --output /绝对路径/字幕.xml --frame-rate 25
```

SRT 转 XML 完全离线，保留文字和时间轴、按目标帧率对齐；冲突或重叠 cue 会明确报错，默认不覆盖已有文件。CLI 在线功能按可信环境提供，不等于已有远程用户鉴权或跨软件直连接口。

## 技术与目录

```text
src/frontend/       原生 JavaScript / CSS 页面源码
src-tauri/          Tauri 2 / Rust 客户端、Swift SpeechAnalyzer 桥接
backend/app/        FastAPI、SQLite、字幕流水线、模型适配与文件管理
backend/scripts/    JSON CLI、离线字幕工作程序及维护命令
scripts/            前端生成、检查、测试及 macOS 离线核心构建
script/             隔离本机 Alpha 启停
```

云端模型可用性与授权随厂商调整；代码中的兼容适配器不代表所有历史模型仍可使用。公开目录目前推荐 Next，可选 Flash。

## 反馈、贡献与许可证

在 [Issues](https://github.com/SilenceXi123/Speaker-OpenSource/issues) 提交系统版本、客户端版本、操作步骤及脱敏报错。请勿上传 API Key、真实节目音频、用户数据库或未脱敏日志。提交代码前参考[开发与验证说明](docs/DEVELOPMENT.md)。

本项目自有源码采用 [MIT](LICENSE)，版权归 SilenceXi123。第三方库、Apple 框架、外部模型、字体及用户素材仍遵守各自条款，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
