import { useEffect, useRef, useState } from 'react';
import { ArrowDown, FileText, Info, Laptop, LoaderCircle, Mic, RefreshCw, Send, Sparkles, X } from 'lucide-react';
import { api, clock } from './api';
import type { LiveState, Meeting, MeetingState, Suggestion, SummaryContent, SummaryItem, TodoItem } from './types';

const tabs = [
  { id: 'summary', label: '即时摘要' },
  { id: 'points', label: '关键要点' },
  { id: 'todos', label: '待办事项' },
  { id: 'advice', label: 'AI 建议' },
] as const;
const prompts = ['会议现在讲了什么？', '目前有哪些待办？', '已经确定了哪些事情？'];
const kindLabel: Record<Suggestion['kind'], string> = { plan_change: '计划变化', missing_info: '信息缺失', fact_check: '事实核查' };

function isState(content: SummaryContent): content is MeetingState {
  return 'topics' in content && 'summary' in content;
}

export function Panels({ meeting, state, interval, onSummarize }: { meeting: Meeting | null; state: LiveState | null; interval: number; onSummarize: () => void }) {
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [showFollow, setShowFollow] = useState(false);
  const [highlight, setHighlight] = useState<number | null>(null);
  const [tab, setTab] = useState<(typeof tabs)[number]['id']>('summary');
  const [askOpen, setAskOpen] = useState(false);
  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const pendingAsk = useRef<AbortController | null>(null);
  const askInput = useRef<HTMLInputElement>(null);
  const [chat, setChat] = useState<{ role: 'user' | 'ai'; text: string; sources: number[] }[]>([]);
  const segments = meeting?.segments ?? [];
  const content = meeting?.summary?.content;
  const latest = segments.at(-1);
  const recording = state?.status === 'recording';
  const fresh = meeting?.summary ? Date.now() - new Date(meeting.summary.created_at).getTime() < 90_000 : false;
  useEffect(() => {
    if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [segments.length]);
  useEffect(() => {
    follow.current = true; setShowFollow(false); setHighlight(null);
    setTab('summary'); setAskOpen(false); setChat([]); setQuestion(''); setAsking(false);
    return () => {
      pendingAsk.current?.abort();
      pendingAsk.current = null;
    };
  }, [meeting?.id]);
  useEffect(() => {
    if (!askOpen) return;
    const frame = requestAnimationFrame(() => askInput.current?.focus());
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setAskOpen(false); };
    document.addEventListener('keydown', closeOnEscape);
    return () => { cancelAnimationFrame(frame); document.removeEventListener('keydown', closeOnEscape); };
  }, [askOpen]);
  const jump = (id: number) => {
    const element = document.getElementById(`segment-${id}`);
    if (element) { follow.current = false; setShowFollow(true); setHighlight(id); element.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
  };
  const ask = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || !meeting || pendingAsk.current) return;
    const request = new AbortController();
    pendingAsk.current = request;
    setQuestion('');
    setChat(items => [...items, { role: 'user', text: trimmed, sources: [] }]);
    setAsking(true);
    try {
      const result = await api<{ answer: string; sources: number[] }>(`/meetings/${meeting.id}/ask`, 'POST', { question: trimmed }, request.signal);
      if (pendingAsk.current !== request || request.signal.aborted) return;
      setChat(items => [...items, { role: 'ai', text: result.answer, sources: result.sources }]);
    } catch (error) {
      if (pendingAsk.current !== request || request.signal.aborted) return;
      setChat(items => [...items, { role: 'ai', text: (error as Error).message, sources: [] }]);
    } finally {
      if (pendingAsk.current === request) { pendingAsk.current = null; setAsking(false); }
    }
  };
  const sourceOf = (id: number | undefined) => id == null ? null : segments.find(segment => segment.id === id);
  const sourceLink = (id: number | undefined) => {
    const segment = sourceOf(id);
    if (!segment) return null;
    return <span className="source-row"><time>{clock(segment.start)}</time><button className="source-link" onClick={() => jump(segment.id)}>查看原文 ↗</button></span>;
  };
  const sources = (items: SummaryItem[] | undefined) => items?.length ? <ul>{items.map((item, index) => <li key={index}><span>{item.text}</span>{sourceLink(item.sources[0])}</li>)}</ul> : <p className="placeholder">尚未明确提及</p>;
  return <div className="workspace">
    <section className="panel analysis-panel" aria-label="会议分析">
      <div className="panel-header analysis-heading"><div><h2>{meeting?.title || '会议分析'}</h2><span>{state?.summary_busy ? '正在更新…' : meeting?.summary ? (fresh ? '刚刚更新' : `更新于 ${new Date(meeting.summary.created_at).toLocaleTimeString('zh-CN', { hour12: false })}`) : '等待第一段转写'}</span></div></div>
      <div className="analysis-tabs" role="tablist">{tabs.map(item => <button key={item.id} role="tab" aria-selected={tab === item.id} onClick={() => setTab(item.id)}>{item.label}</button>)}</div>
      <div className="summary-body" role="tabpanel">
        {tab === 'summary' && (content && isState(content) ? <>
          <article className="state-card"><header><h3>即时摘要</h3>{fresh && <span className="fresh-badge">刚刚更新</span>}</header><p>{content.summary || '还没有足够内容形成概括。'}</p></article>
          <section className="summary-section"><h3>讨论要点</h3>{content.topics.length ? content.topics.map((topic, index) => <article className="state-card topic" key={index}><h4>{index + 1}. {topic.title}</h4>{sources(topic.points)}</article>) : <p className="placeholder">等待会议内容…</p>}</section>
        </> : content && !isState(content) ? <>
          <p className="overview">{content.overview}</p>
          <section className="summary-section"><h3>讨论要点</h3>{sources(content.key_points)}</section>
        </> : <p className="placeholder analysis-empty">开始会议后，这里会保持一份截至当前的完整纪要。</p>)}
        {tab === 'points' && (content && isState(content) ? <section className="summary-section"><h3>关键要点</h3>{sources(content.key_points)}</section> : content && !isState(content) ? <section className="summary-section"><h3>已确认决策</h3>{sources(content.decisions)}</section> : <p className="placeholder analysis-empty">关键结论会单独列在这里，并可回到原话。</p>)}
        {tab === 'todos' && (content && isState(content) ? <section className="summary-section"><h3>待办事项</h3>{content.todos.length ? <ul className="todo-list">{content.todos.map((item, index) => { const origin = sourceOf(item.sources[0]); return <Todo key={index} item={item} onJump={jump} when={origin ? clock(origin.start) : undefined}/>; })}</ul> : <p className="placeholder">尚未明确提及</p>}</section> : content && !isState(content) ? <section className="summary-section"><h3>待办事项</h3>{sources(content.action_items)}</section> : <p className="placeholder analysis-empty">听到明确任务后，会写在这里。</p>)}
        {tab === 'advice' && (content && isState(content) && content.suggestions.length ? content.suggestions.map((item, index) => <article className="suggestion" key={index}><p className="suggestion-kind">{kindLabel[item.kind]}</p><h3>{item.title}</h3>{item.quote && <p><strong>会议原话</strong>{item.quote}</p>}{item.detail && <p><strong>判断</strong>{item.detail}</p>}{sourceLink(item.sources[0])}</article>) : <p className="placeholder analysis-empty">计划变化、缺失信息和需要核对的说法会出现在这里。</p>)}
      </div>
      <div className="panel-footer summary-footer"><Info size={16}/><span>每 {interval} 秒增量更新，左侧始终是当前完整纪要。</span>{segments.length > 0 && <button className="text-button" disabled={state?.summary_busy} onClick={onSummarize}>{state?.summary_busy ? <LoaderCircle className="spin" size={14}/> : <RefreshCw size={14}/>}立即总结</button>}</div>
    </section>
    <section className="panel transcript-panel" aria-label="实时转写">
      <div className="panel-header"><h2><FileText size={22}/>实时转写</h2><span>{segments.length} 条记录</span></div>
      <div className="live-strip"><div className="live-meta"><time>{clock(state?.duration ?? meeting?.duration ?? 0)}</time><span className={recording ? 'live-pill on' : 'live-pill'}>{recording ? '录制中' : '转写'}</span></div><div className="live-wave" aria-hidden="true"><span style={{ width: `${Math.min(100, (state?.level ?? 0) * 1200)}%` }}/></div><p>{latest?.text ?? '开始后，最新一句会显示在这里。'}</p></div>
      <div className="transcript-body" ref={scroll} onScroll={() => { const el = scroll.current!; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 65; setShowFollow(!follow.current); }}>
        {segments.length ? <ol className="transcript-list">{segments.map(segment => <li key={segment.id} id={`segment-${segment.id}`} className={highlight === segment.id ? 'highlight' : ''}><div className="segment-meta"><time>{clock(segment.start)}</time><span>{segment.source === 'mic' ? '麦克风' : '系统声音'}</span></div><p>{segment.text}</p></li>)}</ol> : <div className="empty-transcript"><Mic size={48} strokeWidth={1.5}/><h3>{state?.status === 'recording' ? '正在聆听…' : state?.status === 'starting' ? '正在准备语音模型…' : '从第一句话开始'}</h3><p>{state?.status === 'recording' ? '请开始讲话，识别后的文字会自动出现在这里。' : state?.status === 'starting' ? '首次使用需要下载模型，准备完成后才会开始录音。' : '开始会议后，转写内容会按时间显示在这里。'}</p></div>}
      </div>
      {showFollow && <button className="follow-button" onClick={() => { follow.current = true; setShowFollow(false); setHighlight(null); scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: 'smooth' }); }}><ArrowDown size={14}/>回到最新</button>}
      <div className="panel-footer"><Laptop size={16}/><span>语音在本机处理</span>{state?.status === 'recording' && <span className="live-label">正在记录</span>}</div>
    </section>
    {meeting && <button className="ask-launcher" disabled={!segments.length} title={segments.length ? undefined : '有转写内容后即可提问'} onClick={() => setAskOpen(true)}><Sparkles size={16}/>问问 AI</button>}
    {askOpen && meeting && <div className="ask-overlay" onMouseDown={() => setAskOpen(false)}>
      <aside className="ask-drawer" role="dialog" aria-modal="true" aria-label="会议 AI" onMouseDown={event => event.stopPropagation()}>
        <header><div className="ask-title"><span><Sparkles size={18}/></span><div><h2>会议 AI</h2><p>{meeting.title}</p></div></div><button className="icon-button" aria-label="关闭问答" onClick={() => setAskOpen(false)}><X size={17}/></button></header>
        <div className="ask-log">{chat.length ? chat.map((item, index) => <div key={index} className={item.role === 'user' ? 'ask-user' : 'ask-ai'}><span className="ask-role">{item.role === 'user' ? '你' : '会议 AI'}</span><p>{item.text}</p>{item.sources[0] != null && <button className="source-link" onClick={() => { setAskOpen(false); jump(item.sources[0]); }}>来源 {clock(segments.find(segment => segment.id === item.sources[0])?.start ?? 0)} · 查看原文 ↗</button>}</div>) : <div className="ask-empty"><span className="ask-empty-icon"><Sparkles size={22}/></span><h3>问问这场会议</h3><p>根据转写和当前纪要回答，并标出可以核对的原话。</p><div className="ask-prompts"><span>常用问题</span>{prompts.map(prompt => <button key={prompt} onClick={() => void ask(prompt)}>{prompt}<span>→</span></button>)}</div></div>}{asking && <div className="ask-thinking"><LoaderCircle className="spin" size={15}/>正在查看这场会议…</div>}</div>
        <form className="ask-form" onSubmit={event => { event.preventDefault(); void ask(question); }}><input ref={askInput} value={question} onChange={event => setQuestion(event.target.value)} placeholder="输入与会议相关的问题…" maxLength={500}/><button className="primary" type="submit" disabled={asking || !question.trim()} aria-label="发送"><Send size={17}/></button></form>
      </aside>
    </div>}
  </div>;
}

function Todo({ item, onJump, when }: { item: TodoItem; onJump: (id: number) => void; when?: string }) {
  return <li><span className="todo-mark" aria-hidden="true"/> <span>{item.content}</span><p className="todo-meta">负责人：{item.owner || '未明确'} · 截止时间：{item.deadline || '未明确'}{when && item.sources[0] != null && <><time>{when}</time><button className="source-link" onClick={() => onJump(item.sources[0])}>查看原文 ↗</button></>}</p></li>;
}
