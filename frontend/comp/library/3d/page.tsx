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
      label: "3D library",
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
      title: "Revit Families",
      description:
        "Reusable Revit family content distributed as RFA files.",
      formats: ["RFA"],
      note: "Parametric BIM content",
    },
    {
      index: "02",
      title: "Revit Models",
      description:
        "Revit model content distributed as RVT project files.",
      formats: ["RVT"],
      note: "Revit project content",
    },
    {
      index: "03",
      title: "3ds Max Models",
      description:
        "Standalone 3D assets prepared for 3ds Max workflows.",
      formats: ["MAX"],
      note: "Individual 3D assets",
    },
    {
      index: "04",
      title: "3ds Max Scenes",
      description:
        "Complete 3ds Max scene packages for visualization workflows.",
      formats: ["MAX"],
      note: "Complete scenes",
    },
  ];

export default function ThreeDLibraryPage() {
  return (
    <CatalogPage
      eyebrow="BIMAP digital library / 3D"
      title="3D Models & Scenes"
      description="Browse BIMAP 3D content for Revit and 3ds Max workflows. The storefront remains separate from BIMAP audit products and services."
      formats={[
        "RFA",
        "RVT",
        "MAX",
      ]}
      categories={categories}
      toc={toc}
    />
  );
}