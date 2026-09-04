export type ThreeDFormat =
  | "RFA"
  | "RVT"
  | "MAX";

export type ProductRender = {
  src?: string;
  alt: string;
  caption?: string;
};

export type ProductPreview = {
  /**
   * Square turntable video used on the storefront card.
   *
   * Recommended:
   * .webm with .mp4 fallback added later if needed.
   *
   * Do not expose the purchased RFA/RVT/MAX source file.
   */
  videoSrc?: string;
  posterSrc?: string;
};

export type IfcProperty = {
  name: string;
  value: string;
};

export type RevitIfcInformation = {
  schema?: string;
  entity?: string;
  predefinedType?: string;
  objectType?: string;
  typeName?: string;
  classification?: string;
  properties?: readonly IfcProperty[];
};

export type RevitTechnicalInformation = {
  kind: "revit";

  revitVersion?: string;

  /**
   * Undefined means the catalog entry
   * has not yet been verified.
   */
  parametric?: boolean;

  ifc: RevitIfcInformation;
};

export type MaxTechnicalInformation = {
  kind: "3ds-max";

  maxVersion?: string;

  vertices?: number;
  polygons?: number;

  materials: readonly string[];
};

export type ThreeDTechnicalInformation =
  | RevitTechnicalInformation
  | MaxTechnicalInformation;

export type ThreeDProduct = {
  id: string;
  slug: string;

  title: string;
  shortDescription: string;
  description: string;

  formats: readonly ThreeDFormat[];

  category: string;
  tags: readonly string[];

  preview: ProductPreview;

  renders: readonly ProductRender[];

  technical: ThreeDTechnicalInformation;

  /**
   * Add the actual checkout URL once
   * commerce has been implemented.
   */
  purchaseHref?: string;

  /**
   * Example:
   * "€ 24.95"
   */
  priceLabel?: string;
};