# MeetingAssistant

> 让讨论留下重点。

MeetingAssistant 是 Windows 会议记录工具，可采集麦克风、系统声音或两者混合，在本机实时转写，并持续整理会议摘要、讨论要点、决策、待办与 AI 建议。用户也可以针对当前会议提问，并跳回对应转写核对依据。

桌面版使用 Electron 承载现有 React 工作台，由本地服务处理音频、转写和会议数据。用户可选择 **Whisper Base、Small 或 Medium**，模型在应用内按需下载，并使用 CPU int8 转写；摘要、建议与问答只使用用户配置的 Chat Completions 兼容 API。没有 API 配置时，录音、转写、历史记录和导出仍可用。

## 项目亮点

| 能力 | 说明 |
| --- | --- |
| 单应用安装 | Windows 安装包包含 Electron、本地服务和前端；普通用户无需另装 Python、Node.js 或 Ollama。Whisper 模型在应用内按需下载。 |
| 三档本地转写 | 可选 Whisper Base（约 148 MB）、Small（约 486 MB）或 Medium（约 1.53 GB），均使用 CPU int8，不要求 NVIDIA GPU。支持麦克风、Windows 系统声音和混合采集。 |
| 累计式实时总结 | 根据新增转写持续更新同一份会议状态；API 失败时保留上一版摘要，录音与转写继续运行。 |
| 可核对的会议纪要 | 摘要、要点、决策和待办可以引用转写片段，点击来源即可跳回原话。 |
| 会议问答 | 根据当前纪要和相关转写回答问题；证据不足时明确说明。 |
| 本地优先 | 原始音频、转写和历史记录保存在本机。只将会议文字发送到用户配置的 API；Windows 下密钥使用 DPAPI 加密保存。 |
| 单屏工作区 | 录音控制、会议分析和实时转写集中在一个页面，长内容在各自面板中滚动。 |

## Windows 桌面版

普通用户安装发布的 `MeetingAssistant Setup.exe` 后即可启动。会议音频和历史记录默认保存在：

```text
%LOCALAPPDATA%\MeetingAssistant\data
```

首次使用前，在“设置”选择 Base、Small 或 Medium 并下载；模型各自下载一次，存放在 `%LOCALAPPDATA%\MeetingAssistant\models`，可以查看下载进度，失败后重试。较小模型下载快、占用资源少，Medium 通常更准确但 CPU 和内存要求更高。下载完成后语音在本机 CPU 上识别。摘要 API 是可选配置：在“设置”填写兼容 Chat Completions 的 API 地址、模型名和密钥；DeepSeek 的灰色示例为 `https://api.deepseek.com` 和 `deepseek-flash`，需要用户自行填写 API 密钥后使用。默认每 2 分钟更新一次，间隔可在设置中按整分钟调整（1–60 分钟）。未配置 API 时仍可以录音和转写。

> 安装包不携带 Whisper 权重。首次下载所选模型需要网络连接；应用安装、会议数据和模型分开存放。

### 从源码构建桌面安装包

构建机需要 Windows x64、Python 3.12、Node.js 20+ 和 npm。PowerShell 中运行：

```powershell
.\install.ps1
.\scripts\build-desktop.ps1
```

构建脚本用 PyInstaller 构建本地服务，并输出不含语音权重的 NSIS 安装包到 `release/`。最终安装包不依赖构建机上的 Python 或 Node.js。

Electron 开发运行：

```powershell
cd frontend
npm run desktop:dev
```

该命令会直接启动 Electron、Vite 热更新服务器和本地后端；修改前端 TSX/CSS 后会自动刷新，不需要重新构建。修改 Electron 主进程或 Python 后端后，需要重启应用。

### 浏览器开发模式

浏览器入口保留用于本地开发。需要 Python 3.12 和 Node.js 20+：

```powershell
.\install.ps1
.\start.ps1
```

服务只监听 `127.0.0.1:8766`。浏览器关闭不会结束录音；重新打开页面可恢复当前会议。模型下载到本机用户数据目录；已有的固定版本 Medium 缓存仍会被识别并复用。

## 隐私和数据

| 位置 | 内容 |
| --- | --- |
| `%LOCALAPPDATA%\MeetingAssistant\data\meetings.sqlite3` | 会议、转写和摘要快照 |
| `%LOCALAPPDATA%\MeetingAssistant\data\recordings\<会议ID>` | 麦克风和系统声音 WAV |
| `%LOCALAPPDATA%\MeetingAssistant\data\settings.json` | API 地址、模型名、术语和加密后的 API 密钥 |
| `%LOCALAPPDATA%\MeetingAssistant\models\whisper-{base,small,medium}` | 按需下载的固定版本 Whisper 模型 |
| `%LOCALAPPDATA%\MeetingAssistant\logs\backend.log` | 本地服务诊断日志 |

录音 WAV 和会议文字未加密，按当前 Windows 用户的文件权限保存在本机。摘要、建议和问答会把相关转写文字发送给所配置的 API；原始音频不会发送。卸载应用默认保留用户数据。

## 性能与已知边界

- Whisper 的转写按停顿或最长约 8 秒的音频段更新，不是逐字流式模型。CPU 性能因设备和所选模型而异；Medium 在参考机器上的 P0 基准已覆盖单路 10 分钟音频，混合采集及内存、识别质量门槛仍需完成全部验收，详见 [P0 结果](docs/whisper-medium-p0-results.md)。
- 麦克风和系统声音分别转写后按时间排序；来源标签不代表说话人身份。多人重叠、噪声、人名和专业术语仍可能识别错误。
- 识别队列过载时应用会停止采集并保留 WAV，避免静默丢失音频。
- 摘要 API 离线、超时或返回格式无效时，录音不会停止，已保存摘要不会被空结果覆盖。
- Windows 系统声音采集使用 WASAPI loopback；其他平台目前不在第一版范围。

## 开发验证

```powershell
.venv/Scripts/python.exe -m pytest -q
cd frontend
npm run build
```

对本地音频样本运行 Whisper 冒烟识别（默认 Medium，可选 `base`、`small` 或 `medium`）：

```powershell
cd ..
.venv/Scripts/python.exe scripts/smoke_asr.py <音频路径> --language zh --model small
```

固定模型仓库为 `Systran/faster-whisper-{base,small,medium}`，应用按固定修订版本下载模型文件并校验文件大小。依赖锁定在 `requirements-lock.txt`；运行端只包含 CPU 识别所需依赖，不包含 FunASR、PyTorch 或 CUDA。

模块：`meeting_assistant/audio.py` 负责采集与分段，`asr.py` 负责 Whisper 识别，`session.py` 管理会议任务与增量摘要，`summary.py` 实现 Chat Completions 请求，`storage.py` 持久化，`app.py` 提供本地 HTTP/SSE，`frontend/src/` 是 React 工作台，`frontend/desktop/` 管理 Electron 进程。

## 参考

- [Meetily](https://github.com/Zackriya-Solutions/meetily)
- [MeetingBro 增量摘要](https://github.com/armpro24-blip/MeetingBro/blob/main/app/backend/meetingbro/summarization/llm.py)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [SoundCard](https://soundcard.readthedocs.io/en/latest/)
