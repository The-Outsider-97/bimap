import type {
  ThreeDProduct,
} from "./types";

/**
 * Production catalog.
 *
 * Add only verified commercial
 * product information here.
 *
 * Later this can be replaced by
 * catalog information received
 * from the BIMAP backend.
 */
export const THREE_D_PRODUCTS:
  readonly ThreeDProduct[] = [];


/**
 * Development fixtures.
 *
 * These are rendered only while
 * developing the page and only
 * while THREE_D_PRODUCTS is empty.
 *
 * They are not production products.
 */
const DEVELOPMENT_PRODUCTS:
  readonly ThreeDProduct[] = [
    {
      id: "dev-revit-family",

      slug:
        "development-revit-family",

      title:
        "Revit Family — Development Fixture",

      shortDescription:
        "UI fixture for a future parametric Revit product.",

      description:
        "This development fixture demonstrates the BIMAP product-card, render gallery, detailed-description and IFC-information layout. Replace it with verified metadata from the real Revit asset before publishing the product.",

      formats: [
        "RFA",
      ],

      category:
        "Revit Families",

      tags: [
        "revit",
        "family",
        "parametric",
      ],

      preview: {},

      renders: [
        {
          alt:
            "Development fixture render 01",

          caption:
            "Render 01 — replace with an actual product render.",
        },
        {
          alt:
            "Development fixture render 02",

          caption:
            "Render 02 — replace with an actual product render.",
        },
        {
          alt:
            "Development fixture render 03",

          caption:
            "Render 03 — replace with an actual product render.",
        },
      ],

      technical: {
        kind:
          "revit",

        ifc: {},
      },
    },

    {
      id:
        "dev-max-model",

      slug:
        "development-max-model",

      title:
        "3ds Max Model — Development Fixture",

      shortDescription:
        "UI fixture for a future 3ds Max model.",

      description:
        "This development fixture demonstrates the BIMAP 3ds Max technical-information layout. Replace it with verified vertex, polygon and material information from the production asset before publishing the product.",

      formats: [
        "MAX",
      ],

      category:
        "3ds Max Models",

      tags: [
        "3ds max",
        "model",
        "visualization",
      ],

      preview: {},

      renders: [
        {
          alt:
            "Development fixture render 01",

          caption:
            "Render 01 — replace with an actual product render.",
        },
        {
          alt:
            "Development fixture render 02",

          caption:
            "Render 02 — replace with an actual product render.",
        },
        {
          alt:
            "Development fixture render 03",

          caption:
            "Render 03 — replace with an actual product render.",
        },
      ],

      technical: {
        kind:
          "3ds-max",

        materials: [],
      },
    },
  ];


export function getThreeDProducts():
  readonly ThreeDProduct[] {

  if (
    THREE_D_PRODUCTS.length === 0 &&
    process.env.NODE_ENV ===
      "development"
  ) {
    return DEVELOPMENT_PRODUCTS;
  }

  return THREE_D_PRODUCTS;
}