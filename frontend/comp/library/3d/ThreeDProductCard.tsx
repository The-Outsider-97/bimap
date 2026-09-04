"use client";

import {
  FormatBadge,
} from "@/comp/ui/FormatBadge";

import {
  PurchaseModelButton,
} from "./PurchaseModelButton";

import {
  TurntablePreview,
} from "./TurntablePreview";

import type {
  ThreeDProduct,
} from "./types";

type Props = {
  product: ThreeDProduct;

  onOpen: (
    product: ThreeDProduct,
  ) => void;
};

export function ThreeDProductCard({
  product,
  onOpen,
}: Props) {

  const open = () => {
    onOpen(product);
  };

  return (
    <article
      className="model-card-stack"
    >
      <div
        className="model-card"

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
        <div
          className="model-card__visual"
        >
          <TurntablePreview
            product={product}
          />

          <div
            className="model-card__formats"
          >
            {product.formats.map(
              (format) => (
                <FormatBadge
                  key={format}
                >
                  {format}
                </FormatBadge>
              ),
            )}
          </div>

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


        <div
          className="model-card__info"
        >
          <p
            className="model-card__category"
          >
            {product.category}
          </p>

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
              {
                product.formats.join(
                  " · ",
                )
              }
            </span>

            <span>
              {
                product.technical
                  .kind === "revit"
                  ? "BIM / Revit"
                  : "3ds Max"
              }
            </span>
          </div>
        </div>
      </div>


      <PurchaseModelButton
        product={product}
      />
    </article>
  );
}