import { SectionHeading } from "@/comp/ui/SectionHeading";

const steps = [
  [
    "01",
    "Select",
    "Choose Family Audit, BIM QA or Combined Audit.",
  ],
  [
    "02",
    "Upload",
    "Stage a controlled evidence package and validate the files.",
  ],
  [
    "03",
    "Pay",
    "Confirm the bounded order before compute-intensive processing starts.",
  ],
  [
    "04",
    "Process",
    "BIMAP normalizes evidence, analyzes it and runs release gates.",
  ],
  [
    "05",
    "Download",
    "Receive a versioned report and structured deliverables.",
  ],
] as const;

export function AuditWorkflow() {
  return (
    <section className="section" id="workflow">
      <div className="content-width">
        <SectionHeading
          eyebrow="How BIMAP works"
          title="From evidence package to reviewable output."
          description="A controlled asynchronous journey: select → upload → pay → process → download."
        />

        <div className="workflow" role="list">
          <div className="workflow__line" aria-hidden="true">
            <span />
          </div>

          {steps.map(([number, title, copy]) => (
            <article
              className="workflow-step"
              role="listitem"
              key={number}
            >
              <span className="workflow-step__number">
                {number}
              </span>

              <span
                className="workflow-step__dot"
                aria-hidden="true"
              />

              <h3>{title}</h3>
              <p>{copy}</p>
            </article>
          ))}
        </div>

        <div className="workflow-note">
          <strong>No meeting required.</strong>
          <span>
            Processing begins only after verified payment. Staged
            abandoned uploads can expire without entering the SLAI
            analysis runtime.
          </span>
        </div>
      </div>
    </section>
  );
}
