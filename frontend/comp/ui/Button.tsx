import Link from "next/link";
import type { ReactNode } from "react";

type Props = {
  href: string;
  children: ReactNode;
  variant?: "primary" | "secondary" | "text";
  className?: string;
};

export function Button({
  href,
  children,
  variant = "primary",
  className = "",
}: Props) {
  return (
    <Link
      className={`button button--${variant} ${className}`.trim()}
      href={href}
    >
      <span>{children}</span>
      <span aria-hidden="true" className="button__arrow">
        ↗
      </span>
    </Link>
  );
}
