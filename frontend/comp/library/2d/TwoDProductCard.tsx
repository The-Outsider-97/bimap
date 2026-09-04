"use client";

import {
  FormatBadge,
} from "@/comp/ui/FormatBadge";

import {
  PurchaseAssetButton,
} from "@/comp/library/shared/PurchaseAssetButton";

import {
  DwgPreview,
} from "./DwgPreview";

import type {
  TwoDProduct,
} from "./types";


type Props = {
  product: TwoDProduct;

  onOpen: (
    product: TwoDProduct,
  ) => void;
};


export function TwoDProductCard({
  product,
  onOpen,
}: Props) {

  const open = () => {
    onOpen(product);
  };


  return (
    <article
      className="
        model-card-stack
        dwg-card-stack
      "
    >

      <div
        className="
          model-card
          dwg-card
        "

        role="button"

        tabIndex={0}

        aria-haspopup="dialog"

        aria-label={
          `View details for ${product.title}`
        }

        onClick={open}

        onKeyDown={(event) => {

          if (
            event.key === "Enter" ||
            event.key === " "
          ) {
            event.preventDefault();

            open();
          }
        }}
      >

        {/* 1:1 STATIC PREVIEW */}

        <div
          className="model-card__visual"
        >
          <DwgPreview
            product={product}
          />


          {/* TOP-RIGHT TAGS */}

          <div
            className="
              model-card__formats
              dwg-card__tags
            "
          >
            {product.tags.map(
              (tag) => (
                <FormatBadge
                  key={tag}
                >
                  {tag}
                </FormatBadge>
              ),
            )}
          </div>


          {/* BOTTOM-LEFT CATEGORY */}

          <span
            className="dwg-card__category-overlay"
          >
            {product.category}
          </span>


          <span
            className="model-card__inspect"
            aria-hidden="true"
          >
            View details

            <span>
              ↗
            </span>
          </span>
        </div>


        {/* PRODUCT INFORMATION */}

        <div
          className="model-card__info"
        >
          <h2>
            {product.title}
          </h2>


          <p
            className="model-card__summary"
          >
            {
              product.shortDescription
            }
          </p>


          <div
            className="model-card__meta"
          >
            <span>
              DWG
            </span>

            <span>
              {
                product.kind ===
                  "pack"
                  ? "DWG PACK"
                  : "SINGLE DWG"
              }
            </span>
          </div>
        </div>

      </div>


      {/* ATTACHED PURCHASE BUTTON */}

      <PurchaseAssetButton
        title={product.title}

        label={
          product.kind === "pack"
            ? "Purchase DWG pack"
            : "Purchase DWG"
        }

        purchaseHref={
          product.purchaseHref
        }

        priceLabel={
          product.priceLabel
        }
      />

    </article>
  );
}