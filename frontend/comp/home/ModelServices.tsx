import Link from "next/link";
import { SectionHeading } from "@/comp/ui/SectionHeading";

export function ModelServices() {
  return (
    <section className="section" id="services">
      <div className="content-width">
        <SectionHeading
          eyebrow="Model & data services"
          title="Convert. Extract. Reuse."
          description="Two practical services for moving model information into the format or data structure your downstream workflow needs."
        />

        <div className="service-grid">
          <Link
            href="/services/model-conversion"
            className="service-card"
          >
            <span className="service-card__index">01</span>

            <div>
              <p>Model conversion</p>
              <h3>
                Move geometry and information between workflows.
              </h3>
              <span>
                Submit source model(s), define the intended output,
                and receive a controlled converted package.
              </span>
            </div>

            <span
              className="service-card__arrow"
              aria-hidden="true"
            >
              ↗
            </span>
          </Link>

          <Link
            href="/services/data-extraction"
            className="service-card"
          >
            <span className="service-card__index">02</span>

            <div>
              <p>Data extraction</p>
              <h3>
                Turn model information into structured data.
              </h3>
              <span>
                Extract required attributes and model information
                for schedules, QA, analysis or downstream reuse.
              </span>
            </div>

            <span
              className="service-card__arrow"
              aria-hidden="true"
            >
              ↗
            </span>
          </Link>
        </div>
      </div>
    </section>
  );
}
