import type {
  ReactNode,
} from "react";

import {
  SiteShell,
} from "@/comp/shell/SiteShell";

import type {
  TocItem,
} from "@/lib/types";

type Props = {
  eyebrow: string;
  title: string;
  lead: string;
  toc: readonly TocItem[];
  children: ReactNode;
};

export function InfoPage({
  eyebrow,
  title,
  lead,
  toc,
  children,
}: Props) {
  return (
    <SiteShell
      toc={toc}
      pageDescription={lead}
    >
      <main className="info-page">
        <section className="info-hero">
          <div
            className="info-hero__grid"
            aria-hidden="true"
          />

          <div className="
            content-width
            info-hero__inner
          ">
            <p className="eyebrow">
              <span aria-hidden="true">
                ●
              </span>

              {eyebrow}
            </p>

            <h1>{title}</h1>

            <p>{lead}</p>
          </div>
        </section>

        <div className="
          content-width
          info-content
        ">
          {children}
        </div>
      </main>
    </SiteShell>
  );
}