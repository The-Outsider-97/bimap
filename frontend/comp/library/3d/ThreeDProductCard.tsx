"use client";

import { FormatBadge } from "@/comp/ui/FormatBadge";
import { PurchaseModelButton } from "./PurchaseModelButton";
import { TurntablePreview } from "./TurntablePreview";
import { formatThreeDFormat, type ThreeDProduct } from "./types";

type Props = { product: ThreeDProduct; onOpen: (product: ThreeDProduct) => void };

function technicalLabel(product: ThreeDProduct): string {
  switch (product.technical.kind) {
    case "revit": return "BIM / Revit";
    case "3ds-max": return "3ds Max";
    case "cad-3d": return "3D CAD";
    case "mesh": return "Mesh / exchange";
  }
}

export function ThreeDProductCard({ product, onOpen }: Props) {
  const open = () => onOpen(product);
  return (
    <article className="model-card-stack">
      <div className="model-card" role="button" tabIndex={0} aria-haspopup="dialog" aria-label={`View details for ${product.title}`} onClick={open} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } }}>
        <div className="model-card__visual">
          <TurntablePreview product={product} />
          <div className="model-card__formats">{product.formats.map((format) => <FormatBadge key={format}>{formatThreeDFormat(format)}</FormatBadge>)}</div>
          <span className="model-card__inspect" aria-hidden="true">View details <span>↗</span></span>
        </div>
        <div className="model-card__info">
          <p className="model-card__category">{product.category}</p>
          <h2>{product.title}</h2>
          <p className="model-card__summary">{product.shortDescription}</p>
          <div className="model-card__meta"><span>{product.formats.map(formatThreeDFormat).join(" · ")}</span><span>{technicalLabel(product)}</span></div>
        </div>
      </div>
      <PurchaseModelButton product={product} />
    </article>
  );
}
