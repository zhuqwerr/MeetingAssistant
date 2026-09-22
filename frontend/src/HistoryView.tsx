import { ArrowRight, CalendarDays, Clock3, FileText, RefreshCw } from 'lucide-react';
import { useMemo } from 'react';
import { clock } from './api';
import type { MeetingListItem, SummaryContent } from './types';

interface HistoryViewProps {
  meetings: MeetingListItem[];
  loading: boolean;
  onOpen: (id: string) => void;
  onRefresh: () => void;
}

function summaryText(content: SummaryContent | undefined) {
  if (!content) return '暂无会议摘要，打开记录可以查看完整转写。';
  return ('summary' in content ? content.summary : content.overview) || '暂无会议摘要，打开记录可以查看完整转写。';
}

function groupLabel(value: string) {
  const created = new Date(value);
  const today = new Date();
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const startCreated = new Date(created.getFullYear(), created.getMonth(), created.getDate()).getTime();
  const days = Math.floor((startToday - startCreated) / 86_400_000);
  if (days <= 0) return '今天';
  if (days === 1) return '昨天';
  if (days <= 7) return '过去 7 天';
  return '更早';
}

export function HistoryView({ meetings, loading, onOpen, onRefresh }: HistoryViewProps) {
  const groups = useMemo(() => {
    const result = new Map<string, MeetingListItem[]>();
    const sorted = [...meetings].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    for (const meeting of sorted) {
      const label = groupLabel(meeting.created_at);
      result.set(label, [...(result.get(label) ?? []), meeting]);
    }
    return [...result.entries()];
  }, [meetings]);

  return <section className="history-page" aria-labelledby="history-heading">
    <header className="history-page-header">
      <div><h1 id="history-heading">历史记录</h1><p>查看过往会议的摘要与完整转写。</p></div>
      <button className="secondary history-refresh" disabled={loading} onClick={onRefresh}><RefreshCw size={15} className={loading ? 'spin' : undefined}/>{loading ? '正在刷新' : '刷新'}</button>
    </header>
    {groups.length ? <div className="history-groups">{groups.map(([label, items]) => <section className="history-group" key={label}>
      <h2>{label}<span>{items.length} 场会议</span></h2>
      <div className="history-cards">{items.map(item => <button className="history-card" key={item.id} onClick={() => onOpen(item.id)}>
        <span className="history-card-icon"><FileText size={19}/></span>
        <span className="history-card-content">
          <span className="history-card-title">{item.title || '未命名会议'}</span>
          <span className="history-card-meta"><span><CalendarDays size={13}/>{new Date(item.created_at).toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</span><span><Clock3 size={13}/>{clock(item.duration)}</span></span>
          <span className="history-card-summary">{summaryText(item.summary?.content)}</span>
        </span>
        <span className="history-card-open">查看记录<ArrowRight size={15}/></span>
      </button>)}</div>
    </section>)}</div> : <div className="history-page-empty"><span><FileText size={28}/></span><h2>还没有会议记录</h2><p>开始第一场会议后，摘要和转写会保存在这里。</p></div>}
  </section>;
}
