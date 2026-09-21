import { useEffect, useRef, useState } from 'react';
import { ArrowDown, FileText, Info, Laptop, LoaderCircle, Mic, RefreshCw, Sparkles } from 'lucide-react';
import { clock } from './api';
import type { LiveState, Meeting } from './types';

export function Panels({ meeting, state, interval, onSummarize }: { meeting: Meeting | null; state: LiveState | null; interval: number; onSummarize: () => void }) {
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [showFollow, setShowFollow] = useState(false);
  const [highlight, setHighlight] = useState<number | null>(null);
  const segments = meeting?.segments ?? [];
  const content = meeting?.summary?.content;
  useEffect(() => {
    if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [segments.length]);
  useEffect(() => { follow.current = true; setShowFollow(false); setHighlight(null); }, [meeting?.id]);
  const jump = (id: number) => {
    const element = document.getElementById(`segment-${id}`);
    if (element) { follow.current = false; setShowFollow(true); setHighlight(id); element.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
  };
  const sections = [{ key: 'key_points', title: '讨论要点' }, { key: 'decisions', title: '已确认决策' }, { key: 'action_items', title: '待办事项' }] as const;
  return <div className="workspace">
    <section className="panel transcript-panel" aria-label="实时转写">
      <div className="panel-header"><h2><FileText size={22}/>实时转写</h2><span>{segments.length} 条记录</span></div>
      <div className="transcript-body" ref={scroll} onScroll={() => { const el = scroll.current!; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 65; setShowFollow(!follow.current); }}>
        {segments.length ? <ol className="transcript-list">{segments.map(segment => <li key={segment.id} id={`segment-${segment.id}`} className={highlight === segment.id ? 'highlight' : ''}><div className="segment-meta"><time>{clock(segment.start)}</time><span>{segment.source === 'mic' ? '麦克风' : '系统声音'}</span></div><p>{segment.text}</p></li>)}</ol> : <div className="empty-transcript"><Mic size={48} strokeWidth={1.5}/><h3>{state?.status === 'recording' ? '正在聆听…' : state?.status === 'starting' ? '正在准备语音模型…' : '从第一句话开始'}</h3><p>{state?.status === 'recording' ? '请开始讲话，识别后的文字会自动出现在这里。' : state?.status === 'starting' ? '首次使用需要下载模型，准备完成后才会开始录音。' : '开始会议后，转写内容会按时间显示在这里。'}</p></div>}
      </div>
      {showFollow && <button className="follow-button" onClick={() => { follow.current = true; setShowFollow(false); setHighlight(null); scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: 'smooth' }); }}><ArrowDown size={14}/>回到最新</button>}
      <div className="panel-footer"><Laptop size={16}/><span>语音在本机处理</span>{state?.status === 'recording' && <span className="live-label">正在记录</span>}</div>
    </section>
    <section className="panel summary-panel" aria-label="实时总结">
      <div className="panel-header"><h2><Sparkles size={23}/>实时总结</h2><span>{state?.summary_busy ? '正在更新…' : `每 ${interval} 秒更新`}</span></div>
      <div className="summary-body">
        {content?.overview && <p className="overview">{content.overview}</p>}
        {sections.map(({ key, title }) => <section className="summary-section" key={key}><h3>{title}</h3>{content?.[key].length ? <ul>{content[key].map((item, i) => <li key={i}><span>{item.text}</span>{item.sources.length > 0 && <button className="source-link" title="查看对应转写" onClick={() => jump(item.sources[0])}>原文 ↗</button>}</li>)}</ul> : <p className="placeholder">{content ? '尚未明确提及' : '等待会议内容…'}</p>}</section>)}
      </div>
      <div className="panel-footer summary-footer"><Info size={16}/><span>{meeting?.summary ? `更新于 ${new Date(meeting.summary.created_at).toLocaleTimeString('zh-CN', { hour12: false })}` : '摘要根据已识别内容生成，可随讨论更新。'}</span>{segments.length > 0 && <button className="text-button" disabled={state?.summary_busy} onClick={onSummarize}>{state?.summary_busy ? <LoaderCircle className="spin" size={14}/> : <RefreshCw size={14}/>}立即总结</button>}</div>
    </section>
  </div>;
}
