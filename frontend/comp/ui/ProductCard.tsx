import Link from "next/link";
import type { ReactNode } from "react";

type Props = {
  index: string;
  eyebrow: string;
  title: string;
  description: string;
  href: string;
  children?: ReactNode;
};

export function ProductCard({
  index,
  eyebrow,
  title,
  description,
  href,
  children,
}: Props) {
  return (
    <article className="product-card">
      <div className="product-card__top">
        <span className="product-card__index">{index}</span>
        <span className="product-card__eyebrow">{eyebrow}</span>
      </div>

      <div className="product-card__body">
        <h3>{title}</h3>
        <p>{description}</p>
        {children}
      </div>

      <Link href={href} className="product-card__link">
        Explore <span aria-hidden="true">→</span>
      </Link>
    </article>
  );
}
