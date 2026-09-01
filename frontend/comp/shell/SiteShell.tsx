"use client";

import {
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import type { TocItem } from "@/lib/types";

import { FloatingLogoButton } from "./FloatingLogoButton";
import { Footer } from "./Footer";
import { Header } from "./Header";
import { NavigationPanel } from "./NavigationPanel";
import { SidePanel } from "./SidePanel";

type Props = {
  children: ReactNode;
  toc: readonly TocItem[];
  pageDescription: string;
};

export function SiteShell({
  children,
  toc,
  pageDescription,
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [sideOpen, setSideOpen] = useState(false);

  const closeAll = useCallback(() => {
    setMenuOpen(false);
    setSideOpen(false);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeAll();
      }
    };

    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [closeAll]);

  return (
    <div className="site-shell">
      <Header
        menuOpen={menuOpen}
        onToggleMenu={() => {
          setMenuOpen((value) => !value);
          setSideOpen(false);
        }}
      />

      <NavigationPanel
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
      />

      <SidePanel
        open={sideOpen}
        toc={toc}
        description={pageDescription}
        onClose={() => setSideOpen(false)}
      />

      <FloatingLogoButton
        open={sideOpen}
        onToggle={() => {
          setSideOpen((value) => !value);
          setMenuOpen(false);
        }}
      />

      {children}

      <Footer />
    </div>
  );
}
