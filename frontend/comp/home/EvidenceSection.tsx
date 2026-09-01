import { SectionHeading } from "@/comp/ui/SectionHeading";

const evidenceItems = [
  [
    "01",
    "Evidence references",
    "Every released finding should point back to stable source evidence.",
  ],
  [
    "02",
    "Deterministic first",
    "Rule-based checks establish directly observable facts before contextual reasoning.",
  ],
  [
    "03",
    "Uncertainty visible",
    "Pass, warn, fail, unknown and not-applicable remain distinct customer-facing states.",
  ],
  [
    "04",
    "Severity ≠ confidence",
    "Potential impact and certainty are reported as separate dimensions.",
  ],
  [
    "05",
    "Structured outputs",
    "PDF, JSON, CSV and evidence manifests keep findings reusable downstream.",
  ],
] as const;

export function EvidenceSection() {
  return (
    <section
      className="section section--evidence"
      id="evidence"
    >
      <div className="content-width evidence-layout">
        <div className="evidence-layout__intro">
          <SectionHeading
            eyebrow="Evidence, not just a score"
            title="Quality analysis you can inspect."
            description="BIMAP is designed as decision support and pre-review: transparent about what was checked, what remains unknown and which findings need human review."
          />

          <div
            className="evidence-profile"
            aria-hidden="true"
          >
            <div className="evidence-profile__ring">
              <span>QA</span>
            </div>

            <span className="evidence-profile__axis evidence-profile__axis--a" />
            <span className="evidence-profile__axis evidence-profile__axis--b" />
            <span className="evidence-profile__axis evidence-profile__axis--c" />
          </div>
        </div>

        <div className="evidence-list">
          {evidenceItems.map(([number, title, copy]) => (
            <article key={number}>
              <span>{number}</span>

              <div>
                <h3>{title}</h3>
                <p>{copy}</p>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
