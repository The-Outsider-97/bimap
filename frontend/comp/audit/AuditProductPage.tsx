"use client";

import {
  useCallback,
  useEffect,
  useState,
} from "react";

import { AuditWorkspace } from "@/comp/audit/AuditWorkspace";
import { InfoPage } from "@/comp/content/InfoPage";
import {
  getProduct,
  type BimapProductCode,
  type ProductViewDto,
} from "@/lib/bimap-api";
import type { TocItem } from "@/lib/types";


type Props = {
  productCode: BimapProductCode;
  fallbackTitle: string;
  fallbackLead: string;
};

const toc: readonly TocItem[] = [
  { id: "audit-workspace", label: "Audit workspace" },
  { id: "configuration", label: "Product configuration" },
];


export function AuditProductPage({
  productCode,
  fallbackTitle,
  fallbackLead,
}: Props) {
  const [product, setProduct] = useState<ProductViewDto | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadProduct = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const result = await getProduct(productCode, signal);
      setProduct(result);
      if (!result) {
        setError("This BIMAP audit product is not configured by the backend.");
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        return;
      }
      setProduct(null);
      setError("The BIMAP backend could not provide the audit product configuration.");
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
      }
    }
  }, [productCode]);

  useEffect(() => {
    const controller = new AbortController();
    void loadProduct(controller.signal);
    return () => controller.abort();
  }, [loadProduct]);

  const definition = product?.product;

  return (
    <InfoPage
      eyebrow="BIM audit"
      title={definition?.display_name ?? fallbackTitle}
      lead={definition?.description ?? fallbackLead}
      toc={toc}
    >
      <AuditWorkspace productCode={productCode} />

      <section className="info-section" id="configuration">
        <p className="info-section__index">Configuration</p>
        <h2>Backend-owned audit configuration</h2>

        {loading ? (
          <p className="info-copy">Loading product configuration…</p>
        ) : error ? (
          <>
            <p className="info-copy">{error}</p>
            <button
              type="button"
              className="button button--secondary"
              onClick={() => void loadProduct()}
            >
              <span>Retry backend connection</span>
            </button>
          </>
        ) : definition ? (
          <div className="method-grid">
            <article>
              <span>Scope</span>
              <h3>{definition.scope}</h3>
              <p>{definition.code}</p>
            </article>
            <article>
              <span>Evidence groups</span>
              <h3>{definition.input_groups.length}</h3>
              <p>{definition.input_groups.join(", ") || "None configured"}</p>
            </article>
            <article>
              <span>Report artifacts</span>
              <h3>{definition.output_artifacts.length}</h3>
              <p>{definition.output_artifacts.join(", ") || "None configured"}</p>
            </article>
          </div>
        ) : null}
      </section>
    </InfoPage>
  );
}
