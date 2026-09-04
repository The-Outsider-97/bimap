export type TwoDProductKind =
  | "single"
  | "pack";

export type TwoDPreview = {
  src?: string;
  alt: string;
};

export type TwoDRender = {
  src?: string;
  alt: string;
  caption?: string;
};

export type DwgTechnicalInformation = {
  dwgVersion?: string;

  units?: string;

  drawingScale?: string;

  layerCount?: number;

  fileCount?: number;

  modelSpace?: boolean;

  paperSpaceLayouts?: number;
};

export type TwoDProduct = {
  id: string;
  slug: string;

  title: string;

  shortDescription: string;

  description: string;

  category: string;

  kind: TwoDProductKind;

  tags: readonly string[];

  preview: TwoDPreview;

  renders: readonly TwoDRender[];

  technical: DwgTechnicalInformation;

  priceLabel?: string;

  purchaseHref?: string;
};