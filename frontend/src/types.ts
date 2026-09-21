export interface Settings {
  asr_model: string; asr_device: string; summary_provider: 'ollama' | 'compatible';
  summary_url: string; summary_model: string; summary_interval: number; vocabulary: string;
  has_api_key: boolean; api_key?: string;
}
export interface Segment { id: number; start: number; end: number; text: string; source: string }
export interface SummaryItem { text: string; sources: number[] }
export interface SummaryContent { overview: string; key_points: SummaryItem[]; decisions: SummaryItem[]; action_items: SummaryItem[] }
export interface Summary { id: number; through_id: number; created_at: string; content: SummaryContent }
export interface Meeting { id: string; title: string; created_at: string; status: string; source: string; language: string; duration: number; segments: Segment[]; summary: Summary | null }
export interface LiveState { id: string; status: string; duration: number; level: number; backlog: number; error: string; summary_error: string; summary_busy: boolean }
export interface Health { ok: boolean; asr: { status: string; error: string; model: string | null }; active_meeting: string | null }
export interface Devices { microphones: { id: string; name: string }[]; speakers: { id: string; name: string }[]; system_supported: boolean }
