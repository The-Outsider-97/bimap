"use client";

type Props = {
  open: boolean;
  onToggle: () => void;
};

export function FloatingLogoButton({ open, onToggle }: Props) {
  return (
    <button
      type="button"
      className="floating-logo"
      data-open={open}
      onClick={onToggle}
      aria-expanded={open}
      aria-controls="page-side-panel"
      aria-label={open ? "Close page navigation" : "Open page navigation"}
    >
      <span
        className="floating-logo__pulse floating-logo__pulse--one"
        aria-hidden="true"
      />
      <span
        className="floating-logo__pulse floating-logo__pulse--two"
        aria-hidden="true"
      />
      <span className="floating-logo__ring" aria-hidden="true" />

      <img src="/remy3design-mark.png" alt="" />
    </button>
  );
}
