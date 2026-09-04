import type {
  TwoDProduct,
} from "./types";


export const TWO_D_PRODUCTS:
  readonly TwoDProduct[] = [];


/*
 * Development-only fixtures.
 *
 * These allow the storefront UI
 * to be tested before real DWG
 * products are entered.
 */
const DEVELOPMENT_PRODUCTS:
  readonly TwoDProduct[] = [
    {
      id:
        "dev-single-dwg",

      slug:
        "development-single-dwg",

      title:
        "Single DWG — Development Fixture",

      shortDescription:
        "UI fixture for an individual DWG drawing.",

      description:
        "This development fixture demonstrates the BIMAP 2D product-card, drawing preview, detailed-description, gallery and DWG-information layout. Replace it with verified commercial product data before publication.",

      category:
        "Architectural Detail",

      kind:
        "single",

      tags: [
        "DWG",
        "SINGLE",
      ],

      preview: {
        alt:
          "Development DWG preview",
      },

      renders: [
        {
          alt:
            "DWG development preview 01",

          caption:
            "Preview 01 — replace with an actual drawing preview.",
        },

        {
          alt:
            "DWG development preview 02",

          caption:
            "Preview 02 — replace with an actual drawing preview.",
        },
      ],

      technical: {},
    },


    {
      id:
        "dev-dwg-pack",

      slug:
        "development-dwg-pack",

      title:
        "DWG Pack — Development Fixture",

      shortDescription:
        "UI fixture for a bundled DWG drawing package.",

      description:
        "This development fixture demonstrates how BIMAP can present a bundled set of related DWG files using the same storefront interaction as individual drawings.",

      category:
        "Drawing Pack",

      kind:
        "pack",

      tags: [
        "DWG",
        "PACK",
      ],

      preview: {
        alt:
          "Development DWG pack preview",
      },

      renders: [
        {
          alt:
            "DWG pack development preview 01",

          caption:
            "Drawing preview 01.",
        },

        {
          alt:
            "DWG pack development preview 02",

          caption:
            "Drawing preview 02.",
        },

        {
          alt:
            "DWG pack development preview 03",

          caption:
            "Drawing preview 03.",
        },
      ],

      technical: {},
    },
  ];


export function getTwoDProducts():
  readonly TwoDProduct[] {

  if (
    TWO_D_PRODUCTS.length === 0 &&
    process.env.NODE_ENV ===
      "development"
  ) {
    return DEVELOPMENT_PRODUCTS;
  }

  return TWO_D_PRODUCTS;
}