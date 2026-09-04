import Link from "next/link";

import type {
  ThreeDProduct,
} from "./types";

type Props = {
  product: ThreeDProduct;

  compact?: boolean;
};

export function PurchaseModelButton({
  product,
  compact = false,
}: Props) {

  const label =
    product.priceLabel
      ? `Purchase model · ${product.priceLabel}`
      : "Purchase model";

  /*
   * Commerce does not exist yet.
   *
   * Keep the control visible,
   * but do not send customers to
   * a fabricated checkout route.
   */
  if (!product.purchaseHref) {
    return (
      <button
        type="button"

        className="model-purchase"

        data-compact={compact}

        disabled

        title="Checkout has not yet been configured for this product."
      >
        <span>
          {label}
        </span>

        <span aria-hidden="true">
          ↗
        </span>
      </button>
    );
  }

  return (
    <Link
      href={
        product.purchaseHref
      }

      className="model-purchase"

      data-compact={compact}

      aria-label={
        `${label}: ${product.title}`
      }
    >
      <span>
        {label}
      </span>

      <span aria-hidden="true">
        ↗
      </span>
    </Link>
  );
}