import {
  PurchaseAssetButton,
} from "@/comp/library/shared/PurchaseAssetButton";

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
  return (
    <PurchaseAssetButton
      title={product.title}
      label="Purchase model"
      purchaseHref={
        product.purchaseHref
      }
      priceLabel={
        product.priceLabel
      }
      compact={compact}
    />
  );
}