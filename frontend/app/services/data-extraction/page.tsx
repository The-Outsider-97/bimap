import type { Metadata } from "next";

import {
  DataExtractionClient,
} from "@/comp/services/DataExtractionClient";
import {
  SiteShell,
} from "@/comp/shell/SiteShell";
import type {
  TocItem,
} from "@/lib/types";


export const metadata:
  Metadata = {
    title: "Data extraction",
    description:
      "Extract authenticated IFC data into a downloadable PDF and JSON BIMAP package.",
  };


const toc:
  readonly TocItem[] = [
    {
      id: "overview",
      label: "Overview",
    },
    {
      id: "extract",
      label: "Extract data",
    },
    {
      id: "package",
      label: "Package",
    },
    {
      id: "integrity",
      label: "Integrity",
    },
  ];


export default function DataExtractionPage() {
  return (
    <SiteShell
      toc={toc}
      pageDescription="Authenticated, quota-governed IFC data extraction with a real PDF + JSON download package and optional delivery to the account email."
    >
      <main>
        <DataExtractionClient />
      </main>
    </SiteShell>
  );
}
