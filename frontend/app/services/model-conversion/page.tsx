import type { Metadata } from "next";

import { ModelConversionClient } from "@/comp/services/ModelConversionClient";
import { SiteShell } from "@/comp/shell/SiteShell";
import type { TocItem } from "@/lib/types";


export const metadata: Metadata = {
  title: "Model conversion",
  description: "Convert authenticated BIMAP IFC models to GLB or OBJ delivery packages.",
};

const toc: readonly TocItem[] = [
  { id: "overview", label: "Overview" },
  { id: "convert", label: "Convert model" },
  { id: "formats", label: "Formats" },
  { id: "integrity", label: "Integrity" },
];


export default function ModelConversionPage() {
  return (
    <SiteShell
      toc={toc}
      pageDescription="Authenticated, quota-governed IFC model conversion with real generated GLB and OBJ artifacts."
    >
      <main>
        <ModelConversionClient />
      </main>
    </SiteShell>
  );
}
