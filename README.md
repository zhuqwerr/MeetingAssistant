# MeetingAssistant

> 让讨论留下重点。

MeetingAssistant 是一款面向 Windows 的本地会议助手。它可以同时采集麦克风与系统声音，使用 Whisper 在本机持续转写，并在会议进行过程中维护一份不断更新的完整纪要。用户可以随时查看讨论脉络、决策结论、待办事项和 AI 建议，也可以直接向当前会议提问，并跳回对应原话核对。

项目采用本地服务与浏览器工作台的形式运行，目前不是 Electron/Tauri 安装包。语音默认留在本机处理；摘要既可连接本地 Ollama，也可使用兼容 Chat Completions 的 API。项目参考了 Meetily 的本地录音理念与 MeetingBro 的增量摘要设计，但不依赖它们的运行环境。

## 项目亮点

| 能力 | 说明 |
| --- | --- |
| 本地实时转写 | 基于 faster-whisper / CTranslate2，在本机完成语音识别；支持麦克风、Windows 系统声音以及混合采集。 |
| 累计式实时总结 | AI 根据新增转写持续更新同一份会议状态，而不是生成彼此割裂的摘要片段；会议结束时会补齐尾音并完成最终总结。 |
| 结构化会议纪要 | 自动整理即时摘要、讨论脉络、决策结论、待办事项和 AI 建议，未明确的负责人或时间不会被模型擅自补全。 |
| AI 自动命名 | 未填写名称的会议会在内容足够后，根据实际讨论主题生成简短标题。 |
| 每条结论可追溯 | 摘要、待办和建议可以引用转写片段，点击“查看原文”即可跳到对应时间与文字。 |
| 面向会议的 AI 问答 | “问问 AI”结合当前纪要和相关转写回答问题；证据不足时明确说明，并给出可核对的来源。 |
| 本地优先与隐私保护 | 原始音频、转写、纪要和历史记录保存在本机；API 密钥在 Windows 上使用 DPAPI 加密，前端无法读取。 |
| 可靠的增量处理 | 转写与总结彼此独立；摘要服务失败时继续录音并保留上一版结果，恢复后可重试，不会用空结果覆盖已有纪要。 |
| 专注的单屏工作区 | 桌面端将录音控制、会议分析和实时转写集中在一个视口内，长内容只在各自面板中滚动。 |

适合线上会议、线下访谈、需求讨论、项目复盘和课程记录等需要边听、边记、边梳理的场景。

## 启动

本机已经安装依赖并完成构建后，在 PowerShell 中运行：

```powershell
cd E:\workspace\MeetingAssistant
.\start.ps1
```

打开 <http://127.0.0.1:8766>。服务只监听本机。结束会议后等待“会议已结束”再关闭服务；关闭浏览器不会结束后台录音，可重新打开页面恢复。Ctrl+C 退出服务。

在新电脑安装：Python 3.12（含 `py` 启动器）、Node.js 20+，然后运行 `powershell -ExecutionPolicy Bypass -File .\install.ps1`。执行策略受限时启动也可使用 `powershell -ExecutionPolicy Bypass -File .\start.ps1`，不修改全局执行策略。

## 第一次使用

1. 打开“设置”，选择本地转写模型。默认 Small / CPU；安装 CUDA 可选依赖且驱动探测确认有支持 float16、至少 4GB 显存的 NVIDIA 设备后，新配置默认 Large v3 Turbo / CUDA。探测失败或超时使用 CPU。已保存的配置不会被改写；探测不保证运行时显存始终充足。Tiny 可用于快速验证。首次使用从 Hugging Face 下载模型，缓存到 `models/`。可先点击“保存并准备模型”。未准备好时不会开始采集。
2. 配置摘要服务并点击“测试摘要连接”：
   - **本地 Ollama**：先安装并启动 Ollama，再运行 `ollama pull qwen3:4b`。地址 `http://127.0.0.1:11434`，模型名与本地安装的一致；也可使用你已安装的其他模型。
   - **兼容 API**：填写服务商的 chat completions 根地址（通常以 `/v1` 结尾）、模型名和 API 密钥。应用会追加 `/chat/completions`。只发送文字，不发送音频；是否留存文字由所选服务决定。
3. 选择麦克风、系统声音或两者。线上会议选两者并佩戴耳机；线下会议选麦克风。设备可在“设置 → 音频设备”中指定，默认使用系统默认设备。
4. 输入会议名称、选择中文或英文，点击“开始会议”。确认状态变为“正在录音”后讲话。
5. 左侧四个 Tab 展示即时摘要、关键要点、待办事项、AI 建议；即时摘要同时列出已经形成的决策结论，关键要点按主题呈现讨论脉络，右侧显示转写。默认每 30 秒增量更新；听到关键事件时合并 8 秒内的触发，会议中每 15 分钟校正一次。校正前先补齐尚未总结的转写，失败时保留已成功保存的状态。可点“立即总结”，“查看原文”跳转到引用片段。
6. 点击“结束会议”，等待尾音转写和最终增量摘要完成，再导出 Markdown。右上角时钟图标可打开本机历史记录。
7. “问问 AI”使用当前会议状态与最多 12 条相关转写回答问题。切换会议会清空对话并取消旧请求；对话不入库。旧版摘要仍可查看、跳转和导出，无需重新计算。

**摘要必须连接真实模型。** Ollama 未运行、模型未安装或 API 不可用时，显示明确错误并保留上次摘要；录音和转写独立继续。不会用关键词拼接冒充 AI 总结。

## 当前边界

- “实时”是短段识别：静音处提前提交，连续讲话最多每 4 秒提交一次，再加模型推理、排队耗时。不是逐字流式 ASR，也不保证 1 秒出字。
- 使用 faster-whisper / CTranslate2。CPU 为 int8，CUDA 为 float16。中文术语可在设置里提供提示。短片段边界、人名、噪声和多人重叠仍可能误识别。
- 麦克风与系统声音分别识别并按音频时间排序，来源标签不等于发言人识别；没有回声消除或说话人分离。
- 摘要是累计状态 + 新转写，支持条目来源跳转。15 分钟校正使用最近窗口和当前引用，不等于整场全文重审；仍需核对模型推断。模型遗漏必需状态字段时会拒绝更新，不会自动以空列表覆盖旧内容。没有摘要编辑功能。
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

Windows NVIDIA GPU 可选安装：`.venv/Scripts/python.exe -m pip install -e ".[cuda]"`。安装后，尚未保存过配置时默认使用 Large v3 Turbo 和 CUDA。程序会加载虚拟环境内 NVIDIA 运行库，无需修改系统 PATH。Windows 上 CUDA 识别在独立进程中运行，避免原生库退出时影响会议服务；切换模型会先释放上一份模型。同一模型在 CPU 上慢于实时，不用于当前会议识别。更大模型仍可能误识别或产生幻觉，不能代替人工核对。

模块：`audio.py` 采集/分段，`asr.py` 本地识别，`session.py` 任务生命周期与增量总结，`summary.py` 模型接口，`storage.py` 本地持久化，`app.py` HTTP/SSE，`frontend/src/` React 界面。

## 参考

- [Meetily](https://github.com/Zackriya-Solutions/meetily)
- [MeetingBro 增量摘要](https://github.com/armpro24-blip/MeetingBro/blob/main/app/backend/meetingbro/summarization/llm.py)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Ollama chat API](https://docs.ollama.com/api/chat)
- [SoundCard](https://soundcard.readthedocs.io/en/latest/)
