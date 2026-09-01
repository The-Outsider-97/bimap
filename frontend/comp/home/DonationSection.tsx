import Link from "next/link";

import {
  donationNavigation,
} from "@/lib/navigation";

export function DonationSection() {
  return (
    <section
      className="donation-section"
      id="support"
    >
      <div
        className="donation-section__grid"
        aria-hidden="true"
      />

      <div className="content-width donation-section__inner">
        <div className="donation-section__index">
          <span>SUPPORT</span>
          <strong>R3D / BIMAP</strong>
        </div>

        <div className="donation-section__content">
          <p className="eyebrow">
            <span aria-hidden="true">
              ●
            </span>
            Support BIMAP
          </p>

          <h2>
            Help BIMAP
            <br />
            <em>keep developing.</em>
          </h2>

          <p>
            Donations support the continued
            development, maintenance and expansion
            of BIMAP, including its BIM quality
            tooling, documentation, research and
            supporting digital infrastructure.
          </p>
        </div>

        <Link
          href={donationNavigation.href}
          className="donation-section__action"
        >
          <span>
            {donationNavigation.label}
          </span>

          <span
            className="donation-section__action-mark"
            aria-hidden="true"
          >
            ↗
          </span>
        </Link>
      </div>
    </section>
  );
}