import { useState } from 'react';
import { Check, LoaderCircle } from 'lucide-react';
import { api } from './api';
import { Modal } from './Modal';
import type { Devices, Health, Settings, WhisperModelName } from './types';

const whisperModels: { id: WhisperModelName; label: string; size: string; description: string }[] = [
  { id: 'base', label: 'Base', size: '约 148 MB', description: '体积小、启动快，识别准确度相对较低' },
  { id: 'small', label: 'Small', size: '约 486 MB', description: '速度与准确度较均衡' },
  { id: 'medium', label: 'Medium', size: '约 1.53 GB', description: '准确度更高，对 CPU 和内存要求也更高' },
];

export function SettingsDialog({ settings, health, devices, microphone, speaker, setDevices, onSaved, onClose }: {
  settings: Settings; health: Health | null; devices: Devices | null; microphone: string; speaker: string;
  setDevices: (mic: string, speaker: string) => void; onSaved: (settings: Settings) => void; onClose: () => void;
}) {
  const [draft, setDraft] = useState(settings);
  const [draftMicrophone, setDraftMicrophone] = useState(microphone);
  const [draftSpeaker, setDraftSpeaker] = useState(speaker);
  const [key, setKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const update = (patch: Partial<Settings>) => { setDraft(current => ({ ...current, ...patch })); setMessage(''); };
  const body = () => ({ ...draft, api_key: clearKey ? '' : key.trim() || undefined });
  const act = async (kind: 'test' | 'save') => {
    setBusy(kind); setError(''); setMessage('');
    try {
      if (kind === 'test') {
        await api('/settings/test', 'POST', body());
        setMessage('摘要 API 连接成功');
      } else {
        const saved = await api<Settings>('/settings', 'PUT', body());
        onSaved(saved);
        setDevices(draftMicrophone, draftSpeaker);
        onClose();
      }
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(''); }
  };
  const downloadModel = async () => {
    setBusy('download'); setError(''); setMessage('');
    try {
      await api('/model/download', 'POST', { model: draft.asr_model });
      setMessage(`已开始下载 Whisper ${modelLabel}；可以在此查看进度。`);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(''); }
  };
  const model = health?.model.models.find(item => item.id === draft.asr_model);
  const activeDownload = health?.model.models.find(item => item.status === 'downloading');
  const modelLabel = whisperModels.find(item => item.id === draft.asr_model)?.label ?? 'Medium';
  const selectedModel = whisperModels.find(item => item.id === draft.asr_model) ?? whisperModels[2];
  const formatBytes = (bytes: number) => bytes >= 1_000_000_000
    ? `${(bytes / 1_000_000_000).toFixed(2)} GB`
    : `${Math.round(bytes / 1_000_000)} MB`;

  return <Modal title="设置" onClose={onClose}>
    <div className="settings-body">
      <section>
        <h3>本地语音转写</h3>
        <label>Whisper 模型<select value={draft.asr_model} onChange={event => update({ asr_model: event.target.value as WhisperModelName })}>{whisperModels.map(option => {
          const status = health?.model.models.find(item => item.id === option.id);
          return <option key={option.id} value={option.id}>{option.label} · {option.size}{status?.installed ? ' · 已下载' : ''}</option>;
        })}</select></label>
        <div className="model-summary"><strong>Whisper {modelLabel}</strong><span>本地 CPU int8 · {selectedModel.size}</span></div>
        <p className="helper">{selectedModel.description}。语音在本机识别，模型不随安装包提供。可分别下载这三种模型，模型文件只保存在本机。</p>
        {model?.installed ? <p className="success-text">{settings.asr_model === draft.asr_model ? '模型已下载，可以开始会议。' : '模型已下载；保存设置后将用于后续会议。'}</p> : <button className="secondary small" disabled={!!busy || model?.status === 'downloading' || (!!activeDownload && activeDownload.id !== draft.asr_model)} onClick={() => void downloadModel()}>{model?.status === 'downloading' ? `下载中 ${model.progress}%` : activeDownload && activeDownload.id !== draft.asr_model ? `Whisper ${activeDownload.name.replace('Whisper ', '')} 正在下载` : model?.status === 'download_error' ? '重试下载模型' : busy === 'download' ? '正在启动下载…' : `下载 Whisper ${modelLabel}（${selectedModel.size}）`}</button>}
        {model && !model.installed && model.status === 'downloading' && <div className="model-download-progress" role="progressbar" aria-label={`Whisper ${modelLabel} 下载进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={model.progress}><div style={{ width: `${model.progress}%` }}/></div>}
        {model && !model.installed && model.status === 'downloading' && <p className="helper">已下载 {formatBytes(model.downloaded_bytes)} / {formatBytes(model.total_bytes)}。下载可续传，请保持网络连接。</p>}
        {activeDownload && activeDownload.id !== draft.asr_model && <p className="helper">Whisper {activeDownload.name.replace('Whisper ', '')} 正在下载：{activeDownload.progress}%。</p>}
        {model?.error && <p className="error-text">{model.error}</p>}
        <label>会议术语<textarea rows={2} placeholder="例如：项目名称、人名、专业术语，用逗号分隔" maxLength={1500} value={draft.vocabulary} onChange={event => update({ vocabulary: event.target.value })}/></label>
        {health?.asr.error && <p className="error-text">{health.asr.error}</p>}
      </section>

      <section>
        <h3>实时总结</h3>
        <p className="helper">填写兼容 Chat Completions 的 API。只会发送转写文字，原始录音留在本机；不配置时仍可录音和转写。</p>
        <label>API 地址<input type="url" value={draft.summary_url} onChange={event => update({ summary_url: event.target.value })} placeholder="https://api.deepseek.com" autoComplete="url"/></label>
        <div className="form-grid">
          <label>模型名称<input value={draft.summary_model} onChange={event => update({ summary_model: event.target.value })} placeholder="deepseek-flash" autoComplete="off"/></label>
          <label>更新间隔（分钟）<input type="number" min={1} max={60} step={1} value={Math.round(draft.summary_interval / 60)} onChange={event => update({ summary_interval: Number(event.target.value) * 60 })}/></label>
        </div>
        <p className="helper">每 1–60 分钟更新一次；默认 2 分钟。关键讨论结论仍可提前触发摘要。</p>
        <label>API 密钥<input type="password" autoComplete="new-password" value={key} onChange={event => { setKey(event.target.value); setClearKey(false); }} placeholder={settings.has_api_key ? '已保存，留空保持原密钥' : '可选：输入服务商提供的密钥'}/></label>
        {settings.has_api_key && <label className="check-label"><input type="checkbox" checked={clearKey} onChange={event => setClearKey(event.target.checked)}/>清除已保存密钥</label>}
        <p className="helper">Windows 下密钥使用当前账户加密保存，不会返回前端或写入浏览器存储。</p>
        <button className="secondary small" disabled={!!busy || !draft.summary_url || !draft.summary_model} onClick={() => void act('test')}>
          {busy === 'test' ? <LoaderCircle className="spin" size={15}/> : <Check size={15}/>}测试 API 连接
        </button>
      </section>

      <details>
        <summary>音频设备</summary>
        <label>麦克风<select value={draftMicrophone} onChange={event => setDraftMicrophone(event.target.value)}><option value="">系统默认麦克风</option>{devices?.microphones.map(device => <option key={device.id} value={device.id}>{device.name}</option>)}</select></label>
        <label>系统声音输出设备<select value={draftSpeaker} onChange={event => setDraftSpeaker(event.target.value)}><option value="">系统默认输出</option>{devices?.speakers.map(device => <option key={device.id} value={device.id}>{device.name}</option>)}</select></label>
        <p className="helper">同时录制麦克风和系统声音时，建议佩戴耳机，减少重复收音。</p>
      </details>
      {message && <p role="status" className="success-text">{message}</p>}
      {error && <p role="alert" className="error-text">{error}</p>}
    </div>
    <div className="modal-footer"><button className="secondary" onClick={onClose}>取消</button><button className="primary" disabled={!!busy || !!activeDownload} onClick={() => void act('save')}>{busy === 'save' ? '保存中…' : activeDownload ? '模型下载完成后可保存设置' : '保存设置'}</button></div>
  </Modal>;
}
