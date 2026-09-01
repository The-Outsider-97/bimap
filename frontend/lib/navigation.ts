import type { NavigationItem } from "./types";

export const auditNavigation: readonly NavigationItem[] = [
  {
    label: "Revit Audit",
    href: "/audit/revit",
    eyebrow: "Family QA",
    description: "Evidence-first Revit family quality analysis.",
  },
  {
    label: "BIM Audit",
    href: "/audit/bim",
    eyebrow: "Project QA",
    description:
      "Trace project information requirements to supplied project evidence.",
  },
];

export const commerceNavigation: readonly NavigationItem[] = [
  {
    label: "3D Models & Scenes",
    href: "/library/3d",
    eyebrow: "RVT · RFA · MAX",
    description:
      "Production-ready Revit content, 3ds Max models and complete scenes.",
  },
  {
    label: "2D DWG Library",
    href: "/library/2d",
    eyebrow: "DWG",
    description:
      "Individual drawing elements and coordinated drawing packs.",
  },
];

export const serviceNavigation: readonly NavigationItem[] = [
  {
    label: "Model Conversion",
    href: "/services/model-conversion",
    eyebrow: "Convert",
    description:
      "Prepare model content for a required downstream format.",
  },
  {
    label: "Data Extraction",
    href: "/services/data-extraction",
    eyebrow: "Extract",
    description:
      "Turn model information into structured, reusable data.",
  },
];
