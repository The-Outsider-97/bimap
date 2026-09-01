import { Button } from "@/comp/ui/Button";

export function FinalCta() {
  return (
    <section className="final-cta">
      <div
        className="final-cta__grid"
        aria-hidden="true"
      />

      <div className="content-width final-cta__inner">
        <p className="eyebrow">
          <span aria-hidden="true">●</span>
          R3D BIM Audit
        </p>

        <h2>Ready to check your BIM?</h2>

        <p>
          Choose a bounded analysis, provide the evidence package
          and receive a traceable quality report with structured
          findings.
        </p>

        <div className="final-cta__actions">
          <Button href="/audit/combined">
            Start an audit
          </Button>

          <Button href="#library" variant="secondary">
            Browse digital content
          </Button>
        </div>
      </div>
    </section>
  );
}
