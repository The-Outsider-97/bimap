"use client";

import {
  useMemo,
  useState,
} from "react";

import {
  FormatBadge,
} from "@/comp/ui/FormatBadge";

import {
  SiteShell,
} from "@/comp/shell/SiteShell";

import type {
  TocItem,
} from "@/lib/types";

export type CatalogCategory = {
  index: string;
  title: string;
  description: string;
  formats: readonly string[];
  note: string;
};

type Props = {
  eyebrow: string;
  title: string;
  description: string;
  formats: readonly string[];
  categories:
    readonly CatalogCategory[];
  toc: readonly TocItem[];
};

export function CatalogPage({
  eyebrow,
  title,
  description,
  formats,
  categories,
  toc,
}: Props) {
  const [query, setQuery] =
    useState("");

  const [
    activeFormat,
    setActiveFormat,
  ] = useState("ALL");

  const filtered = useMemo(() => {
    const normalizedQuery =
      query.trim().toLowerCase();

    return categories.filter(
      (category) => {
        const formatMatch =
          activeFormat === "ALL" ||
          category.formats.includes(
            activeFormat,
          );

        const queryMatch =
          normalizedQuery.length === 0 ||
          category.title
            .toLowerCase()
            .includes(normalizedQuery) ||
          category.description
            .toLowerCase()
            .includes(normalizedQuery) ||
          category.note
            .toLowerCase()
            .includes(normalizedQuery) ||
          category.formats.some(
            (format) =>
              format
                .toLowerCase()
                .includes(
                  normalizedQuery,
                ),
          );

        return (
          formatMatch &&
          queryMatch
        );
      },
    );
  }, [
    activeFormat,
    categories,
    query,
  ]);

  return (
    <SiteShell
      toc={toc}
      pageDescription={description}
    >
      <main className="catalog-page">
        <section
          className="catalog-hero"
          id="catalog-overview"
        >
          <div
            className="catalog-hero__grid"
            aria-hidden="true"
          />

          <div className="
            content-width
            catalog-hero__inner
          ">
            <div>
              <p className="eyebrow">
                <span aria-hidden="true">
                  ●
                </span>

                {eyebrow}
              </p>

              <h1>{title}</h1>

              <p>
                {description}
              </p>
            </div>

            <div
              className="catalog-hero__mark"
              aria-hidden="true"
            >
              <span />
              <span />

              <img
                src="/remy3design-mark.png"
                alt=""
              />
            </div>
          </div>
        </section>

        <section
          className="catalog-toolbar"
          id="catalog-browse"
        >
          <div className="content-width">
            <div className="catalog-search">
              <label htmlFor="catalog-search">
                Search this catalog
              </label>

              <div className="catalog-search__field">
                <span aria-hidden="true">
                  ⌕
                </span>

                <input
                  id="catalog-search"
                  type="search"
                  value={query}
                  onChange={(event) =>
                    setQuery(
                      event.target.value,
                    )
                  }
                  placeholder="
                    Search formats or content types
                  "
                />
              </div>
            </div>

            <div
              className="catalog-filters"
              aria-label="
                Filter by file format
              "
            >
              {[
                "ALL",
                ...formats,
              ].map((format) => (
                <button
                  type="button"
                  key={format}
                  data-active={
                    activeFormat ===
                    format
                  }
                  onClick={() =>
                    setActiveFormat(
                      format,
                    )
                  }
                >
                  {format}
                </button>
              ))}
            </div>
          </div>
        </section>

        <section
          className="
            section
            catalog-results
          "
          id="catalog-categories"
        >
          <div className="content-width">
            <div className="catalog-results__heading">
              <div>
                <p className="eyebrow">
                  <span aria-hidden="true">
                    ●
                  </span>

                  Browse content
                </p>

                <h2>
                  Content categories
                </h2>
              </div>

              <span>
                {filtered.length} shown
              </span>
            </div>

            {filtered.length > 0 ? (
              <div className="catalog-grid">
                {filtered.map(
                  (category) => (
                    <article
                      className="catalog-card"
                      key={category.title}
                    >
                      <div className="catalog-card__top">
                        <span>
                          {category.index}
                        </span>

                        <div className="badge-row">
                          {category.formats.map(
                            (format) => (
                              <FormatBadge
                                key={format}
                              >
                                {format}
                              </FormatBadge>
                            ),
                          )}
                        </div>
                      </div>

                      <div
                        className="catalog-card__visual"
                        aria-hidden="true"
                      >
                        <span />
                        <span />
                        <span />
                      </div>

                      <div className="catalog-card__body">
                        <h3>
                          {category.title}
                        </h3>

                        <p>
                          {category.description}
                        </p>
                      </div>

                      <div className="catalog-card__foot">
                        <span>
                          {category.note}
                        </span>

                        <span aria-hidden="true">
                          ↗
                        </span>
                      </div>
                    </article>
                  ),
                )}
              </div>
            ) : (
              <div className="catalog-empty">
                <span>00</span>

                <div>
                  <h3>
                    No category matches
                    this view.
                  </h3>

                  <p>
                    Change the file-format
                    filter or search term.
                  </p>
                </div>
              </div>
            )}
          </div>
        </section>
      </main>
    </SiteShell>
  );
}