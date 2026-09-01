"use client";

import type { TocItem } from "@/lib/types";
import { ThemeToggle } from "./ThemeToggle";

type Props = {
  open: boolean;
  toc: readonly TocItem[];
  description: string;
  onClose: () => void;
};

export function SidePanel({
  open,
  toc,
  description,
  onClose,
}: Props) {
  return (
    <>
      {open ? (
        <div
          className="side-panel__backdrop"
          onClick={onClose}
          aria-hidden="true"
        />
      ) : null}

      <aside
        id="page-side-panel"
        className="side-panel"
        data-open={open}
        aria-hidden={!open}
      >
        <div className="side-panel__top">
          <ThemeToggle />

          <p className="side-panel__kicker">Page navigator</p>
          <h2>BIMAP</h2>
          <p>{description}</p>
        </div>

        <nav
          className="side-panel__toc"
          aria-label="On this page"
        >
          <p className="side-panel__label">On this page</p>

          <ol>
            {toc.map((item, index) => (
              <li key={item.id}>
                <a href={`#${item.id}`} onClick={onClose}>
                  <span>
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <strong>{item.label}</strong>
                </a>
              </li>
            ))}
          </ol>
        </nav>

        <div className="side-panel__foot">
          <span className="status-dot" aria-hidden="true" />
          <span>Powered by SLAI</span>
        </div>
      </aside>
    </>
  );
}
