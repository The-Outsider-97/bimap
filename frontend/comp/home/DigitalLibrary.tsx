import Link from "next/link";
import { FormatBadge } from "@/comp/ui/FormatBadge";
import { SectionHeading } from "@/comp/ui/SectionHeading";

export function DigitalLibrary() {
  return (
    <section
      className="section section--dark-band"
      id="library"
    >
      <div className="content-width">
        <SectionHeading
          eyebrow="Digital library"
          title="Architectural content ready to reuse."
          description="A secondary BIMAP storefront for 3D assets, scenes and 2D drawing content — separate from the audit product domain."
        />

        <div className="library-grid">
          <Link
            className="library-card library-card--3d"
            href="/library/3d"
          >
            <div
              className="library-card__visual"
              aria-hidden="true"
            >
              <span className="wire-cube" />
            </div>

            <div className="library-card__content">
              <p>3D content</p>
              <h3>Models & scenes</h3>

              <div className="badge-row">
                <FormatBadge>RVT</FormatBadge>
                <FormatBadge>RFA</FormatBadge>
                <FormatBadge>MAX</FormatBadge>
              </div>

              <span>
                Revit families, models, 3ds Max assets and complete
                scenes.
              </span>

              <strong>
                Browse 3D <span aria-hidden="true">→</span>
              </strong>
            </div>
          </Link>

          <Link
            className="library-card library-card--2d"
            href="/library/2d"
          >
            <div
              className="library-card__visual library-card__visual--drawing"
              aria-hidden="true"
            >
              <span />
              <span />
              <span />
            </div>

            <div className="library-card__content">
              <p>2D content</p>
              <h3>DWG library</h3>

              <div className="badge-row">
                <FormatBadge>DWG</FormatBadge>
                <FormatBadge>SINGLE</FormatBadge>
                <FormatBadge>PACK</FormatBadge>
              </div>

              <span>
                Individual drawing elements and coordinated DWG
                collections.
              </span>

              <strong>
                Browse 2D <span aria-hidden="true">→</span>
              </strong>
            </div>
          </Link>
        </div>
      </div>
    </section>
  );
}
