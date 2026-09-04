"use client";

import {
  useEffect,
  useRef,
} from "react";

import {
  FormatBadge,
} from "@/comp/ui/FormatBadge";

import {
  PurchaseAssetButton,
} from "@/comp/library/shared/PurchaseAssetButton";

import type {
  TwoDProduct,
} from "./types";


type Props = {
  product:
    TwoDProduct | null;

  onClose: () => void;
};


const numberFormatter =
  new Intl.NumberFormat(
    "en-US",
  );


function displayValue(
  value:
    | string
    | number
    | boolean
    | undefined,
) {

  if (
    value === undefined ||
    value === ""
  ) {
    return "Not configured";
  }


  if (
    typeof value === "boolean"
  ) {
    return value
      ? "Yes"
      : "No";
  }


  if (
    typeof value === "number"
  ) {
    return numberFormatter.format(
      value,
    );
  }


  return value;
}


export function TwoDProductModal({
  product,
  onClose,
}: Props) {

  const dialogRef =
    useRef<HTMLDialogElement>(
      null,
    );


  useEffect(() => {

    const dialog =
      dialogRef.current;


    if (!dialog) {
      return;
    }


    if (product) {

      if (!dialog.open) {
        dialog.showModal();
      }

      document.body.classList.add(
        "is-model-modal-open",
      );

      return;
    }


    if (dialog.open) {
      dialog.close();
    }


    document.body.classList.remove(
      "is-model-modal-open",
    );

  }, [product]);


  useEffect(() => {

    return () => {

      document.body.classList.remove(
        "is-model-modal-open",
      );

    };

  }, []);


  return (
    <dialog
      ref={dialogRef}

      className="model-modal"

      aria-label={
        product
          ? `${product.title} product details`
          : "DWG product details"
      }

      onClose={
        onClose
      }

      onMouseDown={(event) => {

        if (
          event.target ===
          event.currentTarget
        ) {
          event.currentTarget.close();
        }
      }}
    >

      {product ? (

        <div
          className="model-modal__shell"
        >

          {/* HEADER */}

          <header
            className="model-modal__header"
          >
            <div>
              <p>
                {product.category}
              </p>

              <h2>
                {product.title}
              </h2>
            </div>


            <button
              type="button"

              className="model-modal__close"

              aria-label=
                "Close product details"

              onClick={() =>
                dialogRef.current
                  ?.close()
              }
            >
              <span />
              <span />
            </button>
          </header>


          <div
            className="model-modal__body"
          >

            {/* DESCRIPTION */}

            <section
              className="model-modal__intro"
            >

              <div
                className="badge-row"
              >
                {product.tags.map(
                  (tag) => (
                    <FormatBadge
                      key={tag}
                    >
                      {tag}
                    </FormatBadge>
                  ),
                )}
              </div>


              <p>
                {product.description}
              </p>


              <PurchaseAssetButton
                title={
                  product.title
                }

                label={
                  product.kind ===
                    "pack"
                    ? "Purchase DWG pack"
                    : "Purchase DWG"
                }

                purchaseHref={
                  product.purchaseHref
                }

                priceLabel={
                  product.priceLabel
                }

                compact
              />

            </section>


            {/* VERTICAL PREVIEW GALLERY */}

            <section
              className="model-modal__section"

              aria-labelledby=
                "dwg-previews-heading"
            >

              <div
                className=
                  "model-modal__section-heading"
              >
                <span>
                  01
                </span>

                <div>
                  <p>
                    Drawing review
                  </p>

                  <h3
                    id=
                      "dwg-previews-heading"
                  >
                    Drawing previews
                  </h3>
                </div>
              </div>


              <div
                className="model-render-stack"
              >

                {
                  product.renders.length > 0
                  ? (

                    product.renders.map(
                      (
                        render,
                        index,
                      ) => (

                        <figure
                          className="model-render"

                          key={
                            `${product.id}-${index}`
                          }
                        >

                          {
                            render.src
                            ? (

                              <img
                                src={
                                  render.src
                                }

                                alt={
                                  render.alt
                                }

                                loading="lazy"
                              />

                            )
                            : (

                              <div
                                className=
                                  "dwg-render__placeholder"
                              >

                                <div
                                  className=
                                    "dwg-render__linework"
                                  aria-hidden="true"
                                >
                                  <span />
                                  <span />
                                  <span />
                                  <span />
                                </div>


                                <p>
                                  Drawing preview{" "}

                                  {
                                    String(
                                      index + 1,
                                    ).padStart(
                                      2,
                                      "0",
                                    )
                                  }
                                </p>

                              </div>

                            )
                          }


                          {
                            render.caption
                            ? (

                              <figcaption>
                                {
                                  render.caption
                                }
                              </figcaption>

                            )
                            : null
                          }

                        </figure>

                      ),
                    )

                  )
                  : (

                    <div
                      className="model-render__empty"
                    >
                      No drawing previews
                      have been configured
                      for this product.
                    </div>

                  )
                }

              </div>

            </section>


            {/* DWG INFORMATION */}

            <section
              className="model-modal__section"

              aria-labelledby=
                "dwg-information-heading"
            >

              <div
                className=
                  "model-modal__section-heading"
              >
                <span>
                  02
                </span>

                <div>
                  <p>
                    Technical data
                  </p>

                  <h3
                    id=
                      "dwg-information-heading"
                  >
                    DWG information
                  </h3>
                </div>
              </div>


              <div
                className="model-spec-grid"
              >

                <div>
                  <span>
                    DWG version
                  </span>

                  <strong>
                    {
                      displayValue(
                        product
                          .technical
                          .dwgVersion,
                      )
                    }
                  </strong>
                </div>


                <div>
                  <span>
                    Units
                  </span>

                  <strong>
                    {
                      displayValue(
                        product
                          .technical
                          .units,
                      )
                    }
                  </strong>
                </div>


                <div>
                  <span>
                    Drawing scale
                  </span>

                  <strong>
                    {
                      displayValue(
                        product
                          .technical
                          .drawingScale,
                      )
                    }
                  </strong>
                </div>


                <div>
                  <span>
                    Layers
                  </span>

                  <strong>
                    {
                      displayValue(
                        product
                          .technical
                          .layerCount,
                      )
                    }
                  </strong>
                </div>


                <div>
                  <span>
                    DWG files
                  </span>

                  <strong>
                    {
                      displayValue(
                        product
                          .technical
                          .fileCount,
                      )
                    }
                  </strong>
                </div>


                <div>
                  <span>
                    Model space
                  </span>

                  <strong>
                    {
                      displayValue(
                        product
                          .technical
                          .modelSpace,
                      )
                    }
                  </strong>
                </div>


                <div>
                  <span>
                    Paper-space layouts
                  </span>

                  <strong>
                    {
                      displayValue(
                        product
                          .technical
                          .paperSpaceLayouts,
                      )
                    }
                  </strong>
                </div>

              </div>

            </section>

          </div>


          {/* STICKY PURCHASE FOOTER */}

          <footer
            className="model-modal__footer"
          >

            <div>
              <span>
                DWG
              </span>

              <span>
                {
                  product.priceLabel ??
                  "Price not configured"
                }
              </span>
            </div>


            <PurchaseAssetButton
              title={
                product.title
              }

              label={
                product.kind ===
                  "pack"
                  ? "Purchase DWG pack"
                  : "Purchase DWG"
              }

              purchaseHref={
                product.purchaseHref
              }

              priceLabel={
                product.priceLabel
              }

              compact
            />

          </footer>

        </div>

      ) : null}

    </dialog>
  );
}