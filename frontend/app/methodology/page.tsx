import {
  InfoPage,
} from "@/comp/content/InfoPage";

import type {
  TocItem,
} from "@/lib/types";

const toc: readonly TocItem[] = [
  {
    id: "principle",
    label: "Core principle",
  },
  {
    id: "ingestion",
    label: "Evidence ingestion",
  },
  {
    id: "deterministic",
    label: "Deterministic checks",
  },
  {
    id: "reasoning",
    label: "Contextual reasoning",
  },
  {
    id: "governance",
    label: "Governance",
  },
  {
    id: "outputs",
    label: "Outputs",
  },
];

export default function MethodologyPage() {
  return (
    <InfoPage
      eyebrow="BIMAP methodology"
      title="Evidence first. Reasoning second."
      lead="BIMAP separates observable BIM evidence, deterministic rules, contextual reasoning, governance and reporting so released findings remain traceable."
      toc={toc}
    >
      <section
        className="info-section"
        id="principle"
      >
        <p className="info-section__index">
          01 / Principle
        </p>

        <h2>
          Evidence is the basis of
          the audit.
        </h2>

        <p className="info-copy">
          BIMAP first establishes what
          evidence was supplied, where it
          came from and whether a condition
          can be directly tested.
        </p>

        <div className="method-chain">
          <span>Evidence</span>
          <i>→</i>
          <span>Normalize</span>
          <i>→</i>
          <span>Rules</span>
          <i>→</i>
          <span>Reason</span>
          <i>→</i>
          <span>Govern</span>
          <i>→</i>
          <span>Report</span>
        </div>
      </section>

      <section
        className="info-section"
        id="ingestion"
      >
        <p className="info-section__index">
          02 / Ingestion
        </p>

        <h2>
          Controlled evidence ingestion
        </h2>

        <p className="info-copy">
          Accepted evidence is staged,
          validated and normalized into
          BIMAP contracts before analysis.
        </p>

        <div className="method-grid">
          <article>
            <span>01</span>
            <h3>Identify</h3>
            <p>
              Record evidence package,
              scope and source context.
            </p>
          </article>

          <article>
            <span>02</span>
            <h3>Validate</h3>
            <p>
              Unsupported or malformed
              inputs remain explicit.
            </p>
          </article>

          <article>
            <span>03</span>
            <h3>Normalize</h3>
            <p>
              Convert accepted evidence
              into canonical structures.
            </p>
          </article>
        </div>
      </section>

      <section
        className="info-section"
        id="deterministic"
      >
        <p className="info-section__index">
          03 / Rules
        </p>

        <h2>
          Deterministic checks run first.
        </h2>

        <p className="info-copy">
          Directly testable conditions
          are evaluated by versioned rules
          before contextual interpretation
          is introduced.
        </p>

        <div className="method-callout">
          <strong>
            Rule-first design
          </strong>

          <p>
            Contextual explanation cannot
            override deterministic evidence.
            Unknown and not-applicable
            conditions remain explicit.
          </p>
        </div>
      </section>

      <section
        className="info-section"
        id="reasoning"
      >
        <p className="info-section__index">
          04 / SLAI
        </p>

        <h2>
          Contextual reasoning is bounded
          by evidence.
        </h2>

        <p className="info-copy">
          SLAI supports contextual synthesis,
          planning, quality review and
          governed interpretation after the
          deterministic evidence layer.
        </p>
      </section>

      <section
        className="info-section"
        id="governance"
      >
        <p className="info-section__index">
          05 / Governance
        </p>

        <h2>
          Release gates preserve
          uncertainty.
        </h2>

        <div className="method-grid">
          <article>
            <span>A</span>
            <h3>
              Evidence integrity
            </h3>
            <p>
              Released findings retain
              stable evidence references.
            </p>
          </article>

          <article>
            <span>B</span>
            <h3>
              Confidence separation
            </h3>
            <p>
              Confidence remains separate
              from severity.
            </p>
          </article>

          <article>
            <span>C</span>
            <h3>Human review</h3>
            <p>
              Ambiguous conditions can be
              routed to review.
            </p>
          </article>
        </div>
      </section>

      <section
        className="info-section"
        id="outputs"
      >
        <p className="info-section__index">
          06 / Reporting
        </p>

        <h2>
          One result, multiple usable
          outputs.
        </h2>

        <p className="info-copy">
          Approved findings can be rendered
          into a human-readable report and
          structured machine-readable
          artifacts without changing their
          evidence provenance.
        </p>
      </section>
    </InfoPage>
  );
}