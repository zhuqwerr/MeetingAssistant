import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { X } from 'lucide-react';

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); return () => ref.current?.close(); }, []);
  return <dialog ref={ref} onCancel={onClose} onClick={e => { if (e.target === ref.current) onClose(); }} aria-label={title}>
    <div className="modal-header"><h2>{title}</h2><button className="icon-button" aria-label="关闭" onClick={onClose}><X size={20} /></button></div>
    {children}
  </dialog>;
}
