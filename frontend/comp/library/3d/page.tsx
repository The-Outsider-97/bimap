"use client";

import { useMemo, useState } from "react";
import { SiteShell } from "@/comp/shell/SiteShell";
import type { TocItem } from "@/lib/types";
import { ThreeDProductCard } from "./ThreeDProductCard";
import { ThreeDProductModal } from "./ThreeDProductModal";
import {
  formatThreeDFormat,
  type ThreeDFormat,
  type ThreeDProduct,
} from "./types";

const toc: readonly TocItem[] = [
  { id: "catalog-overview", label: "3D library" },
  { id: "catalog-browse", label: "Search & filters" },
  { id: "catalog-products", label: "Models & scenes" },
];

const formats: readonly ("ALL" | ThreeDFormat)[] = [
  "ALL", "RFA", "RVT", "MAX", "STL", "DWG_3D", "OBJ", "GLB",
];

type Props = {
  products: readonly ThreeDProduct[];
};

export default function ThreeDLibraryPage({ products }: Props) {
  const [query, setQuery] = useState("");
  const [activeFormat, setActiveFormat] = useState<"ALL" | ThreeDFormat>("ALL");
  const [selectedProduct, setSelectedProduct] = useState<ThreeDProduct | null>(null);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return products.filter((product) => {
      const formatMatch = activeFormat === "ALL" || product.formats.includes(activeFormat);
      if (!formatMatch) return false;
      if (!normalized) return true;
      return [
        product.title,
        product.shortDescription,
        product.description,
        product.category,
        ...product.formats.map(formatThreeDFormat),
        ...product.tags,
      ].join(" ").toLowerCase().includes(normalized);
    });
  }, [activeFormat, products, query]);

  return (
    <SiteShell
      toc={toc}
      pageDescription="Browse BIMAP BIM, CAD and mesh products sourced from the BIMAP store catalog."
    >
      <main className="catalog-page model-library-page">
        <section className="catalog-hero" id="catalog-overview">
          <div className="catalog-hero__grid" aria-hidden="true" />
          <div className="content-width catalog-hero__inner">
            <div>
              <p className="eyebrow"><span aria-hidden="true">●</span>BIMAP digital library / 3D</p>
              <h1>3D Models<br />&amp; Scenes</h1>
              <p>Browse Revit, 3ds Max, STL, 3D DWG, OBJ and GLB content with previews, renders and format-specific technical information.</p>
            </div>
            <div className="catalog-hero__mark" aria-hidden="true"><span /><span /><img src="/remy3design-mark.png" alt="" /></div>
          </div>
        </section>

        <section className="catalog-toolbar" id="catalog-browse">
          <div className="content-width">
            <div className="catalog-search">
              <label htmlFor="three-d-search">Search 3D content</label>
              <div className="catalog-search__field">
                <span aria-hidden="true">⌕</span>
                <input id="three-d-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search models, scenes or formats" />
              </div>
            </div>
            <div className="catalog-filters" aria-label="Filter 3D content by file format">
              {formats.map((format) => (
                <button type="button" key={format} data-active={activeFormat === format} onClick={() => setActiveFormat(format)}>
                  {format === "ALL" ? "ALL" : formatThreeDFormat(format)}
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="section catalog-results" id="catalog-products">
          <div className="content-width">
            <div className="catalog-results__heading">
              <div><p className="eyebrow"><span aria-hidden="true">●</span>Browse content</p><h2>Models &amp; scenes</h2></div>
              <span>{filtered.length} {filtered.length === 1 ? "product" : "products"}</span>
            </div>
            {filtered.length > 0 ? (
              <div className="model-catalog-grid">
                {filtered.map((product) => <ThreeDProductCard key={product.id} product={product} onOpen={setSelectedProduct} />)}
              </div>
            ) : (
              <div className="catalog-empty"><span>00</span><div><h3>No matching products.</h3><p>Adjust the search query or selected file-format filter.</p></div></div>
            )}
          </div>
        </section>
      </main>
      <ThreeDProductModal product={selectedProduct} onClose={() => setSelectedProduct(null)} />
    </SiteShell>
  );
}
