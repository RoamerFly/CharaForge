import brandIcon from "../assets/charaforge-icon.png";
import { PRODUCT_NAME, PRODUCT_NAME_EN } from "../lib/brand";

interface InitialLoadingProps {
  message?: string;
}

export function InitialLoading({ message = "正在打开创作空间…" }: InitialLoadingProps) {
  return (
    <div className="initial-loading-screen" role="status" aria-live="polite">
      <div className="initial-loading-card">
        <div className="initial-loading-art" aria-hidden="true">
          <span className="loading-frame loading-frame-back" />
          <span className="loading-frame loading-frame-front" />
          <img src={brandIcon} alt="" width="96" height="96" />
        </div>
        <h1 className="initial-loading-title">{PRODUCT_NAME}</h1>
        <span className="initial-loading-name">{PRODUCT_NAME_EN}</span>
        <div className="initial-loading-track" aria-hidden="true"><span /></div>
        <p className="initial-loading-text">{message}</p>
      </div>
    </div>
  );
}
