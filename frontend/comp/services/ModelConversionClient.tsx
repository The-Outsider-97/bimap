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
  convertModel,
  getModelConversionCapabilities,
  type ModelConversionCapabilities,
  type ModelConversionDownload,
  type ModelTargetFormat,
} from "@/lib/model-conversion";

import styles from "./ModelConversionClient.module.css";


type WorkState =
  | "idle"
  | "working"
  | "complete"
  | "error";


type AttemptIdentity = {
  fingerprint: string;
  conversionId: string;
  idempotencyKey: string;
};


const EMPTY_TARGETS:
  readonly ModelTargetFormat[] = [];


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

  if (
    value < 1024
  ) {
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
  result: ModelConversionDownload,
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
  target:
    ModelTargetFormat,
): string {
  return [
    file.name,
    file.size,
    file.lastModified,
    file.type,
    target,
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


function targetDescription(
  target:
    ModelTargetFormat,
): string {
  switch (target) {
    case "glb":
      return (
        "Binary glTF for web, real-time visualization, " +
        "and downstream 3D workflows."
      );

    case "obj":
      return (
        "OBJ geometry for broad interchange workflows; " +
        "associated generated sidecars are delivered together when applicable."
      );
  }
}


export function ModelConversionClient() {
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
    useState<ModelConversionCapabilities | null>(
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


  /*
   * GLB is merely the initial internal output
   * preference. It is not a source-format
   * default and is never shown as an available
   * target unless the selected source actually
   * advertises it.
   */
  const [
    targetFormat,
    setTargetFormat,
  ] =
    useState<ModelTargetFormat>(
      "glb",
    );


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
    useState<ModelConversionDownload | null>(
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
          await getModelConversionCapabilities(
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
  }, [
    status,
  ]);


  /*
   * Supported input extensions are defined
   * exclusively by the backend capability
   * response.
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
      [
        capabilities,
      ],
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
   * Resolve the selected model against the
   * backend capability catalogue.
   *
   * There is no IFC fallback.
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


  const targets =
    sourceCapability
      ?.target_formats ??
    EMPTY_TARGETS;


  /*
   * If the selected source does not support
   * the current target preference, use the
   * first target explicitly advertised for
   * that source.
   */
  useEffect(() => {
    if (
      targets.length ===
      0
    ) {
      return;
    }

    if (
      !targets.includes(
        targetFormat,
      )
    ) {
      setTargetFormat(
        targets[0],
      );
    }
  }, [
    targetFormat,
    targets,
  ]);


  const usage =
    summary
      ?.usage
      .conversion ??
    null;


  const noAllowance =
    usage !== null &&
    !usage.unlimited &&
    usage.remaining !== null &&
    usage.remaining <= 0;


  const canSubmit =
    !loading &&
    status ===
      "signed-in" &&
    account !== null &&
    file !== null &&
    sourceCapability !== null &&
    targets.includes(
      targetFormat,
    ) &&
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
                  "No model conversion formats are currently available."
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


  /*
   * This callback was referenced by the
   * original JSX but absent from the
   * component implementation.
   */
  const onTargetChange =
    useCallback(
      (
        next:
          ModelTargetFormat,
      ) => {
        if (
          !targets.includes(
            next,
          )
        ) {
          return;
        }

        setTargetFormat(
          next,
        );

        resetAttempt();
      },
      [
        resetAttempt,
        targets,
      ],
    );


  const getAttempt =
    useCallback(
      (
        source: File,
        target:
          ModelTargetFormat,
      ): AttemptIdentity => {
        const fingerprint =
          fileFingerprint(
            source,
            target,
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
            conversionId:
              crypto.randomUUID(),
            idempotencyKey:
              crypto.randomUUID(),
          };

        attemptRef.current =
          next;

        return next;
      },
      [],
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
           * Conversion success remains
           * authoritative even if account
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
            targetFormat,
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
            "Uploading and converting the " +
            `${sourceCapability.source_format.toUpperCase()} model to ` +
            `${targetFormat.toUpperCase()}. ` +
            "BIMAP reports completion only after the output artifact has been produced."
          ),
        );

        setResult(null);

        try {
          const converted =
            await convertModel(
              {
                file,
                targetFormat,
                conversionId:
                  attempt
                    .conversionId,
                idempotencyKey:
                  attempt
                    .idempotencyKey,
                signal:
                  controller.signal,
              },
            );

          setResult(
            converted,
          );

          setWorkState(
            "complete",
          );

          setMessage(
            (
              "Conversion completed. " +
              "The generated artifact is ready to download."
            ),
          );

          triggerDownload(
            converted,
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
              "Conversion cancelled.",
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
        file,
        getAttempt,
        refreshUsage,
        sourceCapability,
        targetFormat,
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


  const displayedTargetFormat =
    file !== null &&
    targets.includes(
      targetFormat,
    )
      ? targetFormat
          .toUpperCase()
      : "—";


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
            Model conversion
          </p>

          <h1>
            Convert BIM and 3D
            geometry into reusable
            delivery formats.
          </h1>

          <p
            className={
              styles.lead
            }
          >
            Upload one supported BIM,
            CAD, or 3D model. BIMAP
            resolves its source format
            from the configured
            conversion capabilities,
            shows only valid output
            formats for that source,
            validates and scans the
            model, and returns the
            generated artifact through
            the authenticated account
            workflow.
          </p>
        </div>
      </section>


      <section
        className={
          styles.section
        }
        id="convert"
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
                            "Reading conversion capabilities from BIMAP"
                          )
                        : acceptedExtensions.length >
                            0
                          ? (
                              `Supported: ${supportedFormatsLabel}`
                            )
                          : (
                              "No conversion source formats are currently available"
                            )
                  }
                </span>
              </label>


              <fieldset
                className={
                  styles.targets
                }
                disabled={
                  workState ===
                  "working"
                }
              >
                <legend>
                  Output format
                </legend>

                {targets.map(
                  (target) => (
                    <label
                      key={
                        target
                      }
                      data-active={
                        targetFormat ===
                        target
                      }
                    >
                      <input
                        type="radio"
                        name="target-format"
                        value={
                          target
                        }
                        checked={
                          targetFormat ===
                          target
                        }
                        onChange={() => {
                          onTargetChange(
                            target,
                          );
                        }}
                      />

                      <span>
                        {
                          target
                            .toUpperCase()
                        }
                      </span>

                      <small>
                        {
                          targetDescription(
                            target,
                          )
                        }
                      </small>
                    </label>
                  ),
                )}
              </fieldset>


              <div
                className={
                  styles.submitRow
                }
              >
                <div>
                  <span>
                    Conversion
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
                      ? "Converting…"
                      : "Convert model"
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
                    Conversion state
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
                    Model conversion
                    consumes the
                    conversion allowance
                    attached to your
                    BIMAP account.
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
                            "Conversion not completed"
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
                    Target
                  </dt>

                  <dd>
                    {
                      displayedTargetFormat
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
                    Output size
                  </dt>

                  <dd>
                    {
                      result
                        ?.outputBytes !==
                        null &&
                      result
                        ?.outputBytes !==
                        undefined
                        ? formatBytes(
                            result.outputBytes,
                          )
                        : "—"
                    }
                  </dd>
                </div>


                <div>
                  <dt>
                    Output SHA-256
                  </dt>

                  <dd
                    className={
                      styles.hash
                    }
                  >
                    {
                      result
                        ?.outputSha256 ??
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
        id="formats"
      >
        <div className="content-width">
          <div
            className={
              styles.explainerGrid
            }
          >
            <article>
              <span>
                SOURCE
              </span>

              <h3>
                Capability-driven
                intake
              </h3>

              <p>
                BIMAP does not select
                IFC or another source
                format by default.
                Supported uploads are
                read from the backend
                capability catalogue,
                and the selected file
                determines which
                conversion adapter and
                target formats are
                available.
              </p>
            </article>


            <article>
              <span>
                GLB
              </span>

              <h3>
                Compact 3D delivery
              </h3>

              <p>
                Where supported by the
                selected source, GLB
                packages geometry and
                material information in
                a binary glTF artifact
                suitable for web,
                real-time, and
                downstream 3D
                workflows.
              </p>
            </article>


            <article>
              <span>
                OBJ
              </span>

              <h3>
                Geometry interchange
              </h3>

              <p>
                Where supported by the
                selected source, OBJ
                provides a broadly
                compatible geometry
                interchange format.
                Generated supporting
                files are packaged
                together when the
                configured converter
                requires them.
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
              No source default. No
              fabricated format support.
            </h2>

            <p>
              BIMAP matches the uploaded
              file against the actual
              conversion capabilities
              configured on the backend.
              Only supported
              source/target pairs are
              presented. The source is
              validated before the
              existing conversion
              entitlement is consumed,
              and the completed response
              exposes source and output
              integrity metadata without
              simulating unsupported
              model processing.
            </p>
          </div>
        </div>
      </section>
    </>
  );
}