"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import {
  getAccountSummary,
  getApiErrorMessage,
  type AccountSummary,
} from "@/lib/account";
import { useAccount } from "@/comp/account/AccountProvider";
import {
  extractModelData,
  getDataExtractionCapabilities,
  type DataExtractionCapabilities,
  type DataExtractionDownload,
  type ExtractionDataset,
} from "@/lib/data-extraction";

import styles from "./DataExtractionClient.module.css";


type WorkState =
  | "idle"
  | "working"
  | "complete"
  | "error";

type AttemptIdentity = {
  fingerprint: string;
  extractionId: string;
  idempotencyKey: string;
};


const DATASET_INFO: ReadonlyArray<{
  key: ExtractionDataset;
  title: string;
  description: string;
}> = [
  {
    key: "elements",
    title: "Elements",
    description:
      "IFC identity, class, name, type, predefined type and spatial container.",
  },
  {
    key: "properties",
    title: "Properties",
    description:
      "Inherited and occurrence-level property-set names and values.",
  },
  {
    key: "quantities",
    title: "Quantities",
    description:
      "IFC quantity-set values associated with extracted products.",
  },
  {
    key: "materials",
    title: "Materials",
    description:
      "Associated material identities, names, categories and descriptions.",
  },
];




function fileExtension(filename: string): string {
  const index = filename.lastIndexOf(".");

  return index >= 0
    ? filename.slice(index).toLowerCase()
    : "";
}





function formatBytes(
  value: number,
): string {
  if (
    !Number.isFinite(value) ||
    value < 0
  ) {
    return "—";
  }

  if (value < 1024) {
    return `${value} B`;
  }

  const units = [
    "KB",
    "MB",
    "GB",
    "TB",
  ] as const;

  let result = value / 1024;
  let unit = units[0];

  for (
    let index = 1;
    index < units.length &&
    result >= 1024;
    index += 1
  ) {
    result /= 1024;
    unit = units[index];
  }

  return `${result.toFixed(
    result >= 10 ? 1 : 2,
  )} ${unit}`;
}


function triggerDownload(
  result: DataExtractionDownload,
): void {
  const url =
    URL.createObjectURL(
      result.blob,
    );

  try {
    const anchor =
      document.createElement("a");

    anchor.href = url;
    anchor.download =
      result.filename;
    anchor.rel = "noopener";

    document.body.appendChild(
      anchor,
    );
    anchor.click();
    anchor.remove();
  } finally {
    window.setTimeout(
      () =>
        URL.revokeObjectURL(
          url,
        ),
      0,
    );
  }
}


function fileFingerprint(
  file: File,
  datasets: readonly ExtractionDataset[],
  emailResult: boolean,
): string {
  return [
    file.name,
    file.size,
    file.lastModified,
    file.type,
    [...datasets].sort().join(","),
    emailResult ? "email" : "download",
  ].join(":");
}


export function DataExtractionClient() {
  const {
    account,
    status,
    openAuth,
  } = useAccount();

  const [
    summary,
    setSummary,
  ] =
    useState<AccountSummary | null>(
      null,
    );

  const [
    capabilities,
    setCapabilities,
  ] =
    useState<DataExtractionCapabilities | null>(
      null,
    );

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    file,
    setFile,
  ] =
    useState<File | null>(
      null,
    );

  const [
    datasets,
    setDatasets,
  ] =
    useState<readonly ExtractionDataset[]>(
      [
        "elements",
        "properties",
        "quantities",
        "materials",
      ],
    );

  const [
    emailResult,
    setEmailResult,
  ] = useState(false);

  const [
    workState,
    setWorkState,
  ] =
    useState<WorkState>(
      "idle",
    );

  const [
    message,
    setMessage,
  ] =
    useState<string | null>(
      null,
    );

  const [
    result,
    setResult,
  ] =
    useState<DataExtractionDownload | null>(
      null,
    );

  const attemptRef =
    useRef<AttemptIdentity | null>(
      null,
    );

  const abortRef =
    useRef<AbortController | null>(
      null,
    );


  useEffect(() => {
    const controller =
      new AbortController();

    async function loadCapabilities():
      Promise<void> {
      setLoading(true);

      try {
        setCapabilities(
          await getDataExtractionCapabilities(
            controller.signal,
          ),
        );
      } catch (error) {
        if (
          !controller.signal.aborted
        ) {
          setMessage(
            getApiErrorMessage(
              error,
            ),
          );
          setWorkState(
            "error",
          );
        }
      } finally {
        if (
          !controller.signal.aborted
        ) {
          setLoading(false);
        }
      }
    }

    void loadCapabilities();

    return () => {
      controller.abort();
      abortRef.current?.abort();
    };
  }, []);


  useEffect(() => {
    if (
      status !==
      "signed-in"
    ) {
      setSummary(null);
      return;
    }

    let active = true;

    void getAccountSummary()
      .then((value) => {
        if (active) {
          setSummary(value);
        }
      })
      .catch(() => {
        if (active) {
          setSummary(null);
        }
      });

    return () => {
      active = false;
    };
  }, [status]);


  const sourceCapability = useMemo(() => {
    if (file === null || capabilities === null) {
      return null;
    }

    const extension = fileExtension(file.name);

  const acceptedExtensions = useMemo(
    () =>
      Array.from(
        new Set(
          capabilities?.sources.flatMap(
            (source) => source.extensions,
          ) ?? [],
        ),
      )
        .sort()
        .join(","),
    [capabilities],
  );

  return (
      capabilities.sources.find(
        (source) =>
          source.extensions.includes(extension),
      ) ?? null
    );
  }, [capabilities, file]);

  const allowedDatasets =
    sourceCapability?.datasets ??
    [];

  useEffect(() => {
    if (
      allowedDatasets.length ===
      0
    ) {
      return;
    }

    setDatasets(
      (current) => {
        const filtered =
          current.filter(
            (dataset) =>
              allowedDatasets.includes(
                dataset,
              ),
          );

        return filtered.length
          ? filtered
          : allowedDatasets;
      },
    );
  }, [allowedDatasets]);


  const usage =
    summary?.usage
      .dataExtraction ??
    null;

  const noAllowance =
    usage !== null &&
    !usage.unlimited &&
    usage.remaining !== null &&
    usage.remaining <= 0;

  const emailAvailable =
    capabilities?.email_available ??
    false;

  useEffect(() => {
    if (
      !emailAvailable &&
      emailResult
    ) {
      setEmailResult(false);
    }
  }, [
    emailAvailable,
    emailResult,
  ]);


  const canSubmit =
    !loading &&
    status === "signed-in" &&
    account !== null &&
    file !== null &&
    datasets.length > 0 &&
    workState !== "working" &&
    !noAllowance;


  const resetAttempt =
    useCallback(() => {
      attemptRef.current =
        null;
      setResult(null);
      setMessage(null);
      setWorkState("idle");
    }, []);


  const onFileChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const selected =
        event.target.files?.[0] ?? null;

      if (selected === null) {
        setFile(null);
        resetAttempt();
        return;
      }

      const extension =
        fileExtension(selected.name);

      const capability =
        capabilities?.sources.find(
          (source) =>
            source.extensions.includes(extension),
        );

      if (!capability) {
        setFile(null);
        setResult(null);
        setWorkState("error");

        setMessage(
          acceptedExtensions
            ? `Unsupported model format. Supported file types: ${acceptedExtensions}.`
            : "No model extraction formats are currently available.",
        );

        event.target.value = "";
        return;
      }

      setFile(selected);
      resetAttempt();
    },
    [
      acceptedExtensions,
      capabilities,
      resetAttempt,
    ],
  );


  const toggleDataset =
    useCallback(
      (
        dataset:
          ExtractionDataset,
      ) => {
        setDatasets(
          (current) => {
            const exists =
              current.includes(
                dataset,
              );

            if (exists) {
              if (
                current.length ===
                1
              ) {
                return current;
              }

              return current.filter(
                (item) =>
                  item !==
                  dataset,
              );
            }

            return [
              ...current,
              dataset,
            ];
          },
        );

        resetAttempt();
      },
      [resetAttempt],
    );


  const onEmailChange =
    useCallback(
      (
        event:
          ChangeEvent<HTMLInputElement>,
      ) => {
        setEmailResult(
          event.target.checked,
        );
        resetAttempt();
      },
      [resetAttempt],
    );


  const getAttempt =
    useCallback(
      (
        source: File,
      ): AttemptIdentity => {
        const fingerprint =
          fileFingerprint(
            source,
            datasets,
            emailResult,
          );

        const existing =
          attemptRef.current;

        if (
          existing?.fingerprint ===
          fingerprint
        ) {
          return existing;
        }

        const next:
          AttemptIdentity = {
            fingerprint,
            extractionId:
              crypto.randomUUID(),
            idempotencyKey:
              crypto.randomUUID(),
          };

        attemptRef.current =
          next;

        return next;
      },
      [
        datasets,
        emailResult,
      ],
    );


  const refreshUsage =
    useCallback(async () => {
      try {
        setSummary(
          await getAccountSummary(),
        );
      } catch {
        // The completed extraction
        // remains authoritative.
      }
    }, []);


  const onSubmit =
    useCallback(
      async (
        event:
          FormEvent<HTMLFormElement>,
      ) => {
        event.preventDefault();

        if (
          !canSubmit ||
          file === null
        ) {
          return;
        }

        const attempt =
          getAttempt(file);

        const controller =
          new AbortController();

        abortRef.current?.abort();
        abortRef.current =
          controller;

        setWorkState(
          "working",
        );
        setMessage(
          "Uploading and extracting IFC data. The package is returned only after both JSON and PDF artifacts are complete.",
        );
        setResult(null);

        try {
          const extracted =
            await extractModelData(
              {
                file,
                datasets,
                extractionId:
                  attempt
                    .extractionId,
                idempotencyKey:
                  attempt
                    .idempotencyKey,
                emailResult,
                signal:
                  controller.signal,
              },
            );

          setResult(
            extracted,
          );
          setWorkState(
            "complete",
          );

          if (
            extracted.emailStatus ===
            "accepted"
          ) {
            setMessage(
              "Data extraction completed. The ZIP package was downloaded and the same package was accepted for delivery to your account email.",
            );
          } else if (
            extracted.emailStatus ===
            "failed"
          ) {
            setMessage(
              "Data extraction completed and the ZIP package is ready, but email delivery failed. You can still download the completed package below.",
            );
          } else {
            setMessage(
              "Data extraction completed. The ZIP package containing the PDF report and JSON dataset is ready.",
            );
          }

          triggerDownload(
            extracted,
          );

          await refreshUsage();
        } catch (error) {
          if (
            controller.signal
              .aborted
          ) {
            setWorkState(
              "idle",
            );
            setMessage(
              "Data extraction cancelled.",
            );
            return;
          }

          setWorkState(
            "error",
          );
          setMessage(
            getApiErrorMessage(
              error,
            ),
          );
        } finally {
          if (
            abortRef.current ===
            controller
          ) {
            abortRef.current =
              null;
          }
        }
      },
      [
        canSubmit,
        datasets,
        emailResult,
        file,
        getAttempt,
        refreshUsage,
      ],
    );


  const allowanceLabel =
    useMemo(() => {
      if (usage === null) {
        return "—";
      }

      if (usage.unlimited) {
        return "Unlimited";
      }

      if (
        usage.remaining ===
          null ||
        usage.limit === null
      ) {
        return "—";
      }

      return (
        `${usage.remaining} / ` +
        `${usage.limit} remaining`
      );
    }, [usage]);


  return (
    <>
      <section
        className={
          styles.hero
        }
        id="overview"
      >
        <div className="content-width">
          <p className="eyebrow">
            <span aria-hidden="true">
              ●
            </span>
            Data extraction
          </p>

          <h1>
            Extract structured
            BIM data from IFC.
          </h1>

          <p
            className={
              styles.lead
            }
          >
            Select the datasets you
            need. BIMAP validates and
            scans the IFC source,
            extracts the actual model
            data, then returns one ZIP
            containing the complete
            JSON dataset and a
            human-readable PDF report.
          </p>
        </div>
      </section>


      <section
        className={
          styles.section
        }
        id="extract"
      >
        <div className="content-width">
          <div
            className={
              styles.grid
            }
          >
            <form
              className={
                styles.panel
              }
              onSubmit={
                onSubmit
              }
            >
              <div
                className={
                  styles.panelHeading
                }
              >
                <span>01</span>
                <div>
                  <p>
                    Source model
                  </p>
                  <h2>
                    Upload IFC
                  </h2>
                </div>
              </div>

              <label
                className={
                  styles.dropzone
                }
              >
                <input
                  type="file"
                  accept=".ifc,application/octet-stream"
                  onChange={
                    onFileChange
                  }
                  disabled={
                    workState ===
                    "working"
                  }
                />

                <strong>
                  {file?.name ??
                    "Choose an IFC model"}
                </strong>

                <span>
                  {file
                    ? formatBytes(
                        file.size,
                      )
                    : "One .ifc file per extraction"}
                </span>
              </label>

              <fieldset
                className={
                  styles.datasets
                }
                disabled={
                  workState ===
                  "working"
                }
              >
                <legend>
                  Extraction datasets
                </legend>

                {DATASET_INFO
                  .filter(
                    (item) =>
                      allowedDatasets
                        .length ===
                        0 ||
                      allowedDatasets.includes(
                        item.key,
                      ),
                  )
                  .map(
                    (item) => (
                      <label
                        key={
                          item.key
                        }
                        data-active={
                          datasets.includes(
                            item.key,
                          )
                        }
                      >
                        <input
                          type="checkbox"
                          checked={
                            datasets.includes(
                              item.key,
                            )
                          }
                          onChange={() =>
                            toggleDataset(
                              item.key,
                            )
                          }
                        />
                        <span>
                          {
                            item.title
                          }
                        </span>
                        <small>
                          {
                            item.description
                          }
                        </small>
                      </label>
                    ),
                  )}
              </fieldset>

              <label
                className={
                  styles.emailOption
                }
                data-disabled={
                  !emailAvailable
                }
              >
                <input
                  type="checkbox"
                  checked={
                    emailResult
                  }
                  disabled={
                    !emailAvailable ||
                    workState ===
                      "working"
                  }
                  onChange={
                    onEmailChange
                  }
                />
                <span>
                  <strong>
                    Email completed
                    package
                  </strong>
                  <small>
                    {emailAvailable
                      ? `Send the same ZIP package to ${account?.email ?? "your BIMAP account email"}.`
                      : "Email artifact delivery is not configured on this BIMAP deployment."}
                  </small>
                </span>
              </label>

              <div
                className={
                  styles.submitRow
                }
              >
                <div>
                  <span>
                    Extraction
                    allowance
                  </span>
                  <strong>
                    {
                      allowanceLabel
                    }
                  </strong>
                </div>

                <button
                  type="submit"
                  disabled={
                    !canSubmit
                  }
                >
                  {workState ===
                  "working"
                    ? "Extracting…"
                    : "Extract data"}
                  <span
                    aria-hidden="true"
                  >
                    →
                  </span>
                </button>
              </div>

              {workState ===
              "working" ? (
                <button
                  type="button"
                  className={
                    styles.cancel
                  }
                  onClick={() =>
                    abortRef
                      .current
                      ?.abort()
                  }
                >
                  Cancel request
                </button>
              ) : null}
            </form>


            <aside
              className={
                styles.statusPanel
              }
              aria-live="polite"
            >
              <div
                className={
                  styles.panelHeading
                }
              >
                <span>02</span>
                <div>
                  <p>
                    Extraction state
                  </p>
                  <h2>
                    {workState ===
                    "complete"
                      ? "Complete"
                      : workState ===
                          "working"
                        ? "Processing"
                        : workState ===
                            "error"
                          ? "Action required"
                          : "Ready"}
                  </h2>
                </div>
              </div>

              {status ===
              "signed-out" ? (
                <div
                  className={
                    styles.notice
                  }
                  data-kind="warning"
                >
                  <strong>
                    Account required
                  </strong>

                  <p>
                    Data extraction
                    consumes the
                    extraction
                    allowance attached
                    to your BIMAP
                    account.
                  </p>

                  <div
                    className={
                      styles.authActions
                    }
                  >
                    <button
                      type="button"
                      onClick={() =>
                        openAuth(
                          "login",
                        )
                      }
                    >
                      Sign in
                    </button>

                    <button
                      type="button"
                      onClick={() =>
                        openAuth(
                          "signup",
                        )
                      }
                    >
                      Create account
                    </button>
                  </div>
                </div>
              ) : null}

              {message ? (
                <div
                  className={
                    styles.notice
                  }
                  data-kind={
                    workState ===
                    "error"
                      ? "error"
                      : "info"
                  }
                >
                  <strong>
                    {workState ===
                    "error"
                      ? "Extraction not completed"
                      : "Status"}
                  </strong>
                  <p>
                    {message}
                  </p>
                </div>
              ) : null}

              <dl
                className={
                  styles.metadata
                }
              >
                <div>
                  <dt>
                    Source
                  </dt>
                  <dd>
                    {file?.name ??
                      "No file selected"}
                  </dd>
                </div>

                <div>
                  <dt>
                    Datasets
                  </dt>
                  <dd>
                    {datasets
                      .map(
                        (item) =>
                          item[0]
                            .toUpperCase() +
                          item.slice(
                            1,
                          ),
                      )
                      .join(", ")}
                  </dd>
                </div>

                <div>
                  <dt>
                    IFC schema
                  </dt>
                  <dd>
                    {result
                      ?.ifcSchema ??
                      "—"}
                  </dd>
                </div>

                <div>
                  <dt>
                    Products
                  </dt>
                  <dd>
                    {result
                      ?.productCount ??
                      "—"}
                  </dd>
                </div>

                <div>
                  <dt>
                    Package
                  </dt>
                  <dd>
                    {result
                      ?.packageBytes !==
                      null &&
                    result
                      ?.packageBytes !==
                      undefined
                      ? formatBytes(
                          result.packageBytes,
                        )
                      : "—"}
                  </dd>
                </div>

                <div>
                  <dt>
                    Email
                  </dt>
                  <dd>
                    {result
                      ?.emailStatus ===
                    "accepted"
                      ? "Accepted for delivery"
                      : result
                            ?.emailStatus ===
                          "failed"
                        ? "Delivery failed"
                        : "Not requested"}
                  </dd>
                </div>

                <div>
                  <dt>
                    Package
                    SHA-256
                  </dt>
                  <dd
                    className={
                      styles.hash
                    }
                  >
                    {result
                      ?.packageSha256 ??
                      "—"}
                  </dd>
                </div>
              </dl>

              {result ? (
                <button
                  type="button"
                  className={
                    styles.download
                  }
                  onClick={() =>
                    triggerDownload(
                      result,
                    )
                  }
                >
                  Download{" "}
                  {result.filename}
                  <span
                    aria-hidden="true"
                  >
                    ↓
                  </span>
                </button>
              ) : null}
            </aside>
          </div>
        </div>
      </section>


      <section
        className={
          styles.section
        }
        id="package"
      >
        <div className="content-width">
          <div
            className={
              styles.explainerGrid
            }
          >
            <article>
              <span>JSON</span>
              <h3>
                Complete structured
                result
              </h3>
              <p>
                The JSON artifact
                carries the complete
                normalized extraction,
                source identity,
                project metadata,
                project units, counts
                and selected datasets.
              </p>
            </article>

            <article>
              <span>PDF</span>
              <h3>
                Human-readable
                summary
              </h3>
              <p>
                The PDF summarizes
                extraction identity,
                source integrity,
                project information,
                dataset counts, IFC
                class distribution and
                project units.
              </p>
            </article>

            <article>
              <span>ZIP</span>
              <h3>
                One authoritative
                package
              </h3>
              <p>
                BIMAP downloads and,
                when requested, emails
                the same completed ZIP
                bytes. The email path
                does not regenerate a
                second result.
              </p>
            </article>
          </div>
        </div>
      </section>


      <section
        className={
          styles.section
        }
        id="integrity"
      >
        <div className="content-width">
          <div
            className={
              styles.integrity
            }
          >
            <p className="eyebrow">
              <span
                aria-hidden="true"
              >
                ●
              </span>
              Integrity
            </p>

            <h2>
              Actual IFC data,
              governed delivery.
            </h2>

            <p>
              BIMAP authenticates
              before parsing the
              multipart body, stages
              and hashes the source,
              requires a clean malware
              verdict, preflights the
              IFC, consumes the
              existing data-extraction
              entitlement, and only
              then produces the final
              JSON/PDF package.
            </p>
          </div>
        </div>
      </section>
    </>
  );
}
