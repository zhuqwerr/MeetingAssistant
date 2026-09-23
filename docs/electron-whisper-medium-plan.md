# MeetingAssistant Electron 桌面版方案

状态：实施中。已接入 API-only 设置、Whisper Base/Small/Medium 可选转写与按需下载、Electron 主进程和 Windows NSIS 安装包。安装包约 219 MiB，不含模型；48 项后端测试、前端构建和冻结版后端启动/鉴权/关闭冒烟测试通过。实际窗口交互、干净 Windows 安装和首次完整模型下载仍待验收。目标平台：Windows 10/11 x64。

## 目标与范围

用户安装一个 MeetingAssistant 桌面应用后，无需另装 Python、Node.js 或 Ollama，即可使用麦克风和 Windows 系统声音进行本地转写。用户可选择 Base（约 148 MB）、Small（约 486 MB）或 Medium（约 1.53 GB）Whisper 多语言模型并按需下载，仅在 CPU 上以 CTranslate2 `int8` 运行；Medium 为默认选项。会议摘要、AI 建议和问答只调用用户配置的 **兼容 Chat Completions 的 API**；没有 API 配置时，录音、转写、历史记录和导出仍可使用，摘要区域明确提示需要配置。原始音频不发送给摘要 API；用于摘要和问答的会议文字会发送给所配置的服务。

第一版只做 Windows 桌面分发，不做 macOS/Linux、内置本地大语言模型、自动更新、说话人识别或重写现有页面。保留当前 React 界面、FastAPI 接口和 SQLite 数据结构，减少迁移风险。

## 最终运行结构

```text
MeetingAssistant.exe（Electron 主进程）
  ├─ 启动并管理 backend/MeetingAssistantBackend.exe
  │    ├─ FastAPI：录音、Whisper 转写、会议数据、摘要 API、SSE
  │    ├─ %LOCALAPPDATA%\MeetingAssistant\models：按需下载的 CTranslate2 权重
  │    └─ 仅监听 127.0.0.1 的动态端口
  └─ BrowserWindow：加载同一 FastAPI 服务提供的 React 页面
```

继续由 Python 服务提供页面，前端现有的相对路径 `/api`、`EventSource` 和导出链接便可沿用。Electron 主进程只负责单实例、后端进程、窗口、退出收尾及安装路径，不接管音频识别或摘要业务。Python 后端采用 PyInstaller `onedir` 打包；可执行文件作为 Electron 外部资源放在真实文件系统中，模型下载到用户数据目录，避免从 ASAR 虚拟目录执行程序或读取大型权重。

## 需要修改的现有模块

| 范围 | 改造内容 |
| --- | --- |
| `meeting_assistant/asr.py`、`audio.py`、`session.py` | 删除 FunASR/Paraformer/FSMN-VAD/CT-Punc 流式分支，保留 faster-whisper Base/Small/Medium；各模型固定版本，应用内独立下载到用户数据目录后以 CPU `int8` 运行。继续保留无语音过滤、幻觉抑制、句尾收束和过载提示。 |
| `meeting_assistant/config.py`、`summary.py` | 仅保留兼容 Chat Completions 的摘要配置；迁移旧设置时清理 FunASR/Ollama 字段，不覆盖已有 API 地址、模型名和受 DPAPI 保护的密钥。未配置 API 时返回可操作的状态，不影响录音。 |
| `meeting_assistant/launch.py`、`app.py` | 后端支持 Electron 传入的动态端口、模型路径、前端路径、数据路径和本次启动凭据；启动后向主进程报告就绪信息。保留 `start.ps1` 作为开发入口。 |
| `frontend/src/SettingsDialog.tsx`、`types.ts`、`api.ts` | 删除转写引擎和 Ollama 选项；支持选 Whisper 档位、查看各档下载状态和下载进度，同时配置摘要 API 地址、模型名、密钥与连接测试。将缺少 API 的说明与转写故障分开。 |
| 新增 `desktop/` 与打包脚本 | Electron 主进程、Windows 图标、PyInstaller 配置、模型按需下载、打包与安装程序配置。CI/本机构建锁定 Electron、Python 依赖和模型版本。 |

## 启动、退出与安全边界

1. Electron 获取单实例锁，确定资源路径和用户数据路径，然后启动独立的 Python 子进程。后端监听 `127.0.0.1:0`，把实际端口通过受控的标准输出消息告知主进程；主进程等服务就绪后再显示窗口。端口冲突或启动失败时显示桌面错误页；模型缺失时在主页面引导下载。
2. 每次启动生成随机访问凭据。主进程在窗口加载前设置仅限本机服务使用的 HttpOnly Cookie；后端对 `/api`（包括 SSE）验证它，并继续校验 Host/Origin。当前写死 `8766` 的 Origin 规则改为本次实际端口。浏览器窗口禁用 Node 集成、开启上下文隔离与沙箱，并限制导航范围。[Electron 安全清单](https://www.electronjs.org/docs/latest/tutorial/security)。
3. 用户关闭窗口时，若正在录音，先调用现有停止接口，等待尾音转写、最后一次摘要和数据库落盘；完成后结束后端子进程。超时则提示原始 WAV 已保存，并记录可恢复状态。异常退出后下次启动沿用现有中断会议恢复逻辑。
4. 录音、SQLite 和设置放到 `%LOCALAPPDATA%\MeetingAssistant\data`；模型放到 `%LOCALAPPDATA%\MeetingAssistant\models`，日志另设目录。旧开发版 `data/` 提供一次性导入与备份，不自动删除用户原文件。软件升级不得覆盖用户数据。

## 安装包与依赖

- 安装包包含 Electron、PyInstaller `onedir` 后端和 React 构建产物，不携带模型权重。应用从固定修订版本按需下载 `Systran/faster-whisper-base`、`small` 或 `medium` 到用户数据目录；模型约为 148 MB、486 MB 和 1.53 GB。[Base](https://huggingface.co/Systran/faster-whisper-base/tree/main)、[Small](https://huggingface.co/Systran/faster-whisper-small/tree/main)、[Medium](https://huggingface.co/Systran/faster-whisper-medium/tree/main)。
- 从 Python 锁定依赖中移除 FunASR、ModelScope、PyTorch、kaldi-native-fbank 等只为当前 FunASR 链路使用的包，并检查传递依赖。Whisper 保留 faster-whisper/CTranslate2、音频采集和 FastAPI 所需组件。
- 开发模式可使用现有 `.venv`；发布包绝不调用用户机器上的 `python`、`py` 或 `node`；首次下载模型时应用连接模型托管服务，下载后识别可离线运行。先产出 Windows 便携包验证资源路径，再产出单一安装程序。对外发布前完成安装包签名与干净机器验证。

## 性能门槛与验收

Whisper Medium 不是逐字流式模型。当前应用按停顿或最长约 8 秒的音频块进行识别，用户看到的是逐段更新；延迟还包括 CPU 解码时间。不能仅凭“模型能加载”就宣称实时。先在明确记录 CPU、内存的参考 Windows 机器上，用至少 10 分钟的真实中文会议音频测：

- 整体处理速度持续快于音频产生速度，队列不会持续增长或触发过载停止；记录各片段从录到显示的 p50/p95 延迟。
- 麦克风、系统声音、混合采集均可完整转写；长时间运行无音频丢失、进程泄漏或重复句。
- 与当前样本对照检查误识别和无声幻觉；摘要 API 失败时录音继续，恢复后可重试。

不同 CPU 上可按资源和准确率要求选择不同档位；模型选择不会自动改变，性能边界应在设置中说明。

最终在**未安装 Python、Node、Ollama，安装器不含模型**的干净 Windows 环境中验收安装；Base、Small、Medium 分别首次下载时需联网，下载后断网仍应可本地录音、转写、历史记录和导出；接入有效 API 后验收总结与问答。还需覆盖重复启动、占用端口、无声输入、关闭窗口时仍在录音、断网/API 超时、升级后数据保留等情况。

## 实施顺序

1. 固定 Medium 模型版本，跑 CPU 会议样本基准，记录可接受的延迟与内存；同时核对模型和依赖许可。
2. 精简为 Whisper + API 单路径，完成旧设置迁移及现有后端/前端测试。
3. 加入 Electron 进程管理、动态端口、访问凭据、用户数据目录和退出收尾；在开发模式验证。
4. 构建 PyInstaller 后端与 Electron 便携包，解决原生 DLL/资源路径问题；随后制作安装程序，并在干净 Windows 机器做端到端验收。

交付物为安装程序、可复现的构建脚本、更新后的 README、依赖清单与模型下载版本信息、性能验收记录。本文件是实施方案，不代表上述改造已经完成。
