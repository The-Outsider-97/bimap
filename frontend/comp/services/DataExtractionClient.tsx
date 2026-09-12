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
  utcOffsetMinutes: number;
};


const EMPTY_DATASETS:
  readonly ExtractionDataset[] = [];


const DATASET_INFO: ReadonlyArray<{
  key: ExtractionDataset;
  title: string;
  description: string;
}> = [
  {
    key: "elements",
    title: "Elements",
    description:
      "Source objects, identities, names, classes or object types, and available hierarchy or container information.",
  },
  {
    key: "properties",
    title: "Properties",
    description:
      "Available source properties, attributes, metadata, and object-level values.",
  },
  {
    key: "quantities",
    title: "Quantities",
    description:
      "Available source-native or geometry-derived dimensional and quantitative values.",
  },
  {
    key: "materials",
    title: "Materials",
    description:
      "Available material identities, names, categories, descriptions, and source material metadata.",
  },
];


function fileExtension(
  filename: string,
): string {
  const index =
    filename.lastIndexOf(".");

  return index >= 0
    ? filename
        .slice(index)
        .toLowerCase()
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

  let result =
    value / 1024;

  let unit: (typeof units)[number] =
    units[0];

  for (
    let index = 1;
    index < units.length &&
    result >= 1024;
    index += 1
  ) {
    result /= 1024;
    unit = units[index];
  }

  return (
    `${result.toFixed(
      result >= 10 ? 1 : 2,
    )} ${unit}`
  );
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
      () => {
        URL.revokeObjectURL(
          url,
        );
      },
      0,
    );
  }
}


function fileFingerprint(
  file: File,
  datasets:
    readonly ExtractionDataset[],
  emailResult: boolean,
  projectName: string,
  utcOffsetMinutes: number,
): string {
  return [
    file.name,
    file.size,
    file.lastModified,
    file.type,
    [...datasets]
      .sort()
      .join(","),
    emailResult
      ? "email"
      : "download",
    projectName.trim(),
    utcOffsetMinutes,
  ].join(":");
}


function formatSourceFormat(
  value: string | null | undefined,
): string {
  if (!value) {
    return "—";
  }

  return value.toUpperCase();
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
  ] =
    useState(true);


  const [
    file,
    setFile,
  ] =
    useState<File | null>(
      null,
    );


  const [
    projectName,
    setProjectName,
  ] =
    useState("");


  const [
    datasets,
    setDatasets,
  ] =
    useState<
      readonly ExtractionDataset[]
    >([
      "elements",
      "properties",
      "quantities",
      "materials",
    ]);


  const [
    emailResult,
    setEmailResult,
  ] =
    useState(false);


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
        const loaded =
          await getDataExtractionCapabilities(
            controller.signal,
          );

        if (
          !controller.signal.aborted
        ) {
          setCapabilities(
            loaded,
          );
        }
      } catch (error) {
        if (
          !controller.signal.aborted
        ) {
          setCapabilities(null);

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

      abortRef.current
        ?.abort();
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
          setSummary(
            value,
          );
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


  /*
   * Build the accepted extension set once from
   * the capabilities actually advertised by
   * the backend.
   *
   * There is deliberately no hard-coded IFC
   * fallback.
   */
  const acceptedExtensions =
    useMemo(
      () =>
        Array.from(
          new Set(
            capabilities
              ?.sources
              .flatMap(
                (source) =>
                  source.extensions,
              )
              .map(
                (extension) =>
                  extension
                    .trim()
                    .toLowerCase(),
              )
              .filter(Boolean) ??
              [],
          ),
        ).sort(),
      [capabilities],
    );


  const acceptAttribute =
    acceptedExtensions.length > 0
      ? acceptedExtensions.join(
          ",",
        )
      : undefined;


  const supportedFormatsLabel =
    acceptedExtensions.length > 0
      ? acceptedExtensions.join(
          ", ",
        )
      : "—";


  /*
   * Resolve the selected source strictly
   * through advertised capabilities.
   *
   * No source format exists until the user
   * selects a supported file.
   */
  const sourceCapability =
    useMemo(() => {
      if (
        file === null ||
        capabilities === null
      ) {
        return null;
      }

      const extension =
        fileExtension(
          file.name,
        );

      return (
        capabilities.sources.find(
          (source) =>
            source.extensions.some(
              (candidate) =>
                candidate
                  .trim()
                  .toLowerCase() ===
                extension,
            ),
        ) ?? null
      );
    }, [
      capabilities,
      file,
    ]);


  const allowedDatasets =
    sourceCapability
      ?.datasets ??
    EMPTY_DATASETS;


  /*
   * Whenever the source format changes, retain
   * only datasets genuinely supported by that
   * source. If none of the previous selections
   * remain valid, select the source's complete
   * advertised dataset set.
   */
  useEffect(() => {
    if (
      sourceCapability ===
      null
    ) {
      return;
    }

    const supported =
      sourceCapability.datasets;

    setDatasets(
      (current) => {
        const filtered =
          current.filter(
            (dataset) =>
              supported.includes(
                dataset,
              ),
          );

        return filtered.length > 0
          ? filtered
          : supported;
      },
    );
  }, [
    sourceCapability,
  ]);


  const usage =
    summary
      ?.usage
      .dataExtraction ??
    null;


  const noAllowance =
    usage !== null &&
    !usage.unlimited &&
    usage.remaining !== null &&
    usage.remaining <= 0;


  const emailAvailable =
    capabilities
      ?.email_available ??
    false;


  useEffect(() => {
    if (
      !emailAvailable &&
      emailResult
    ) {
      setEmailResult(
        false,
      );
    }
  }, [
    emailAvailable,
    emailResult,
  ]);


  const datasetsAreSupported =
    datasets.length > 0 &&
    datasets.every(
      (dataset) =>
        allowedDatasets.includes(
          dataset,
        ),
    );


  const canSubmit =
    !loading &&
    status ===
      "signed-in" &&
    account !== null &&
    file !== null &&
    sourceCapability !== null &&
    datasetsAreSupported &&
    workState !==
      "working" &&
    !noAllowance;


  const resetAttempt =
    useCallback(() => {
      attemptRef.current =
        null;

      setResult(null);
      setMessage(null);

      setWorkState(
        "idle",
      );
    }, []);


  const onProjectChange =
    useCallback(
      (
        event:
          ChangeEvent<HTMLInputElement>,
      ) => {
        setProjectName(
          event.currentTarget.value,
        );

        resetAttempt();
      },
      [
        resetAttempt,
      ],
    );


  const onFileChange =
    useCallback(
      (
        event:
          ChangeEvent<HTMLInputElement>,
      ) => {
        const selected =
          event.currentTarget
            .files?.[0] ??
          null;

        if (
          selected === null
        ) {
          setFile(null);

          resetAttempt();
          return;
        }

        const extension =
          fileExtension(
            selected.name,
          );

        const capability =
          capabilities
            ?.sources
            .find(
              (source) =>
                source.extensions.some(
                  (
                    candidate,
                  ) =>
                    candidate
                      .trim()
                      .toLowerCase() ===
                    extension,
                ),
            );

        if (!capability) {
          setFile(null);

          resetAttempt();

          setWorkState(
            "error",
          );

          setMessage(
            acceptedExtensions.length >
              0
              ? (
                  "Unsupported model format. " +
                  `Supported file types: ${supportedFormatsLabel}.`
                )
              : (
                  "No model extraction formats are currently available."
                ),
          );

          event.currentTarget.value =
            "";

          return;
        }

        setFile(
          selected,
        );

        resetAttempt();
      },
      [
        acceptedExtensions.length,
        capabilities,
        resetAttempt,
        supportedFormatsLabel,
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
              /*
               * At least one dataset must remain
               * selected.
               */
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
      [
        resetAttempt,
      ],
    );


  const onEmailChange =
    useCallback(
      (
        event:
          ChangeEvent<HTMLInputElement>,
      ) => {
        setEmailResult(
          event.currentTarget
            .checked,
        );

        resetAttempt();
      },
      [
        resetAttempt,
      ],
    );


  const getAttempt =
    useCallback(
      (
        source: File,
      ): AttemptIdentity => {
        const utcOffsetMinutes =
          -new Date()
            .getTimezoneOffset();

        const fingerprint =
          fileFingerprint(
            source,
            datasets,
            emailResult,
            projectName,
            utcOffsetMinutes,
          );

        const existing =
          attemptRef.current;

        if (
          existing
            ?.fingerprint ===
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
            utcOffsetMinutes,
          };

        attemptRef.current =
          next;

        return next;
      },
      [
        datasets,
        emailResult,
        projectName,
      ],
    );


  const refreshUsage =
    useCallback(
      async () => {
        try {
          setSummary(
            await getAccountSummary(),
          );
        } catch {
          /*
           * A completed extraction remains
           * authoritative even if the account
           * usage refresh fails.
           */
        }
      },
      [],
    );


  const onSubmit =
    useCallback(
      async (
        event:
          FormEvent<HTMLFormElement>,
      ) => {
        event.preventDefault();

        if (
          !canSubmit ||
          file === null ||
          sourceCapability ===
            null
        ) {
          return;
        }

        const attempt =
          getAttempt(
            file,
          );

        const controller =
          new AbortController();

        abortRef.current
          ?.abort();

        abortRef.current =
          controller;

        setWorkState(
          "working",
        );

        setMessage(
          (
            "Uploading and extracting data from the " +
            `${sourceCapability.source_format.toUpperCase()} model. ` +
            "The package is returned only after the JSON and PDF artifacts are complete."
          ),
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
                project:
                  projectName.trim() ||
                  undefined,
                utcOffsetMinutes:
                  attempt
                    .utcOffsetMinutes,
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
            extracted
              .emailStatus ===
            "accepted"
          ) {
            setMessage(
              (
                "Data extraction completed. " +
                "The ZIP package was downloaded and the same package was accepted " +
                "for delivery to your account email."
              ),
            );
          } else if (
            extracted
              .emailStatus ===
            "failed"
          ) {
            setMessage(
              (
                "Data extraction completed and the ZIP package is ready, " +
                "but email delivery failed. You can still download the completed package below."
              ),
            );
          } else {
            setMessage(
              (
                "Data extraction completed. " +
                "The ZIP package containing the PDF report and JSON dataset is ready."
              ),
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
        projectName,
        refreshUsage,
        sourceCapability,
      ],
    );


  const allowanceLabel =
    useMemo(() => {
      if (
        usage === null
      ) {
        return "—";
      }

      if (
        usage.unlimited
      ) {
        return "Unlimited";
      }

      if (
        usage.remaining ===
          null ||
        usage.limit ===
          null
      ) {
        return "—";
      }

      return (
        `${usage.remaining} / ` +
        `${usage.limit} remaining`
      );
    }, [
      usage,
    ]);


  const displayedSourceFormat =
    result?.sourceFormat ??
    sourceCapability
      ?.source_format ??
    null;


  const displayedSourceSchema =
    result?.sourceSchema ??
    null;


  const displayedEntityCount =
    result?.entityCount ??
    null;


  const emailStatusLabel =
    result?.emailStatus ===
    "accepted"
      ? "Accepted for delivery"
      : result?.emailStatus ===
          "failed"
        ? "Delivery failed"
        : emailResult
          ? "Requested"
          : "Not requested";


  const inputDisabled =
    loading ||
    acceptedExtensions.length ===
      0 ||
    workState ===
      "working";


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
            <span
              aria-hidden="true"
            >
              ●
            </span>
            Data extraction
          </p>

          <h1>
            Extract structured BIM
            and model data.
          </h1>

          <p
            className={
              styles.lead
            }
          >
            Upload a supported BIM,
            CAD, or 3D model and select
            the datasets you need.
            BIMAP resolves the source
            format from the uploaded
            model, validates and scans
            it, extracts the available
            structured data, and
            returns one ZIP containing
            the JSON dataset and a
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
                <span>
                  01
                </span>

                <div>
                  <p>
                    Source model
                  </p>

                  <h2>
                    Upload model
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
                  accept={
                    acceptAttribute
                  }
                  onChange={
                    onFileChange
                  }
                  disabled={
                    inputDisabled
                  }
                />

                <strong>
                  {
                    file?.name ??
                    (
                      loading
                        ? "Loading supported formats…"
                        : "Choose a model"
                    )
                  }
                </strong>

                <span>
                  {
                    file
                      ? (
                          `${formatBytes(file.size)} · ` +
                          `${formatSourceFormat(
                            sourceCapability
                              ?.source_format,
                          )}`
                        )
                      : loading
                        ? (
                            "Reading extraction capabilities from BIMAP"
                          )
                        : acceptedExtensions.length >
                            0
                          ? (
                              `Supported: ${supportedFormatsLabel}`
                            )
                          : (
                              "No extraction source formats are currently available"
                            )
                  }
                </span>
              </label>


              <label
                className={
                  styles.projectField
                }
              >
                <span>
                  Project
                  <small>
                    Optional
                  </small>
                </span>

                <input
                  type="text"
                  value={
                    projectName
                  }
                  onChange={
                    onProjectChange
                  }
                  maxLength={
                    512
                  }
                  disabled={
                    workState ===
                    "working"
                  }
                  placeholder={
                    "Project name"
                  }
                  autoComplete={
                    "off"
                  }
                />
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
                      sourceCapability ===
                        null ||
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
                          onChange={() => {
                            toggleDataset(
                              item.key,
                            );
                          }}
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
                    {
                      emailAvailable
                        ? (
                            `Send the same ZIP package to ${
                              account
                                ?.email ??
                              "your BIMAP account email"
                            }.`
                          )
                        : (
                            "Email artifact delivery is not configured on this BIMAP deployment."
                          )
                    }
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
                  {
                    workState ===
                    "working"
                      ? "Extracting…"
                      : "Extract data"
                  }

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
                  onClick={() => {
                    abortRef
                      .current
                      ?.abort();
                  }}
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
                <span>
                  02
                </span>

                <div>
                  <p>
                    Extraction state
                  </p>

                  <h2>
                    {
                      workState ===
                      "complete"
                        ? "Complete"
                        : workState ===
                            "working"
                          ? "Processing"
                          : workState ===
                              "error"
                            ? "Action required"
                            : "Ready"
                    }
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
                      onClick={() => {
                        openAuth(
                          "login",
                        );
                      }}
                    >
                      Sign in
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        openAuth(
                          "signup",
                        );
                      }}
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
                    {
                      workState ===
                      "error"
                        ? (
                            "Extraction not completed"
                          )
                        : "Status"
                    }
                  </strong>

                  <p>
                    {
                      message
                    }
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
                    {
                      file?.name ??
                      "No file selected"
                    }
                  </dd>
                </div>


                <div>
                  <dt>
                    Format
                  </dt>

                  <dd>
                    {
                      formatSourceFormat(
                        displayedSourceFormat,
                      )
                    }
                  </dd>
                </div>


                <div>
                  <dt>
                    Datasets
                  </dt>

                  <dd>
                    {
                      datasets
                        .map(
                          (item) =>
                            (
                              item[0]
                                .toUpperCase() +
                              item.slice(
                                1,
                              )
                            ),
                        )
                        .join(", ")
                    }
                  </dd>
                </div>


                <div>
                  <dt>
                    Schema / version
                  </dt>

                  <dd>
                    {
                      displayedSourceSchema ??
                      "—"
                    }
                  </dd>
                </div>


                <div>
                  <dt>
                    Entities
                  </dt>

                  <dd>
                    {
                      displayedEntityCount ??
                      "—"
                    }
                  </dd>
                </div>


                <div>
                  <dt>
                    Package
                  </dt>

                  <dd>
                    {
                      result
                        ?.packageBytes !==
                        null &&
                      result
                        ?.packageBytes !==
                        undefined
                        ? formatBytes(
                            result.packageBytes,
                          )
                        : "—"
                    }
                  </dd>
                </div>


                <div>
                  <dt>
                    Email
                  </dt>

                  <dd>
                    {
                      emailStatusLabel
                    }
                  </dd>
                </div>


                <div>
                  <dt>
                    Package SHA-256
                  </dt>

                  <dd
                    className={
                      styles.hash
                    }
                  >
                    {
                      result
                        ?.packageSha256 ??
                      "—"
                    }
                  </dd>
                </div>
              </dl>


              {result ? (
                <button
                  type="button"
                  className={
                    styles.download
                  }
                  onClick={() => {
                    triggerDownload(
                      result,
                    );
                  }}
                >
                  Download{" "}
                  {
                    result.filename
                  }

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
              <span>
                JSON
              </span>

              <h3>
                Complete structured
                result
              </h3>

              <p>
                The JSON artifact
                carries the normalized
                extraction result,
                source identity,
                available project or
                scene metadata, units,
                counts, and the
                selected datasets.
              </p>
            </article>


            <article>
              <span>
                PDF
              </span>

              <h3>
                Human-readable
                summary
              </h3>

              <p>
                The PDF summarizes
                extraction identity,
                source integrity,
                available model
                information, dataset
                counts, object or class
                distribution, and
                source units where
                available.
              </p>
            </article>


            <article>
              <span>
                ZIP
              </span>

              <h3>
                One authoritative
                package
              </h3>

              <p>
                BIMAP downloads and,
                when requested and
                configured, emails the
                same completed ZIP
                package. The delivery
                path does not fabricate
                a second extraction
                result.
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
              Capability-driven model
              extraction.
            </h2>

            <p>
              BIMAP does not assume an
              IFC source. The selected
              model is matched against
              the extraction
              capabilities advertised
              by the backend. The
              authenticated workflow
              then validates, stages,
              hashes and scans the
              source, consumes the
              existing extraction
              entitlement, and produces
              the completed JSON/PDF
              package through the
              configured source
              adapter.
            </p>
          </div>
        </div>
      </section>
    </>
  );
}
