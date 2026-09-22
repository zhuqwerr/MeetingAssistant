import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, AudioLines, Check, Download, History, LoaderCircle, Mic, Settings as SettingsIcon, Square, X } from 'lucide-react';
import { api, clock } from './api';
import { Modal } from './Modal';
import { Panels } from './Panels';
import { SettingsDialog } from './SettingsDialog';
import { useMeeting } from './useMeeting';
import type { Devices, Health, Meeting, Settings } from './types';

const statusLabels: Record<string, string> = { starting: '正在准备模型', recording: '正在录音', stopping: '正在处理最后的内容', ended: '会议已结束', error: '会议已停止', interrupted: '已恢复中断的会议' };

export default function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [devices, setDevices] = useState<Devices | null>(null);
  const [title, setTitle] = useState('');
  const [source, setSource] = useState('mic');
  const [language, setLanguage] = useState('zh');
  const [microphone, setMicrophone] = useState('');
  const [speaker, setSpeaker] = useState('');
  const [modal, setModal] = useState<'settings' | 'history' | null>(null);
  const [history, setHistory] = useState<Meeting[]>([]);
  const [error, setError] = useState('');
  const [serviceError, setServiceError] = useState('');
  const [busy, setBusy] = useState(false);
  const { meeting, state, connected, load } = useMeeting();
  const hydrated = useRef(false);
  const status = state?.status ?? meeting?.status;
  const active = ['starting', 'recording', 'stopping'].includes(status ?? '');
  const refreshHealth = useCallback(async () => {
    try {
      const data = await api<Health>('/health'); setHealth(data); setServiceError('');
      if (!hydrated.current) {
        hydrated.current = true;
        if (data.active_meeting) await load(data.active_meeting);
      }
    } catch (e) { setServiceError((e as Error).message); }
  }, [load]);
  useEffect(() => {
    void refreshHealth();
    void api<Settings>('/settings').then(setSettings).catch(e => setError(e.message));
    const timer = setInterval(() => void refreshHealth(), 2500);
    return () => clearInterval(timer);
  }, [refreshHealth]);

  const action = async (fn: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const start = () => action(async () => {
    const result = await api<{ id: string }>('/meetings', 'POST', { title: title.trim() || '未命名会议', source, language, microphone: microphone || null, speaker: speaker || null });
    await load(result.id);
  });
  const stop = () => action(async () => { if (meeting) await api(`/meetings/${meeting.id}/stop`, 'POST'); });
  const openSettings = () => action(async () => {
    const saved = await api<Settings>('/settings'); setSettings(saved);
    // A device enumeration failure must not prevent configuring the LLM.
    const found = await api<Devices>('/devices').catch(() => null); setDevices(found); setModal('settings');
  });
  const openHistory = () => action(async () => { setHistory(await api<Meeting[]>('/meetings')); setModal('history'); });
  const summarize = () => action(async () => { if (meeting) await api(`/meetings/${meeting.id}/summarize`, 'POST'); });
  const level = Math.min(1, (state?.level ?? 0) * 12);
  return <>
    <header className="topbar"><a className="brand" href="/" aria-label="MeetingAssistant 首页"><AudioLines size={32} strokeWidth={2.7}/><span>MeetingAssistant</span></a><div className="header-actions"><span className="local-badge"><i/>本地转写</span><span className="header-divider"/><button className="icon-button history-button" aria-label="会议记录" title="会议记录" disabled={active || busy} onClick={() => void openHistory()}><History size={20}/></button><button className="secondary settings-button" disabled={active || busy} onClick={() => void openSettings()}><SettingsIcon size={18}/>设置</button></div></header>
    <main>
      {!meeting && <div className="intro"><h1>让讨论留下重点。</h1><p>实时记录每一句话，持续整理会议脉络。</p></div>}
      <form className="controls" onSubmit={e => { e.preventDefault(); if (!active) void start(); }}>
        <label className="title-field"><span className="sr-only">会议名称</span><input maxLength={120} placeholder="未命名会议" value={active ? meeting?.title ?? title : title} onChange={e => setTitle(e.target.value)} disabled={active}/></label>
        <label className="source-field"><Mic size={20}/><span className="sr-only">音频来源</span><select value={active ? meeting?.source ?? source : source} onChange={e => setSource(e.target.value)} disabled={active}><option value="mic">麦克风</option><option value="system">系统声音</option><option value="mixed">麦克风＋系统声音</option></select></label>
        <label className="language-field"><span className="sr-only">转写语言</span><select value={active ? meeting?.language ?? language : language} onChange={e => setLanguage(e.target.value)} disabled={active}><option value="zh">中文</option><option value="en">English</option><option value="auto">自动识别</option></select></label>
        {active ? <button type="button" className="primary stop-button" disabled={busy || status === 'stopping'} onClick={() => void stop()}>{status === 'stopping' ? <LoaderCircle size={19} className="spin"/> : <Square size={16} fill="currentColor"/>}{status === 'starting' ? '取消准备' : status === 'stopping' ? '正在收尾…' : '结束会议'}</button> : <button className="primary start-button" type="submit" disabled={busy || !!serviceError || !settings}>{busy ? <LoaderCircle className="spin" size={20}/> : <Mic size={22}/>}开始会议</button>}
      </form>
      {!active && source === 'mixed' && <p className="inline-hint">建议佩戴耳机，避免系统声音被麦克风再次录入。</p>}
      <div className="status-strip"><div className="status-left"><span className={`status-dot ${status === 'recording' ? 'recording' : ''}`}/><span>{status ? statusLabels[status] ?? status : '准备就绪'}</span><span className="status-divider"/><time>{clock(state?.duration ?? meeting?.duration ?? 0)}</time>{status === 'recording' && <span className="level-meter" aria-label="输入音量"><span style={{ width: `${level * 100}%` }}/></span>}{(state?.backlog ?? 0) > 3 && <span className="backlog">等待识别 {state?.backlog} 段</span>}</div><a className={`secondary export-button ${!meeting?.segments.length ? 'disabled' : ''}`} aria-disabled={!meeting?.segments.length} tabIndex={!meeting?.segments.length ? -1 : 0} href={meeting?.segments.length ? `/api/meetings/${meeting.id}/export` : undefined} download><Download size={17}/>导出纪要</a></div>
      {(error || serviceError || state?.error || state?.summary_error || (!connected && meeting)) && <div className="notice" role="alert"><AlertCircle size={19}/><div>{serviceError || error || state?.error || state?.summary_error || '与本地服务的连接已断开，正在重新连接。录音状态以服务端为准。'}{state?.summary_error && !active && <button className="text-button" onClick={() => void openSettings()}>检查摘要设置</button>}</div>{error && <button className="icon-button" aria-label="关闭提示" onClick={() => setError('')}><X size={16}/></button>}</div>}
      <Panels key={meeting?.id ?? 'empty'} meeting={meeting} state={state} interval={settings?.summary_interval ?? 30} onSummarize={() => void summarize()}/>
      {meeting && !active && <div className="meeting-caption"><Check size={14}/><span>当前记录：{meeting.title} · 已保存到本机</span></div>}
    </main>
    {modal === 'settings' && settings && <SettingsDialog settings={settings} health={health} devices={devices} microphone={microphone} speaker={speaker} setDevices={(mic, speaker) => { setMicrophone(mic); setSpeaker(speaker); }} onSaved={setSettings} onClose={() => setModal(null)}/>}
    {modal === 'history' && <Modal title="会议记录" onClose={() => setModal(null)}><div className="history-list">{history.length ? history.map(item => <button key={item.id} onClick={() => void action(async () => { await load(item.id); setModal(null); })}><div><strong>{item.title}</strong><span>{new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}</span></div><span>{clock(item.duration)}</span></button>) : <p className="history-empty">还没有会议记录，开始第一场会议吧。</p>}</div></Modal>}
  </>;
}
