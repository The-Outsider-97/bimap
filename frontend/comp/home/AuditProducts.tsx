import { ProductCard } from "@/comp/ui/ProductCard";
import { SectionHeading } from "@/comp/ui/SectionHeading";

export function AuditProducts() {
  return (
    <section className="section section--surface" id="audits">
      <div className="content-width">
        <SectionHeading
          eyebrow="BIM quality assurance"
          title="What do you need audited?"
          description="Three customer-facing scopes share one evidence-first audit pipeline while keeping family, project and cross-scope findings explicit."
        />

        <div className="product-grid">
          <ProductCard
            index="01"
            eyebrow="Family"
            title="R3D Family Audit"
            description="Evaluate Revit-family evidence against configured technical and organizational criteria."
            href="/audit/revit"
          >
            <ul className="card-list">
              <li>Identity & naming</li>
              <li>Parameter governance</li>
              <li>Formula & metadata evidence</li>
              <li>Unsupported conditions reported</li>
            </ul>
          </ProductCard>

          <ProductCard
            index="02"
            eyebrow="Project"
            title="R3D BIM QA"
            description="Trace project and deliverable requirements to schedules, registers, reports and other supplied evidence."
            href="/audit/bim"
          >
            <ul className="card-list">
              <li>Requirement-evidence matrix</li>
              <li>Naming & information presence</li>
              <li>Cross-document consistency</li>
              <li>Provenance & versioning</li>
            </ul>
          </ProductCard>

          <ProductCard
            index="03"
            eyebrow="Flagship"
            title="R3D Combined Audit"
            description="Connect family-quality findings to project information-quality consequences where the evidence supports the relationship."
            href="/audit/combined"
          >
            <ul className="card-list">
              <li>One evidence graph</li>
              <li>Linked cross-scope findings</li>
              <li>Integrated risk summary</li>
              <li>Combined remediation plan</li>
            </ul>
          </ProductCard>
        </div>
      </div>
    </section>
  );
}
