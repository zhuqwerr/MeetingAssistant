export interface Settings {
  asr_model: 'base' | 'small' | 'medium';
  summary_url: string; summary_model: string; summary_interval: number; vocabulary: string;
  has_api_key: boolean; summary_configured: boolean; api_key?: string;
}
export interface Segment { id: number; start: number; end: number; text: string; source: string }
export interface SummaryItem { text: string; sources: number[] }
export interface Topic { title: string; points: SummaryItem[] }
export interface TodoItem { content: string; owner: string; deadline: string; sources: number[] }
export interface Suggestion { kind: 'plan_change' | 'missing_info' | 'fact_check'; title: string; quote: string; detail: string; sources: number[] }
export interface MeetingState { summary: string; topics: Topic[]; key_points: SummaryItem[]; todos: TodoItem[]; suggestions: Suggestion[] }
export interface LegacySummary { overview: string; key_points: SummaryItem[]; decisions: SummaryItem[]; action_items: SummaryItem[] }
export type SummaryContent = MeetingState | LegacySummary
export interface Summary { id: number; through_id: number; created_at: string; content: SummaryContent }
export interface Meeting { id: string; title: string; created_at: string; status: string; source: string; language: string; duration: number; segments: Segment[]; summary: Summary | null }
export type MeetingListItem = Omit<Meeting, 'segments'>;
export interface LiveState { id: string; title: string; status: string; duration: number; level: number; backlog: number; error: string; summary_error: string; summary_busy: boolean }
export interface Health {
  ok: boolean;
  asr: { status: string; error: string; model: string | null };
  model: WhisperModelInfo & { selected: WhisperModelName; models: WhisperModelInfo[] };
  active_meeting: string | null;
}
export type WhisperModelName = 'base' | 'small' | 'medium';
export interface WhisperModelInfo {
  id: WhisperModelName; name: string; revision: string; status: string; installed: boolean;
  progress: number; downloaded_bytes: number; total_bytes: number; size_label: string; error: string;
}
export interface Devices { microphones: { id: string; name: string }[]; speakers: { id: string; name: string }[]; system_supported: boolean }
