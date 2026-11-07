import { useEffect, useRef } from "react";
import { X } from "lucide-react";

interface ImagePreviewDialogProps {
  source: string | null;
  title: string;
  description?: string;
  onClose: () => void;
}

export function ImagePreviewDialog({ source, title, description, onClose }: ImagePreviewDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (source && !dialog.current?.open) dialog.current?.showModal();
    if (!source) dialog.current?.close();
  }, [source]);

  return (
    <dialog
      ref={dialog}
      className="studio-image-dialog"
      aria-label={title}
      onClose={onClose}
      onClick={(event) => { if (event.target === event.currentTarget) dialog.current?.close(); }}
    >
      <div className="studio-image-heading">
        <strong title={title}>{title}</strong>
        <button className="icon-button" type="button" aria-label="关闭大图" onClick={() => dialog.current?.close()}>
          <X size={18} />
        </button>
      </div>
      <div className="studio-image-canvas">
        {source && <img src={source} alt={title} />}
      </div>
      {description && <p className="studio-image-description">{description}</p>}
    </dialog>
  );
}
