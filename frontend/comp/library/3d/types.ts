export type ThreeDFormat =
  | "RFA"
  | "RVT"
  | "MAX"
  | "STL"
  | "DWG_3D"
  | "OBJ"
  | "GLB";

export function formatThreeDFormat(
  format: ThreeDFormat,
): string {
  return format === "DWG_3D"
    ? "3D DWG"
    : format;
}

export type ProductRender = {
  src?: string | null;
  alt: string;
  caption?: string | null;
};

export type ProductPreview = {
  videoSrc?: string | null;
  posterSrc?: string | null;
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
  revitVersion?: string | null;
  parametric?: boolean | null;
  ifc: RevitIfcInformation;
};

export type MaxTechnicalInformation = {
  kind: "3ds-max";
  maxVersion?: string | null;
  vertices?: number | null;
  polygons?: number | null;
  materials: readonly string[];
};

export type MeshTechnicalInformation = {
  kind: "mesh";
  vertices?: number | null;
  edges?: number | null;
  polygons?: number | null;
  units?: string | null;
  materials: readonly string[];
  texturesIncluded?: boolean | null;
};

export type Cad3DTechnicalInformation = {
  kind: "cad-3d";
  dwgVersion?: string | null;
  units?: string | null;
  solids?: number | null;
  surfaces?: number | null;
  meshes?: number | null;
  layers?: number | null;
};

export type ThreeDTechnicalInformation =
  | RevitTechnicalInformation
  | MaxTechnicalInformation
  | MeshTechnicalInformation
  | Cad3DTechnicalInformation;

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
  purchaseHref?: string;
  priceLabel?: string | null;
};
