"use client";

import {
  useCallback,
  useEffect,
  useState,
} from "react";

import {
  InfoPage,
} from "@/comp/content/InfoPage";

import {
  getProduct,
  type BimapProductCode,
  type ProductViewDto,
} from "@/lib/bimap-api";

import type {
  TocItem,
} from "@/lib/types";


const toc: readonly TocItem[] = [
  {
    id: "product",
    label: "Audit product",
  },
  {
    id: "inputs",
    label: "Required inputs",
  },
  {
    id: "outputs",
    label: "Outputs",
  },
  {
    id: "configuration",
    label: "Configuration",
  },
];


type Props = {
  productCode: BimapProductCode;
  fallbackTitle: string;
  fallbackLead: string;
};


export function AuditProductPage({
  productCode,
  fallbackTitle,
  fallbackLead,
}: Props) {
  const [
    product,
    setProduct,
  ] = useState<ProductViewDto | null>(
    null,
  );

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    error,
    setError,
  ] = useState<string | null>(
    null,
  );


  const loadProduct = useCallback(
    async (
      signal?: AbortSignal,
    ) => {
      setLoading(true);
      setError(null);

      try {
        const result =
          await getProduct(
            productCode,
            signal,
          );

        if (!result) {
          setProduct(null);

          setError(
            "This BIMAP product is not configured by the backend.",
          );

          return;
        }

        setProduct(result);
      } catch (caught) {
        if (
          caught instanceof DOMException &&
          caught.name === "AbortError"
        ) {
          return;
        }

        setProduct(null);

        setError(
          "The BIMAP backend could not be reached or returned an invalid response.",
        );
      } finally {
        if (!signal?.aborted) {
          setLoading(false);
        }
      }
    },
    [productCode],
  );


  useEffect(() => {
    const controller =
      new AbortController();

    void loadProduct(
      controller.signal,
    );

    return () => {
      controller.abort();
    };
  }, [loadProduct]);


  const definition =
    product?.product;

  const title =
    definition?.display_name ??
    fallbackTitle;

  const lead =
    definition?.description ??
    fallbackLead;


  return (
    <InfoPage
      eyebrow="BIM quality assurance"
      title={title}
      lead={lead}
      toc={toc}
    >
      <section
        className="info-section"
        id="product"
      >
        <p className="info-section__index">
          01 / Product
        </p>

        <h2>
          Live BIMAP product configuration
        </h2>

        {loading ? (
          <p className="info-copy">
            Loading BIMAP product
            configuration…
          </p>
        ) : error ? (
          <>
            <p className="info-copy">
              {error}
            </p>

            <button
              type="button"
              className="
                button
                button--secondary
              "
              onClick={() => {
                void loadProduct();
              }}
            >
              <span>
                Retry backend connection
              </span>
            </button>
          </>
        ) : definition ? (
          <div className="method-grid">
            <article>
              <span>Code</span>

              <h3>
                {definition.code}
              </h3>

              <p>
                Canonical BIMAP product
                identifier.
              </p>
            </article>

            <article>
              <span>Scope</span>

              <h3>
                {definition.scope}
              </h3>

              <p>
                Evidence scope configured
                by the backend.
              </p>
            </article>

            <article>
              <span>Status</span>

              <h3>
                Connected
              </h3>

              <p>
                Product data loaded from
                /api/v1/products.
              </p>
            </article>
          </div>
        ) : null}
      </section>


      <section
        className="info-section"
        id="inputs"
      >
        <p className="info-section__index">
          02 / Inputs
        </p>

        <h2>
          Configured evidence groups
        </h2>

        {definition &&
        definition.input_groups.length >
          0 ? (
          <div className="method-grid">
            {definition.input_groups.map(
              (
                input,
                index,
              ) => (
                <article
                  key={input}
                >
                  <span>
                    {String(
                      index + 1,
                    ).padStart(
                      2,
                      "0",
                    )}
                  </span>

                  <h3>
                    {input}
                  </h3>
                </article>
              ),
            )}
          </div>
        ) : (
          <p className="info-copy">
            {loading
              ? "Loading configured input groups…"
              : "No input groups are currently exposed for this product."}
          </p>
        )}
      </section>


      <section
        className="info-section"
        id="outputs"
      >
        <p className="info-section__index">
          03 / Outputs
        </p>

        <h2>
          Configured audit outputs
        </h2>

        {definition &&
        definition.output_artifacts
          .length > 0 ? (
          <div className="method-grid">
            {definition.output_artifacts.map(
              (
                output,
                index,
              ) => (
                <article
                  key={output}
                >
                  <span>
                    {String(
                      index + 1,
                    ).padStart(
                      2,
                      "0",
                    )}
                  </span>

                  <h3>
                    {output}
                  </h3>
                </article>
              ),
            )}
          </div>
        ) : (
          <p className="info-copy">
            {loading
              ? "Loading configured outputs…"
              : "No output artifacts are currently exposed for this product."}
          </p>
        )}
      </section>


      <section
        className="info-section"
        id="configuration"
      >
        <p className="info-section__index">
          04 / Configuration
        </p>

        <h2>
          Commercial and audit policy
        </h2>

        <p className="info-copy">
          Product tiers and limits are
          supplied by the BIMAP backend.
          The frontend does not define
          pricing, rule versions, evidence
          limits or audit policy.
        </p>

        {product ? (
          <div className="method-grid">
            <article>
              <span>Tiers</span>

              <h3>
                {product.tiers.length}
              </h3>

              <p>
                Configured commercial
                tiers.
              </p>
            </article>

            <article>
              <span>Limits</span>

              <h3>
                {product.limits.length}
              </h3>

              <p>
                Configured product/tier
                limit scopes.
              </p>
            </article>
          </div>
        ) : null}
      </section>
    </InfoPage>
  );
}
