"use client";

import Link from "next/link";
import {
  auditNavigation,
  commerceNavigation,
  serviceNavigation,
} from "@/lib/navigation";
import type { NavigationItem } from "@/lib/types";

function NavigationGroup({
  title,
  items,
  onNavigate,
}: {
  title: string;
  items: readonly NavigationItem[];
  onNavigate: () => void;
}) {
  return (
    <div className="nav-group">
      <p className="nav-group__label">{title}</p>

      <div className="nav-group__items">
        {items.map((item) => (
          <Link
            href={item.href}
            key={item.href}
            className="nav-item"
            onClick={onNavigate}
          >
            <span className="nav-item__eyebrow">{item.eyebrow}</span>
            <strong>{item.label}</strong>

            <span className="nav-item__description">
              {item.description}
            </span>

            <span className="nav-item__arrow" aria-hidden="true">
              ↗
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}

type Props = {
  open: boolean;
  onClose: () => void;
};

export function NavigationPanel({ open, onClose }: Props) {
  if (!open) return null;

  return (
    <div className="nav-backdrop" onMouseDown={onClose}>
      <div
        id="global-navigation"
        className="navigation-panel"
        role="dialog"
        aria-modal="true"
        aria-label="BIMAP navigation"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="navigation-panel__grid">
          <NavigationGroup
            title="Audit"
            items={auditNavigation}
            onNavigate={onClose}
          />

          <div className="navigation-panel__column">
            <NavigationGroup
              title="Digital content"
              items={commerceNavigation}
              onNavigate={onClose}
            />

            <NavigationGroup
              title="Services"
              items={serviceNavigation}
              onNavigate={onClose}
            />
          </div>
        </div>

        <div className="navigation-panel__foot">
          <span>Remy3Design / R3D</span>
          <span>Evidence-first BIM quality analysis</span>
        </div>
      </div>
    </div>
  );
}
