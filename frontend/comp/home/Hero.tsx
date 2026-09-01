import { Button } from "@/comp/ui/Button";

export function Hero() {
  return (
    <section className="hero section" id="overview">
      <div className="hero__grid" aria-hidden="true" />

      <div className="hero__content content-width">
        <div className="hero__copy">
          <p className="eyebrow">
            <span aria-hidden="true">●</span>
            R3D BIM Audit Platform
          </p>

          <h1>
            BIM quality,
            <br />
            <em>made traceable.</em>
          </h1>

          <p className="hero__lead">
            Evidence-first quality analysis for Revit families and BIM
            deliverables. See what was checked, what was found, where
            the evidence came from, and what to correct next.
          </p>

          <div className="hero__actions">
            <Button href="/audit/combined">Start an audit</Button>

            <Button
              href="/audit/revit"
              variant="secondary"
            >
              Audit Revit families
            </Button>
          </div>

          <a className="hero__text-link" href="#library">
            Browse digital content
            <span aria-hidden="true">↓</span>
          </a>
        </div>

        <div className="hero-visual" aria-hidden="true">
          <div className="hero-visual__orbit hero-visual__orbit--one" />
          <div className="hero-visual__orbit hero-visual__orbit--two" />
          <div className="hero-visual__cross hero-visual__cross--x" />
          <div className="hero-visual__cross hero-visual__cross--y" />

          <img src="/remy3design-mark.png" alt="" />

          <span className="hero-visual__label hero-visual__label--a">
            EVIDENCE
          </span>
          <span className="hero-visual__label hero-visual__label--b">
            REQUIREMENT
          </span>
          <span className="hero-visual__label hero-visual__label--c">
            FINDING
          </span>

          <span className="hero-visual__node hero-visual__node--a" />
          <span className="hero-visual__node hero-visual__node--b" />
          <span className="hero-visual__node hero-visual__node--c" />
        </div>
      </div>

      <div className="hero__meta content-width">
        <span>Asynchronous B2B analysis</span>
        <span>Structured outputs</span>
        <span>Powered by SLAI</span>
      </div>
    </section>
  );
}
