"use client";

import {
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import type { TocItem } from "@/lib/types";

import { ContactModal } from "./ContactModal";
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
  const [menuOpen, setMenuOpen] =
    useState(false);

  const [sideOpen, setSideOpen] =
    useState(false);

  const [contactOpen, setContactOpen] =
    useState(false);

  const closeAll = useCallback(() => {
    setMenuOpen(false);
    setSideOpen(false);
    setContactOpen(false);
  }, []);

  const openContact = useCallback(() => {
    setMenuOpen(false);
    setSideOpen(false);
    setContactOpen(true);
  }, []);

  useEffect(() => {
    const onKeyDown = (
      event: KeyboardEvent,
    ) => {
      if (event.key === "Escape") {
        closeAll();
      }
    };

    document.addEventListener(
      "keydown",
      onKeyDown,
    );

    return () => {
      document.removeEventListener(
        "keydown",
        onKeyDown,
      );
    };
  }, [closeAll]);

  useEffect(() => {
    const overlayOpen =
      menuOpen ||
      sideOpen ||
      contactOpen;

    document.body.classList.toggle(
      "is-overlay-open",
      overlayOpen,
    );

    return () => {
      document.body.classList.remove(
        "is-overlay-open",
      );
    };
  }, [
    menuOpen,
    sideOpen,
    contactOpen,
  ]);

  return (
    <div className="site-shell">
      <Header
        menuOpen={menuOpen}
        onToggleMenu={() => {
          setMenuOpen(
            (value) => !value,
          );

          setSideOpen(false);
          setContactOpen(false);
        }}
      />

      <NavigationPanel
        open={menuOpen}
        onClose={() =>
          setMenuOpen(false)
        }
        onOpenContact={openContact}
      />

      <SidePanel
        open={sideOpen}
        toc={toc}
        description={pageDescription}
        onClose={() =>
          setSideOpen(false)
        }
      />

      <FloatingLogoButton
        open={sideOpen}
        onToggle={() => {
          setSideOpen(
            (value) => !value,
          );

          setMenuOpen(false);
          setContactOpen(false);
        }}
      />

      <ContactModal
        open={contactOpen}
        onClose={() =>
          setContactOpen(false)
        }
      />

      {children}

      <Footer
        onOpenContact={openContact}
      />
    </div>
  );
}