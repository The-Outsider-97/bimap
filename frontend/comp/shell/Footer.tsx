"use client";

import Link from "next/link";

type Props = {
  onOpenContact: () => void;
};

export function Footer({
  onOpenContact,
}: Props) {
  return (
    <footer className="site-footer">
      <div className="site-footer__top">
        <div className="footer-brand">
          <img
            src="/remy3design-mark.png"
            alt=""
          />

          <div>
            <strong>BIMAP</strong>
            <span>
              R3D BIM Audit Platform
            </span>
          </div>
        </div>

        <div className="footer-column">
          <p>Audit</p>

          <Link href="/audit/revit">
            Revit Audit
          </Link>

          <Link href="/audit/bim">
            BIM Audit
          </Link>

          <Link href="/audit/combined">
            Combined Audit
          </Link>
        </div>

        <div className="footer-column">
          <p>Content & services</p>

          <Link href="/library/3d">
            3D Models & Scenes
          </Link>

          <Link href="/library/2d">
            2D DWG Library
          </Link>

          <Link href="/services/model-conversion">
            Model Conversion
          </Link>

          <Link href="/services/data-extraction">
            Data Extraction
          </Link>
        </div>

        <div className="footer-column">
          <p>Information</p>

          <Link href="/methodology">
            Methodology
          </Link>

          <Link href="/faq">
            FAQ
          </Link>

          <Link href="/disclaimer">
            Disclaimer
          </Link>

          <button
            type="button"
            className="footer-contact"
            onClick={onOpenContact}
          >
            Contact
          </button>
        </div>
      </div>

      <div className="site-footer__bottom">
        <p>
          © {new Date().getFullYear()}{" "}
          Remy3Design. All rights
          reserved.
        </p>

        <p className="footer-disclaimer">
          BIMAP provides decision-support
          and pre-review quality analysis.
          Outputs do not constitute
          statutory certification,
          building-code approval,
          clash-detection assurance, or a
          substitute for professional BIM
          management and project
          responsibility.
        </p>
      </div>
    </footer>
  );
}