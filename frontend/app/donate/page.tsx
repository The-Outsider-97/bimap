import Link from "next/link";

import {
  SiteShell,
} from "@/comp/shell/SiteShell";

import type {
  TocItem,
} from "@/lib/types";

const donationToc:
  readonly TocItem[] = [
    {
      id: "support-bimap",
      label: "Support BIMAP",
    },
    {
      id: "why-support",
      label: "Why support BIMAP",
    },
    {
      id: "development",
      label: "Development areas",
    },
  ];

export default function DonatePage() {
  return (
    <SiteShell
      toc={donationToc}
      pageDescription="Support the continued development, maintenance and expansion of the R3D BIM Audit Platform."
    >
      <main className="donate-page">
        <section
          className="donate-hero"
          id="support-bimap"
        >
          <div className="content-width">
            <p className="eyebrow">
              <span aria-hidden="true">
                ●
              </span>

              Support BIMAP
            </p>

            <h1>
              Support continued
              <br />

              <em>
                BIMAP development.
              </em>
            </h1>

            <p>
              Donations contribute to the
              continued development,
              maintenance, documentation and
              technical infrastructure of the
              R3D BIM Audit Platform.
            </p>
          </div>
        </section>

        <section
          className="section section--surface"
          id="why-support"
        >
          <div className="content-width donate-copy">
            <span>01</span>

            <div>
              <h2>
                Why support BIMAP?
              </h2>

              <p>
                BIMAP combines BIM quality
                assurance, reusable digital
                content and model-data services
                within a continuously developed
                technical platform.
              </p>
            </div>
          </div>
        </section>

        <section
          className="section"
          id="development"
        >
          <div className="content-width donate-copy">
            <span>02</span>

            <div>
              <h2>
                Development areas
              </h2>

              <ul>
                <li>
                  BIM QA and Revit-family
                  auditing
                </li>

                <li>
                  Evidence and reporting
                  capabilities
                </li>

                <li>
                  SLAI integration
                </li>

                <li>
                  Documentation and research
                </li>

                <li>
                  Digital infrastructure and
                  platform maintenance
                </li>
              </ul>

              <p className="donate-page__notice">
                A donation payment method can
                be connected here when the
                BIMAP payment layer is
                implemented.
              </p>

              <Link
                href="/"
                className="button button--secondary"
              >
                <span>
                  Return to BIMAP
                </span>

                <span
                  className="button__arrow"
                  aria-hidden="true"
                >
                  ←
                </span>
              </Link>
            </div>
          </div>
        </section>
      </main>
    </SiteShell>
  );
}