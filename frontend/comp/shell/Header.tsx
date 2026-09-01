"use client";

import Link from "next/link";
import { useHeaderVisibility } from "@/hooks/useHeaderVisibility";

type Props = {
  menuOpen: boolean;
  onToggleMenu: () => void;
};

export function Header({ menuOpen, onToggleMenu }: Props) {
  const visible = useHeaderVisibility(menuOpen);

  return (
    <header
      className="site-header"
      data-visible={visible || menuOpen}
    >
      <div className="site-header__inner">
        <div className="site-header__left">
          <button
            className="menu-toggle"
            type="button"
            aria-expanded={menuOpen}
            aria-controls="global-navigation"
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            onClick={onToggleMenu}
          >
            <span />
            <span />
            <span />
          </button>

          <Link href="/" className="brand" aria-label="BIMAP home">
            <img src="/remy3design-mark.png" alt="" />
            <span className="brand__name">BIMAP</span>
            <span className="brand__divider" aria-hidden="true" />
            <span className="brand__sub">R3D BIM Audit Platform</span>
          </Link>
        </div>

        <nav className="site-header__actions" aria-label="Primary actions">
          <a className="header-link" href="#audits">
            Audits
          </a>

          <Link className="header-cta" href="/audit/combined">
            Start audit <span aria-hidden="true">↗</span>
          </Link>
        </nav>
      </div>
    </header>
  );
}
