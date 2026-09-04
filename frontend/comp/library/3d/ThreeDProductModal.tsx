"use client";

import {
  useEffect,
  useRef,
} from "react";

import {
  FormatBadge,
} from "@/comp/ui/FormatBadge";

import {
  PurchaseModelButton,
} from "./PurchaseModelButton";

import type {
  RevitIfcInformation,
  ThreeDProduct,
} from "./types";


type Props = {
  product:
    ThreeDProduct | null;

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


function IfcInformation({
  ifc,
}: {
  ifc: RevitIfcInformation;
}) {

  return (
    <>
      <div
        className="model-spec-grid"
      >
        <div>
          <span>
            IFC schema
          </span>

          <strong>
            {
              displayValue(
                ifc.schema,
              )
            }
          </strong>
        </div>


        <div>
          <span>
            IFC entity
          </span>

          <strong>
            {
              displayValue(
                ifc.entity,
              )
            }
          </strong>
        </div>


        <div>
          <span>
            Predefined type
          </span>

          <strong>
            {
              displayValue(
                ifc.predefinedType,
              )
            }
          </strong>
        </div>


        <div>
          <span>
            Object type
          </span>

          <strong>
            {
              displayValue(
                ifc.objectType,
              )
            }
          </strong>
        </div>


        <div>
          <span>
            Type name
          </span>

          <strong>
            {
              displayValue(
                ifc.typeName,
              )
            }
          </strong>
        </div>


        <div>
          <span>
            Classification
          </span>

          <strong>
            {
              displayValue(
                ifc.classification,
              )
            }
          </strong>
        </div>
      </div>


      {ifc.properties &&
      ifc.properties.length > 0 ? (
        <div
          className="model-property-list"
        >
          <h4>
            IFC properties
          </h4>

          <dl>
            {ifc.properties.map(
              (property) => (
                <div
                  key={
                    property.name
                  }
                >
                  <dt>
                    {
                      property.name
                    }
                  </dt>

                  <dd>
                    {
                      property.value
                    }
                  </dd>
                </div>
              ),
            )}
          </dl>
        </div>
      ) : null}
    </>
  );
}


export function ThreeDProductModal({
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
          : "3D model details"
      }

      onClose={onClose}

      onMouseDown={(event) => {

        /*
         * Clicking the native
         * dialog backdrop.
         */
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

          <header
            className="model-modal__header"
          >
            <div>
              <p>
                {
                  product.category
                }
              </p>

              <h2>
                {
                  product.title
                }
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

            <section
              className="model-modal__intro"
            >
              <div
                className="badge-row"
              >
                {product.formats.map(
                  (format) => (
                    <FormatBadge
                      key={format}
                    >
                      {format}
                    </FormatBadge>
                  ),
                )}
              </div>


              <p>
                {
                  product.description
                }
              </p>


              <PurchaseModelButton
                product={product}
                compact
              />
            </section>


            <section
              className="model-modal__section"

              aria-labelledby=
                "model-renders-heading"
            >

              <div
                className="model-modal__section-heading"
              >
                <span>
                  01
                </span>

                <div>
                  <p>
                    Visual review
                  </p>

                  <h3
                    id=
                      "model-renders-heading"
                  >
                    Renders
                  </h3>
                </div>
              </div>


              <div
                className="model-render-stack"
              >
                {
                  product.renders
                    .length > 0
                  ? (
                    product.renders.map(
                      (
                        render,
                        index,
                      ) => (

                        <figure
                          className=
                            "model-render"

                          key={
                            `${product.id}-${index}`
                          }
                        >

                          {render.src ? (
                            <img
                              src={
                                render.src
                              }

                              alt={
                                render.alt
                              }

                              loading="lazy"
                            />
                          ) : (
                            <div
                              className=
                                "model-render__placeholder"
                            >
                              <span>
                                Render{" "}
                                {
                                  String(
                                    index + 1,
                                  ).padStart(
                                    2,
                                    "0",
                                  )
                                }
                              </span>

                              <p>
                                Image not
                                configured
                              </p>
                            </div>
                          )}


                          {render.caption ? (
                            <figcaption>
                              {
                                render.caption
                              }
                            </figcaption>
                          ) : null}

                        </figure>
                      ),
                    )
                  )
                  : (
                    <div
                      className=
                        "model-render__empty"
                    >
                      No renders have
                      been configured
                      for this product.
                    </div>
                  )
                }
              </div>
            </section>


            <section
              className="model-modal__section"

              aria-labelledby=
                "model-tech-heading"
            >

              <div
                className="model-modal__section-heading"
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
                      "model-tech-heading"
                  >
                    {
                      product.technical
                        .kind === "revit"
                        ? "Revit / IFC information"
                        : "3ds Max model information"
                    }
                  </h3>
                </div>
              </div>


              {
                product.technical
                  .kind === "revit"
                ? (
                  <>
                    <div
                      className="
                        model-spec-grid
                        model-spec-grid--summary
                      "
                    >
                      <div>
                        <span>
                          Revit version
                        </span>

                        <strong>
                          {
                            displayValue(
                              product
                                .technical
                                .revitVersion,
                            )
                          }
                        </strong>
                      </div>


                      <div>
                        <span>
                          Parametric
                        </span>

                        <strong>
                          {
                            displayValue(
                              product
                                .technical
                                .parametric,
                            )
                          }
                        </strong>
                      </div>
                    </div>


                    <IfcInformation
                      ifc={
                        product
                          .technical
                          .ifc
                      }
                    />
                  </>
                )
                : (
                  <div
                    className="model-spec-grid"
                  >

                    <div>
                      <span>
                        3ds Max version
                      </span>

                      <strong>
                        {
                          displayValue(
                            product
                              .technical
                              .maxVersion,
                          )
                        }
                      </strong>
                    </div>


                    <div>
                      <span>
                        Total vertices
                      </span>

                      <strong>
                        {
                          displayValue(
                            product
                              .technical
                              .vertices,
                          )
                        }
                      </strong>
                    </div>


                    <div>
                      <span>
                        Total polygons
                      </span>

                      <strong>
                        {
                          displayValue(
                            product
                              .technical
                              .polygons,
                          )
                        }
                      </strong>
                    </div>


                    <div>
                      <span>
                        Materials
                      </span>

                      <strong>
                        {
                          product
                            .technical
                            .materials
                            .length > 0
                          ? product
                              .technical
                              .materials
                              .length
                          : "Not configured"
                        }
                      </strong>
                    </div>
                  </div>
                )
              }


              {
                product.technical
                  .kind === "3ds-max" &&
                product.technical
                  .materials.length > 0
                ? (
                  <div
                    className="model-materials"
                  >
                    <h4>
                      Material list
                    </h4>

                    <ul>
                      {
                        product
                          .technical
                          .materials
                          .map(
                            (material) => (
                              <li
                                key={
                                  material
                                }
                              >
                                {
                                  material
                                }
                              </li>
                            ),
                          )
                      }
                    </ul>
                  </div>
                )
                : null
              }

            </section>
          </div>


          <footer
            className="model-modal__footer"
          >
            <div>
              <span>
                {
                  product.formats.join(
                    " · ",
                  )
                }
              </span>

              <span>
                {
                  product.priceLabel ??
                  "Price not configured"
                }
              </span>
            </div>


            <PurchaseModelButton
              product={product}
              compact
            />
          </footer>

        </div>
      ) : null}
    </dialog>
  );
}