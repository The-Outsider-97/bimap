import Link from "next/link";

type Props = {
  title: string;

  label: string;

  purchaseHref?: string;

  priceLabel?: string;

  compact?: boolean;
};

export function PurchaseAssetButton({
  title,
  label,
  purchaseHref,
  priceLabel,
  compact = false,
}: Props) {
  const buttonLabel =
    priceLabel
      ? `${label} · ${priceLabel}`
      : label;

  if (!purchaseHref) {
    return (
      <button
        type="button"
        className="model-purchase"
        data-compact={compact}
        disabled
        title="Checkout has not yet been configured for this product."
      >
        <span>
          {buttonLabel}
        </span>

        <span aria-hidden="true">
          ↗
        </span>
      </button>
    );
  }

  return (
    <Link
      href={purchaseHref}
      className="model-purchase"
      data-compact={compact}
      aria-label={`${buttonLabel}: ${title}`}
    >
      <span>
        {buttonLabel}
      </span>

      <span aria-hidden="true">
        ↗
      </span>
    </Link>
  );
}