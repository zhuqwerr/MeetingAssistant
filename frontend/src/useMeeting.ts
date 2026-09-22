import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { LiveState, Meeting, Segment, Summary } from './types';

export function useMeeting() {
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [state, setState] = useState<LiveState | null>(null);
  const [connected, setConnected] = useState(true);
  const generation = useRef(0);
  const [streamVersion, setStreamVersion] = useState(0);
  const load = useCallback(async (id: string) => {
    const version = ++generation.current;
    const data = await api<Meeting>(`/meetings/${id}`);
    if (version !== generation.current) return;
    setMeeting(data); setState(null); setStreamVersion(v => v + 1);
  }, []);
  const id = meeting?.id;
  useEffect(() => {
    if (!id) return;
    const stream = new EventSource(`/api/meetings/${id}/events`);
    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    stream.onmessage = (event) => {
      const data = JSON.parse(event.data) as { state: LiveState; segments: Segment[]; summary?: Summary };
      if (data.state.id !== id) return;
      setConnected(true); setState(data.state);
      setMeeting(previous => {
        if (!previous || previous.id !== id) return previous;
        const title = data.state.title || previous.title;
        if (!data.segments.length && !data.summary && title === previous.title) return previous;
        const merged = new Map(previous.segments.map(s => [s.id, s]));
        for (const segment of data.segments) merged.set(segment.id, segment);
        return { ...previous, title, segments: [...merged.values()].sort((a, b) => a.start - b.start || a.id - b.id), summary: data.summary ?? previous.summary };
      });
    };
    return () => stream.close();
  }, [id, streamVersion]);
  return { meeting, state, connected, load };
}
