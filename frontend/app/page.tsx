import { AuditProducts } from "@/comp/home/AuditProducts";
import { AuditWorkflow } from "@/comp/home/AuditWorkflow";
import { DigitalLibrary } from "@/comp/home/DigitalLibrary";
import { EvidenceSection } from "@/comp/home/EvidenceSection";
import { FinalCta } from "@/comp/home/FinalCta";
import { Hero } from "@/comp/home/Hero";
import { ModelServices } from "@/comp/home/ModelServices";
import { SiteShell } from "@/comp/shell/SiteShell";
import type { TocItem } from "@/lib/types";

const homeToc: readonly TocItem[] = [
  { id: "overview", label: "Overview" },
  { id: "audits", label: "BIM audits" },
  { id: "workflow", label: "How it works" },
  { id: "library", label: "Digital library" },
  { id: "services", label: "Model services" },
  { id: "evidence", label: "Evidence & quality" },
];

export default function HomePage() {
  return (
    <SiteShell
      toc={homeToc}
      pageDescription="BIMAP combines evidence-first BIM quality assurance with architectural digital content and model-data services."
    >
      <main>
        <Hero />
        <AuditProducts />
        <AuditWorkflow />
        <DigitalLibrary />
        <ModelServices />
        <EvidenceSection />
        <FinalCta />
      </main>
    </SiteShell>
  );
}
