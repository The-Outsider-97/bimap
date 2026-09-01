export type Theme = "light" | "dark";

export type TocItem = {
  id: string;
  label: string;
  description?: string;
};

export type NavigationItem = {
  label: string;
  href: string;
  eyebrow: string;
  description: string;
};

next.config.ts

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;
