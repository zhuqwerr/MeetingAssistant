import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, AudioLines, Download, History, LoaderCircle, Mic, Settings as SettingsIcon, Square, X } from 'lucide-react';
import { api } from './api';
import { HistoryView } from './HistoryView';
import { Panels } from './Panels';
import { SettingsDialog } from './SettingsDialog';
import { useMeeting } from './useMeeting';
import type { Devices, Health, MeetingListItem, Settings } from './types';

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
  const [modal, setModal] = useState<'settings' | null>(null);
  const [view, setView] = useState<'meeting' | 'history'>('meeting');
  const [history, setHistory] = useState<MeetingListItem[]>([]);
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
  useEffect(() => {
    if (meeting && !active) setTitle(meeting.title);
  }, [meeting?.id, meeting?.title, active]);

  const action = async (fn: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const start = () => action(async () => {
    const result = await api<{ id: string }>('/meetings', 'POST', { title: title.trim() || '未命名会议', source, language, microphone: microphone || null, speaker: speaker || null });
    await load(result.id);
    setView('meeting');
  });
  const stop = () => action(async () => { if (meeting) await api(`/meetings/${meeting.id}/stop`, 'POST'); });
  const openSettings = () => action(async () => {
    const saved = await api<Settings>('/settings'); setSettings(saved);
    // A device enumeration failure must not prevent configuring the LLM.
    const found = await api<Devices>('/devices').catch(() => null); setDevices(found); setModal('settings');
  });
  const openHistory = () => action(async () => { setHistory(await api<MeetingListItem[]>('/meetings')); setView('history'); });
  const summarize = () => action(async () => { if (meeting) await api(`/meetings/${meeting.id}/summarize`, 'POST'); });
  return <div className="app-shell">
    <aside className="sidebar" aria-label="主导航">
      <button className="brand" aria-label="MeetingAssistant 首页" onClick={() => setView('meeting')}><AudioLines size={32} strokeWidth={2.7}/><span>MeetingAssistant</span></button>
      <nav className="sidebar-nav">
        <button className={view === 'meeting' ? 'active' : ''} aria-current={view === 'meeting' ? 'page' : undefined} onClick={() => setView('meeting')}><Mic size={19}/><span>开始录音</span></button>
        <button className={view === 'history' ? 'active' : ''} aria-current={view === 'history' ? 'page' : undefined} disabled={active || busy} onClick={() => void openHistory()}><History size={19}/><span>历史记录</span></button>
      </nav>
      <button className="sidebar-settings" disabled={active || busy} onClick={() => void openSettings()}><SettingsIcon size={18}/><span>设置</span></button>
    </aside>
    <div className="app-content">
    <header className="topbar">
      <form className="header-controls" onSubmit={e => { e.preventDefault(); if (!active) void start(); }}>
        <label className="header-source"><Mic size={17}/><span className="sr-only">音频来源</span><select value={active ? meeting?.source ?? source : source} onChange={e => setSource(e.target.value)} disabled={active}><option value="mic">麦克风</option><option value="system">系统声音</option><option value="mixed">混合声音</option></select></label>
        <label className="header-language"><span className="sr-only">转写语言</span><select value={active ? meeting?.language ?? language : language} onChange={e => setLanguage(e.target.value)} disabled={active}><option value="zh">中文</option><option value="en">English</option><option value="auto">自动识别</option></select></label>
        {active ? <button type="button" className="primary stop-button header-record-button" disabled={busy || status === 'stopping'} onClick={() => void stop()}>{status === 'stopping' ? <LoaderCircle size={17} className="spin"/> : <Square size={14} fill="currentColor"/>}{status === 'starting' ? '取消准备' : status === 'stopping' ? '正在收尾…' : '结束会议'}</button> : <button className="primary header-record-button" type="submit" disabled={busy || !!serviceError || !settings}>{busy ? <LoaderCircle className="spin" size={18}/> : <Mic size={18}/>}开始会议</button>}
      </form>
      <div className="header-actions">
        <a className={`toolbar-action ${!meeting?.segments.length ? 'disabled' : ''}`} aria-label="导出纪要" title="导出纪要" aria-disabled={!meeting?.segments.length} tabIndex={!meeting?.segments.length ? -1 : 0} href={meeting?.segments.length ? `/api/meetings/${meeting.id}/export` : undefined} download><Download size={18}/></a>
        <button className="toolbar-action" aria-label="设置" title="设置" disabled={active || busy} onClick={() => void openSettings()}><SettingsIcon size={19}/></button>
      </div>
    </header>
    {view === 'meeting' ? <main className={meeting ? 'meeting-view' : 'setup-view'}>
      {(error || serviceError || state?.error || state?.summary_error || (!connected && meeting)) && <div className="notice" role="alert"><AlertCircle size={19}/><div>{serviceError || error || state?.error || state?.summary_error || '与本地服务的连接已断开，正在重新连接。录音状态以服务端为准。'}{state?.summary_error && !active && <button className="text-button" onClick={() => void openSettings()}>检查摘要设置</button>}</div>{error && <button className="icon-button" aria-label="关闭提示" onClick={() => setError('')}><X size={16}/></button>}</div>}
      <Panels key={meeting?.id ?? 'empty'} meeting={meeting} state={state} title={active ? meeting?.title ?? title : title} titleDisabled={active} statusLabel={status ? statusLabels[status] ?? status : '准备就绪'} interval={settings?.summary_interval ?? 30} onTitleChange={setTitle} onSummarize={() => void summarize()}/>
    </main> : <main className="history-view"><HistoryView meetings={history} loading={busy} onRefresh={() => void openHistory()} onOpen={id => void action(async () => { await load(id); setView('meeting'); })}/></main>}
    {modal === 'settings' && settings && <SettingsDialog settings={settings} health={health} devices={devices} microphone={microphone} speaker={speaker} setDevices={(mic, speaker) => { setMicrophone(mic); setSpeaker(speaker); }} onSaved={setSettings} onClose={() => setModal(null)}/>}
    </div>
  </div>;
}
