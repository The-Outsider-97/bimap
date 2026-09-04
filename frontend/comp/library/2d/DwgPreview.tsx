import type {
  TwoDProduct,
} from "./types";

type Props = {
  product: TwoDProduct;
};

export function DwgPreview({
  product,
}: Props) {
  if (product.preview.src) {
    return (
      <img
        className="dwg-preview__image"
        src={product.preview.src}
        alt={product.preview.alt}
        loading="lazy"
      />
    );
  }

  /*
   * Development fallback.
   * Represents architectural
   * linework without pretending
   * to be a real drawing.
   */
  return (
    <div
      className="dwg-preview"
      aria-label={
        `${product.title} preview not configured`
      }
    >
      <div
        className="dwg-preview__grid"
        aria-hidden="true"
      />

      <div
        className="dwg-preview__drawing"
        aria-hidden="true"
      >
        <span />
        <span />
        <span />
        <span />
        <span />
      </div>

      <p>
        DWG preview

        <span>
          not configured
        </span>
      </p>
    </div>
  );
}