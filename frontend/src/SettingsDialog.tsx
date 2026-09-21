import { useState } from 'react';
import { Check, LoaderCircle, Download } from 'lucide-react';
import { api } from './api';
import { Modal } from './Modal';
import type { Devices, Health, Settings } from './types';

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
  const update = (patch: Partial<Settings>) => { setDraft(d => ({ ...d, ...patch })); setMessage(''); };
  const body = () => ({ ...draft, api_key: clearKey ? '' : key || undefined });
  const act = async (kind: string) => {
    setBusy(kind); setError(''); setMessage('');
    try {
      if (kind === 'test') { await api('/settings/test', 'POST', body()); setMessage('摘要服务连接成功'); }
      else {
        const saved = await api<Settings>('/settings', 'PUT', body()); onSaved(saved); setDevices(draftMicrophone, draftSpeaker);
        if (kind === 'model') { await api('/model/prepare', 'POST'); setMessage('已开始准备语音模型，首次下载需要一些时间。'); }
        else onClose();
      }
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(''); }
  };
  return <Modal title="设置" onClose={onClose}>
    <div className="settings-body">
      <section><h3>本地语音转写</h3><p className="helper">音频在本机识别。首次使用会下载所选模型。</p>
        <div className="form-grid"><label>语音模型<select value={draft.asr_model} onChange={e => update({ asr_model: e.target.value })}>
          <option value="tiny">Tiny · 快速验证</option><option value="base">Base · 轻量</option><option value="small">Small · 默认</option><option value="medium">Medium · 更高准确率</option><option value="large-v3-turbo">Large v3 Turbo</option>
        </select></label><label>计算设备<select value={draft.asr_device} onChange={e => update({ asr_device: e.target.value })}><option value="cpu">CPU · 无需显卡</option><option value="cuda">NVIDIA CUDA · 需 CUDA 12 / cuDNN 9</option></select></label></div>
        <label>会议术语<textarea rows={2} placeholder="例如：项目名称、人名、专业术语，用逗号分隔" maxLength={1500} value={draft.vocabulary} onChange={e => update({ vocabulary: e.target.value })}/></label>
        <button className="secondary small" disabled={!!busy || health?.asr.status === 'loading'} onClick={() => void act('model')}><Download size={15}/>{health?.asr.status === 'loading' ? '模型准备中…' : '保存并准备模型'}</button>
        {health?.asr.error && <p className="error-text">{health.asr.error}</p>}
      </section>
      <section><h3>实时总结</h3><div className="provider-tabs" role="group" aria-label="摘要方式"><button aria-pressed={draft.summary_provider === 'ollama'} onClick={() => update({ summary_provider: 'ollama', summary_url: 'http://127.0.0.1:11434', summary_model: 'qwen3:4b' })}>本地 Ollama</button><button aria-pressed={draft.summary_provider === 'compatible'} onClick={() => update({ summary_provider: 'compatible', summary_url: 'https://api.deepseek.com/v1', summary_model: 'deepseek-chat' })}>兼容 API</button></div>
        <p className="helper">{draft.summary_provider === 'ollama' ? '需先启动 Ollama 并安装对应模型。' : '仅将转写文字发送到你配置的服务，原始音频留在本机。'}</p>
        <label>服务地址<input type="url" value={draft.summary_url} onChange={e => update({ summary_url: e.target.value })}/></label>
        <div className="form-grid"><label>模型名称<input value={draft.summary_model} onChange={e => update({ summary_model: e.target.value })}/></label><label>更新间隔<select value={draft.summary_interval} onChange={e => update({ summary_interval: Number(e.target.value) })}><option value={15}>15 秒</option><option value={30}>30 秒</option><option value={60}>60 秒</option><option value={120}>120 秒</option></select></label></div>
        {draft.summary_provider === 'compatible' && <><label>API 密钥<input type="password" autoComplete="off" value={key} onChange={e => { setKey(e.target.value); setClearKey(false); }} placeholder={settings.has_api_key ? '已保存，留空保持原密钥' : '输入服务商提供的密钥'}/></label>{settings.has_api_key && <label className="check-label"><input type="checkbox" checked={clearKey} onChange={e => setClearKey(e.target.checked)}/>清除已保存密钥</label>}<p className="helper">Windows 下使用系统账户加密保存，不在浏览器中存储。</p></>}
        <button className="secondary small" disabled={!!busy} onClick={() => void act('test')}>{busy === 'test' ? <LoaderCircle className="spin" size={15}/> : <Check size={15}/>}测试摘要连接</button>
      </section>
      <details><summary>音频设备</summary><label>麦克风<select value={draftMicrophone} onChange={e => setDraftMicrophone(e.target.value)}><option value="">系统默认麦克风</option>{devices?.microphones.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label><label>系统声音输出设备<select value={draftSpeaker} onChange={e => setDraftSpeaker(e.target.value)}><option value="">系统默认输出</option>{devices?.speakers.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label><p className="helper">同时录制麦克风和系统声音时，建议佩戴耳机，减少重复收音。</p></details>
      {message && <p role="status" className="success-text">{message}</p>}{error && <p role="alert" className="error-text">{error}</p>}
    </div><div className="modal-footer"><button className="secondary" onClick={onClose}>取消</button><button className="primary" disabled={!!busy} onClick={() => void act('save')}>{busy === 'save' ? '保存中…' : '保存设置'}</button></div>
  </Modal>;
}
