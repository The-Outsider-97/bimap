import type { NextConfig } from "next";


const DEFAULT_BACKEND_URL =
  "http://127.0.0.1:8000";


function resolveBackendOrigin(): string {
  const raw =
    process.env.BIMAP_BACKEND_URL?.trim() ||
    DEFAULT_BACKEND_URL;

  let url: URL;

  try {
    url = new URL(raw);
  } catch {
    throw new Error(
      "BIMAP_BACKEND_URL must be a valid absolute URL.",
    );
  }

  if (
    url.protocol !== "http:" &&
    url.protocol !== "https:"
  ) {
    throw new Error(
      "BIMAP_BACKEND_URL must use http or https.",
    );
  }

  if (url.username || url.password) {
    throw new Error(
      "BIMAP_BACKEND_URL must not contain credentials.",
    );
  }

  if (
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error(
      "BIMAP_BACKEND_URL must contain an origin only, " +
        "for example http://127.0.0.1:8000.",
    );
  }

  return url.origin;
}


const backendOrigin =
  resolveBackendOrigin();


const nextConfig: NextConfig = {
  output: "standalone",

  poweredByHeader: false,

  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination:
          `${backendOrigin}/api/v1/:path*`,
      },
    ];
  },
};


export default nextConfig;
