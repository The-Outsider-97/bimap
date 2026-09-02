import {
  InfoPage,
} from "@/comp/content/InfoPage";

import type {
  TocItem,
} from "@/lib/types";

const toc: readonly TocItem[] = [
  {
    id: "purpose",
    label: "Purpose",
  },
  {
    id: "professional",
    label: "Professional responsibility",
  },
  {
    id: "compliance",
    label: "Compliance boundaries",
  },
  {
    id: "evidence",
    label: "Evidence limitations",
  },
  {
    id: "digital-content",
    label: "Digital content",
  },
  {
    id: "services",
    label: "Conversion & extraction",
  },
];

export default function DisclaimerPage() {
  return (
    <InfoPage
      eyebrow="Service disclaimer"
      title="Disclaimer"
      lead="The intended use, boundaries and limitations of BIMAP audits, digital content and model-data services."
      toc={toc}
    >
      <section
        className="info-section"
        id="purpose"
      >
        <p className="info-section__index">
          01 / Purpose
        </p>

        <h2>
          Decision support and pre-review
        </h2>

        <p className="info-copy">
          BIMAP supports BIM quality
          assurance, evidence review and
          remediation planning within the
          configured audit scope.
        </p>
      </section>

      <section
        className="info-section"
        id="professional"
      >
        <p className="info-section__index">
          02 / Responsibility
        </p>

        <h2>
          Professional responsibility
          remains with the appointed
          parties.
        </h2>

        <p className="info-copy">
          BIMAP does not replace the
          professional judgement,
          contractual duties or project
          responsibilities of BIM managers,
          designers, engineers, contractors,
          information managers or other
          appointed project parties.
        </p>
      </section>

      <section
        className="info-section"
        id="compliance"
      >
        <p className="info-section__index">
          03 / Compliance
        </p>

        <h2>
          No automatic statutory
          certification
        </h2>

        <p className="info-copy">
          A BIMAP result is not, by itself,
          statutory approval, building-code
          approval, contractual acceptance,
          design certification or formal
          authority approval.
        </p>
      </section>

      <section
        className="info-section"
        id="evidence"
      >
        <p className="info-section__index">
          04 / Evidence
        </p>

        <h2>
          Results depend on the supplied
          evidence.
        </h2>

        <p className="info-copy">
          Missing, ambiguous or unsupported
          evidence can result in unknown,
          not-applicable or review-required
          outcomes rather than an artificial
          pass or fail.
        </p>
      </section>

      <section
        className="info-section"
        id="digital-content"
      >
        <p className="info-section__index">
          05 / Digital content
        </p>

        <h2>
          Verify purchased content in its
          project context.
        </h2>

        <p className="info-copy">
          Revit, 3ds Max and DWG content
          should be checked for suitability
          against the receiving project,
          software version, specification
          and contractual requirements.
        </p>
      </section>

      <section
        className="info-section"
        id="services"
      >
        <p className="info-section__index">
          06 / Services
        </p>

        <h2>
          Conversion does not guarantee
          semantic equivalence.
        </h2>

        <p className="info-copy">
          Model conversion can alter how
          geometry, parameters,
          classifications and metadata are
          represented between source and
          destination formats.
        </p>
      </section>
    </InfoPage>
  );
}