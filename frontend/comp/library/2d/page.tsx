import {
  CatalogPage,
  type CatalogCategory,
} from "@/comp/library/CatalogPage";

import type {
  TocItem,
} from "@/lib/types";

const toc:
  readonly TocItem[] = [
    {
      id: "catalog-overview",
      label: "2D library",
    },
    {
      id: "catalog-browse",
      label: "Search & filters",
    },
    {
      id: "catalog-categories",
      label: "Content categories",
    },
  ];

const categories:
  readonly CatalogCategory[] = [
    {
      index: "01",
      title: "Single DWG Files",
      description:
        "Individual 2D drawing elements distributed as standalone DWG files.",
      formats: ["DWG"],
      note: "Single-file purchase",
    },
    {
      index: "02",
      title: "DWG Packs",
      description:
        "Grouped collections of related 2D DWG content distributed in bulk.",
      formats: ["DWG"],
      note: "Bulk content purchase",
    },
  ];

export default function TwoDLibraryPage() {
  return (
    <CatalogPage
      eyebrow="BIMAP digital library / 2D"
      title="2D DWG Library"
      description="Browse individual 2D DWG content or grouped DWG packs for architectural and BIM-support workflows."
      formats={["DWG"]}
      categories={categories}
      toc={toc}
    />
  );
}