import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "BIMAP | R3D BIM Audit Platform",
    template: "%s | BIMAP",
  },
  description:
    "Evidence-first quality analysis for Revit families and BIM deliverables, plus R3D digital content and model-data services.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#000000" },
    { media: "(prefers-color-scheme: light)", color: "#EAEDEC" },
  ],
};

const themeBootScript = `
(() => {
  try {
    const stored = localStorage.getItem("bimap-theme");
    const preferred = window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";

    document.documentElement.dataset.theme =
      stored === "light" || stored === "dark" ? stored : preferred;
  } catch (_) {
    document.documentElement.dataset.theme = "dark";
  }
})();
`;

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html
      lang="en"
      data-theme="dark"
      suppressHydrationWarning
    >
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: themeBootScript,
          }}
        />
      </head>

      <body suppressHydrationWarning>
        {children}
      </body>
    </html>
  );
}