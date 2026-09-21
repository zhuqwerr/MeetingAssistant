# MeetingAssistant

第一版：**本地语音实时转写 + 实时会议总结**。Windows 本地服务，浏览器工作台；不是 Electron/Tauri 安装包。独立项目，参考 Meetily 的本地录音理念与 MeetingBro 的增量摘要设计，未依赖这两个项目的运行环境。

## 启动

本机已经安装依赖并完成构建后，在 PowerShell 中运行：

```powershell
cd E:\workspace\MeetingAssistant
.\start.ps1
```

打开 <http://127.0.0.1:8766>。服务只监听本机。结束会议后等待“会议已结束”再关闭服务；关闭浏览器不会结束后台录音，可重新打开页面恢复。Ctrl+C 退出服务。

在新电脑安装：Python 3.12（含 `py` 启动器）、Node.js 20+，然后运行 `powershell -ExecutionPolicy Bypass -File .\install.ps1`。执行策略受限时启动也可使用 `powershell -ExecutionPolicy Bypass -File .\start.ps1`，不修改全局执行策略。

## 第一次使用

1. 打开“设置”，选择本地转写模型。默认 Small / CPU；Tiny 可用于快速验证。首次使用从 Hugging Face 下载模型，缓存到 `models/`。可先点击“保存并准备模型”。未准备好时不会开始采集。
2. 配置摘要服务并点击“测试摘要连接”：
   - **本地 Ollama**：先安装并启动 Ollama，再运行 `ollama pull qwen3:4b`。地址 `http://127.0.0.1:11434`，模型名与本地安装的一致；也可使用你已安装的其他模型。
   - **兼容 API**：填写服务商的 chat completions 根地址（通常以 `/v1` 结尾）、模型名和 API 密钥。应用会追加 `/chat/completions`。只发送文字，不发送音频；是否留存文字由所选服务决定。
3. 选择麦克风、系统声音或两者。线上会议选两者并佩戴耳机；线下会议选麦克风。设备可在“设置 → 音频设备”中指定，默认使用系统默认设备。
4. 输入会议名称、选择中文或英文，点击“开始会议”。确认状态变为“正在录音”后讲话。
5. 左侧持续显示带时间的转写；右侧每 30 秒有新增内容时更新讨论要点、决策、待办。可在设置中调整频率，或点“立即总结”。“原文”跳转到引用片段。
6. 点击“结束会议”，等待尾音转写和最终增量摘要完成，再导出 Markdown。右上角时钟图标可打开本机历史记录。

**摘要必须连接真实模型。** Ollama 未运行、模型未安装或 API 不可用时，显示明确错误并保留上次摘要；录音和转写独立继续。不会用关键词拼接冒充 AI 总结。

## 第一版边界

- “实时”是短段识别：静音处提前提交，连续讲话最多每 4 秒提交一次，再加模型推理、排队耗时。不是逐字流式 ASR，也不保证 1 秒出字。
- 使用 faster-whisper / CTranslate2，默认 CPU int8；中文术语可在设置里提供提示。短片段边界、人名、噪声和多人重叠仍可能误识别。
- 麦克风与系统声音分别识别并按音频时间排序，来源标签不等于发言人识别；第一版没有回声消除、说话人分离或提问辅助。
- 摘要是累计状态 + 新转写，支持条目来源跳转；引用表示输入证据，仍需人工核对模型推断。第一版没有全文会后重审、自动撤销已压缩错误或摘要编辑。
- 声音始终保存为本地 WAV。遇到识别队列过载会明确停止采集并提示，原始音频仍保留；第一版未提供音频文件重新导入界面。
- Windows 系统声音使用 WASAPI loopback；跨平台系统声音尚未实现。CUDA 需要匹配的 CUDA 12 / cuDNN 9 运行库；CPU 路径不需要。

## 文件与隐私

| 位置 | 内容 |
| --- | --- |
| `data/meetings.sqlite3` | 会议、转写、摘要快照 |
| `data/recordings/<会议ID>/mic.wav` | 麦克风原始音频（单声道 16kHz） |
| `data/recordings/<会议ID>/system.wav` | 系统声音原始音频 |
| `data/settings.json` | 模型配置及 Windows DPAPI 加密后的 API 密钥 |
| `models/` | 下载的本地语音模型 |

密钥不返回前端或写入浏览器存储。Windows 密钥绑定当前账户；非 Windows 环境仅在进程内保留密钥，可用 `MEETING_ASSISTANT_API_KEY` 环境变量提供。会议内容和 WAV 未加密，按本机文件权限保存。首次下载模型需要网络；本地模型已缓存且使用本地 Ollama 时，会议处理无需云端服务。

## 开发与测试

```powershell
# 后端（生产界面由同一服务提供）
.venv/Scripts/python.exe -m uvicorn meeting_assistant.app:create_app --factory --host 127.0.0.1 --port 8766
# 开发界面（另一个终端）
cd frontend
npm run dev
# http://127.0.0.1:5179，/api 代理到本机 8766
```

```powershell
.venv/Scripts/python.exe -m pytest -q
cd frontend
npm run build
```

真实语音模型验证：`.venv/Scripts/python.exe scripts/smoke_asr.py <音频路径> --model small --language zh`。单元/集成测试使用受控模型替身验证调度和协议，不代表真实识别或摘要质量。

模块：`audio.py` 采集/分段，`asr.py` 本地识别，`session.py` 任务生命周期与增量总结，`summary.py` 模型接口，`storage.py` 本地持久化，`app.py` HTTP/SSE，`frontend/src/` React 界面。

## 参考

- [Meetily](https://github.com/Zackriya-Solutions/meetily)
- [MeetingBro 增量摘要](https://github.com/armpro24-blip/MeetingBro/blob/main/app/backend/meetingbro/summarization/llm.py)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Ollama chat API](https://docs.ollama.com/api/chat)
- [SoundCard](https://soundcard.readthedocs.io/en/latest/)
