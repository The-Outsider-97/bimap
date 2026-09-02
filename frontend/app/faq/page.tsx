import {
  InfoPage,
} from "@/comp/content/InfoPage";

import type {
  TocItem,
} from "@/lib/types";

const toc: readonly TocItem[] = [
  {
    id: "audits",
    label: "Audits",
  },
  {
    id: "evidence",
    label: "Evidence & findings",
  },
  {
    id: "deliverables",
    label: "Deliverables",
  },
  {
    id: "content",
    label: "Digital content",
  },
  {
    id: "services",
    label: "Model services",
  },
];

export default function FaqPage() {
  return (
    <InfoPage
      eyebrow="Frequently asked questions"
      title="FAQ"
      lead="Practical answers about BIMAP audits, evidence, deliverables, digital content, model conversion and data extraction."
      toc={toc}
    >
      <section
        className="info-section"
        id="audits"
      >
        <p className="info-section__index">
          01 / Audits
        </p>

        <h2>Audit scope</h2>

        <div className="faq-list">
          <details>
            <summary>
              What does BIMAP audit?
            </summary>

            <p>
              BIMAP is structured around
              Revit-family quality assurance,
              project-level BIM QA and a
              Combined Audit that can
              correlate findings across both
              scopes.
            </p>
          </details>

          <details>
            <summary>
              What is the difference between
              Revit Audit and BIM Audit?
            </summary>

            <p>
              Revit Audit focuses on
              family-level evidence such as
              identity, parameters, formulas
              and metadata. BIM Audit focuses
              on project requirements,
              information presence, naming,
              consistency, provenance and
              supplied project evidence.
            </p>
          </details>

          <details>
            <summary>
              What does Combined Audit add?
            </summary>

            <p>
              Combined Audit does not merely
              place two reports next to each
              other. Its purpose is to connect
              family-level and project-level
              evidence where a supported
              relationship exists.
            </p>
          </details>
        </div>
      </section>

      <section
        className="info-section"
        id="evidence"
      >
        <p className="info-section__index">
          02 / Evidence
        </p>

        <h2>
          Evidence & findings
        </h2>

        <div className="faq-list">
          <details>
            <summary>
              How are findings produced?
            </summary>

            <p>
              BIMAP uses deterministic checks
              for directly testable conditions
              before contextual reasoning.
              Unsupported conditions can
              remain unknown rather than
              being forced into a pass or
              fail result.
            </p>
          </details>

          <details>
            <summary>
              Are severity and confidence
              the same?
            </summary>

            <p>
              No. Severity represents
              potential impact; confidence
              represents certainty in the
              finding.
            </p>
          </details>

          <details>
            <summary>
              Does BIMAP replace a BIM
              manager?
            </summary>

            <p>
              No. BIMAP is decision-support
              and pre-review software.
              Professional and contractual
              responsibility remains with the
              appointed project parties.
            </p>
          </details>
        </div>
      </section>

      <section
        className="info-section"
        id="deliverables"
      >
        <p className="info-section__index">
          03 / Outputs
        </p>

        <h2>Deliverables</h2>

        <div className="faq-list">
          <details>
            <summary>
              What does an audit return?
            </summary>

            <p>
              BIMAP is designed around a
              human-readable report together
              with structured findings,
              remediation information and
              evidence manifests. BIM QA and
              Combined products can also
              include requirement-matrix
              outputs.
            </p>
          </details>

          <details>
            <summary>
              Why include structured outputs?
            </summary>

            <p>
              Structured outputs keep
              findings and evidence usable
              after report delivery for
              filtering, comparison and
              downstream workflows.
            </p>
          </details>
        </div>
      </section>

      <section
        className="info-section"
        id="content"
      >
        <p className="info-section__index">
          04 / Digital content
        </p>

        <h2>3D & 2D content</h2>

        <div className="faq-list">
          <details>
            <summary>
              What digital content does
              BIMAP sell?
            </summary>

            <p>
              BIMAP includes a separate
              storefront for Revit and
              3ds Max 3D content, complete
              scenes, and 2D DWG content
              offered as individual files
              or grouped packs.
            </p>
          </details>
        </div>
      </section>

      <section
        className="info-section"
        id="services"
      >
        <p className="info-section__index">
          05 / Services
        </p>

        <h2>
          Conversion & extraction
        </h2>

        <div className="faq-list">
          <details>
            <summary>
              What is model conversion?
            </summary>

            <p>
              Model conversion moves supplied
              model content into a required
              downstream format within an
              explicitly defined conversion
              scope.
            </p>
          </details>

          <details>
            <summary>
              What is data extraction?
            </summary>

            <p>
              Data extraction converts
              required model information into
              structured data for schedules,
              analysis, QA or downstream
              reuse.
            </p>
          </details>
        </div>
      </section>
    </InfoPage>
  );
}