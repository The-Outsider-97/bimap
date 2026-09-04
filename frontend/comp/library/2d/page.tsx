"use client";

import {
  useMemo,
  useState,
} from "react";

import {
  SiteShell,
} from "@/comp/shell/SiteShell";

import type {
  TocItem,
} from "@/lib/types";

import {
  getTwoDProducts,
} from "./catalog";

import {
  TwoDProductCard,
} from "./TwoDProductCard";

import {
  TwoDProductModal,
} from "./TwoDProductModal";

import type {
  TwoDProduct,
  TwoDProductKind,
} from "./types";


const toc:
  readonly TocItem[] = [
    {
      id:
        "catalog-overview",

      label:
        "2D library",
    },

    {
      id:
        "catalog-browse",

      label:
        "Search & filters",
    },

    {
      id:
        "catalog-products",

      label:
        "DWG content",
    },
  ];


type Filter =
  | "ALL"
  | TwoDProductKind;


const filters:
  readonly Filter[] = [
    "ALL",
    "single",
    "pack",
  ];


export default function TwoDLibraryPage() {

  const products =
    getTwoDProducts();


  const [
    query,
    setQuery,
  ] = useState("");


  const [
    activeFilter,
    setActiveFilter,
  ] =
    useState<Filter>(
      "ALL",
    );


  const [
    selectedProduct,
    setSelectedProduct,
  ] =
    useState<
      TwoDProduct | null
    >(null);


  const filtered =
    useMemo(() => {

      const normalized =
        query
          .trim()
          .toLowerCase();


      return products.filter(
        (product) => {

          const typeMatch =
            activeFilter ===
              "ALL" ||
            product.kind ===
              activeFilter;


          if (!typeMatch) {
            return false;
          }


          if (!normalized) {
            return true;
          }


          const searchable = [
            product.title,
            product.shortDescription,
            product.description,
            product.category,
            product.kind,
            ...product.tags,
          ]
            .join(" ")
            .toLowerCase();


          return searchable.includes(
            normalized,
          );
        },
      );

    }, [
      activeFilter,
      products,
      query,
    ]);


  return (
    <SiteShell
      toc={toc}

      pageDescription={
        "Browse individual DWG drawings and coordinated drawing packs, inspect drawing previews and technical information, and purchase reusable 2D content."
      }
    >

      <main
        className="
          catalog-page
          dwg-library-page
        "
      >

        {/* HERO */}

        <section
          className="catalog-hero"

          id=
            "catalog-overview"
        >

          <div
            className=
              "catalog-hero__grid"

            aria-hidden="true"
          />


          <div
            className="
              content-width
              catalog-hero__inner
            "
          >

            <div>

              <p
                className="eyebrow"
              >
                <span
                  aria-hidden="true"
                >
                  ●
                </span>

                BIMAP digital
                library / 2D
              </p>


              <h1>
                2D DWG
                <br />
                Library
              </h1>


              <p>
                Browse individual
                architectural drawing
                elements and coordinated
                DWG packs with static
                previews, detailed
                descriptions and
                technical drawing
                information.
              </p>

            </div>


            <div
              className=
                "catalog-hero__mark"

              aria-hidden="true"
            >
              <span />
              <span />

              <img
                src=
                  "/remy3design-mark.png"

                alt=""
              />
            </div>

          </div>

        </section>


        {/* SEARCH */}

        <section
          className=
            "catalog-toolbar"

          id=
            "catalog-browse"
        >

          <div
            className=
              "content-width"
          >

            <div
              className=
                "catalog-search"
            >

              <label
                htmlFor=
                  "two-d-search"
              >
                Search 2D content
              </label>


              <div
                className=
                  "catalog-search__field"
              >

                <span
                  aria-hidden="true"
                >
                  ⌕
                </span>


                <input
                  id=
                    "two-d-search"

                  type="search"

                  value={query}

                  onChange={(
                    event,
                  ) =>
                    setQuery(
                      event.target
                        .value,
                    )
                  }

                  placeholder={
                    "Search drawings, packs or categories"
                  }
                />

              </div>

            </div>


            <div
              className=
                "catalog-filters"

              aria-label=
                "Filter 2D content"
            >

              {filters.map(
                (filter) => (

                  <button
                    type="button"

                    key={filter}

                    data-active={
                      activeFilter ===
                      filter
                    }

                    onClick={() =>
                      setActiveFilter(
                        filter,
                      )
                    }
                  >

                    {
                      filter ===
                        "ALL"
                        ? "ALL"

                        : filter ===
                          "single"
                          ? "SINGLE"

                          : "PACK"
                    }

                  </button>

                ),
              )}

            </div>

          </div>

        </section>


        {/* PRODUCTS */}

        <section
          className="
            section
            catalog-results
          "

          id=
            "catalog-products"
        >

          <div
            className=
              "content-width"
          >

            <div
              className=
                "catalog-results__heading"
            >

              <div>

                <p
                  className=
                    "eyebrow"
                >
                  <span
                    aria-hidden="true"
                  >
                    ●
                  </span>

                  Browse content
                </p>


                <h2>
                  DWG drawings
                  &amp; packs
                </h2>

              </div>


              <span>

                {
                  filtered.length
                }{" "}

                {
                  filtered.length ===
                    1
                    ? "product"
                    : "products"
                }

              </span>

            </div>


            {
              filtered.length > 0
                ? (

                  <div
                    className=
                      "model-catalog-grid"
                  >

                    {filtered.map(
                      (product) => (

                        <TwoDProductCard
                          key={
                            product.id
                          }

                          product={
                            product
                          }

                          onOpen={
                            setSelectedProduct
                          }
                        />

                      ),
                    )}

                  </div>

                )
                : (

                  <div
                    className=
                      "catalog-empty"
                  >

                    <span>
                      00
                    </span>


                    <div>

                      <h3>
                        No matching
                        DWG products.
                      </h3>


                      <p>
                        Adjust the
                        search query or
                        selected product
                        type.
                      </p>

                    </div>

                  </div>

                )
            }

          </div>

        </section>

      </main>


      <TwoDProductModal
        product={
          selectedProduct
        }

        onClose={() =>
          setSelectedProduct(
            null,
          )
        }
      />

    </SiteShell>
  );
}