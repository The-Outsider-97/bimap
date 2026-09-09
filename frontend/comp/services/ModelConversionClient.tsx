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


function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) {
    return "—";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KB", "MB", "GB", "TB"] as const;
  let result = value / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && result >= 1024; index += 1) {
    result /= 1024;
    unit = units[index];
  }
  return `${result.toFixed(result >= 10 ? 1 : 2)} ${unit}`;
}


function triggerDownload(result: ModelConversionDownload): void {
  const url = URL.createObjectURL(result.blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = result.filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}


function fileFingerprint(file: File, target: ModelTargetFormat): string {
  return [
    file.name,
    file.size,
    file.lastModified,
    file.type,
    target,
  ].join(":");
}


export function ModelConversionClient() {
  const { account, status, openAuth } = useAccount();
  const [summary, setSummary] = useState<AccountSummary | null>(null);
  const [capabilities, setCapabilities] =
    useState<ModelConversionCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [targetFormat, setTargetFormat] =
    useState<ModelTargetFormat>("glb");
  const [workState, setWorkState] = useState<WorkState>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [result, setResult] = useState<ModelConversionDownload | null>(null);
  const attemptRef = useRef<AttemptIdentity | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function loadCapabilities(): Promise<void> {
      setLoading(true);
      try {
        setCapabilities(
          await getModelConversionCapabilities(controller.signal),
        );
      } catch (error) {
        if (!controller.signal.aborted) {
          setMessage(getApiErrorMessage(error));
          setWorkState("error");
        }
      } finally {
        if (!controller.signal.aborted) {
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
    if (status !== "signed-in") {
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

  const sourceCapability = capabilities?.sources.find(
    (entry) => entry.source_format === "ifc",
  );

  const targets = sourceCapability?.target_formats ?? [];

  useEffect(() => {
    if (targets.length > 0 && !targets.includes(targetFormat)) {
      setTargetFormat(targets[0]);
    }
  }, [targetFormat, targets]);

  const usage = summary?.usage.conversion ?? null;
  const noAllowance =
    usage !== null &&
    !usage.unlimited &&
    usage.remaining !== null &&
    usage.remaining <= 0;

  const canSubmit =
    !loading &&
    status === "signed-in" &&
    account !== null &&
    file !== null &&
    targets.includes(targetFormat) &&
    workState !== "working" &&
    !noAllowance;

  const resetAttempt = useCallback(() => {
    attemptRef.current = null;
    setResult(null);
    setMessage(null);
    setWorkState("idle");
  }, []);

  const onFileChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const selected = event.target.files?.[0] ?? null;
      if (selected !== null && !selected.name.toLowerCase().endsWith(".ifc")) {
        setFile(null);
        attemptRef.current = null;
        setResult(null);
        setWorkState("error");
        setMessage("Choose an IFC (.ifc) source model.");
        event.target.value = "";
        return;
      }
      setFile(selected);
      resetAttempt();
    },
    [resetAttempt],
  );

  const onTargetChange = useCallback(
    (next: ModelTargetFormat) => {
      setTargetFormat(next);
      resetAttempt();
    },
    [resetAttempt],
  );

  const getAttempt = useCallback(
    (source: File, target: ModelTargetFormat): AttemptIdentity => {
      const fingerprint = fileFingerprint(source, target);
      const existing = attemptRef.current;
      if (existing?.fingerprint === fingerprint) {
        return existing;
      }
      const next: AttemptIdentity = {
        fingerprint,
        conversionId: crypto.randomUUID(),
        idempotencyKey: crypto.randomUUID(),
      };
      attemptRef.current = next;
      return next;
    },
    [],
  );

  const refreshUsage = useCallback(async () => {
    try {
      setSummary(await getAccountSummary());
    } catch {
      // Conversion success remains authoritative even if summary refresh fails.
    }
  }, []);

  const onSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!canSubmit || file === null) {
        return;
      }

      const attempt = getAttempt(file, targetFormat);
      const controller = new AbortController();
      abortRef.current?.abort();
      abortRef.current = controller;

      setWorkState("working");
      setMessage("Uploading and converting the IFC model. BIMAP reports completion only after the artifact is produced.");
      setResult(null);

      try {
        const converted = await convertModel({
          file,
          targetFormat,
          conversionId: attempt.conversionId,
          idempotencyKey: attempt.idempotencyKey,
          signal: controller.signal,
        });
        setResult(converted);
        setWorkState("complete");
        setMessage("Conversion completed. The generated artifact is ready to download.");
        triggerDownload(converted);
        await refreshUsage();
      } catch (error) {
        if (controller.signal.aborted) {
          setWorkState("idle");
          setMessage("Conversion cancelled.");
          return;
        }
        setWorkState("error");
        setMessage(getApiErrorMessage(error));
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
      }
    },
    [canSubmit, file, getAttempt, refreshUsage, targetFormat],
  );

  const allowanceLabel = useMemo(() => {
    if (usage === null) {
      return "—";
    }
    if (usage.unlimited) {
      return "Unlimited";
    }
    if (usage.remaining === null || usage.limit === null) {
      return "—";
    }
    return `${usage.remaining} / ${usage.limit} remaining`;
  }, [usage]);

  return (
    <>
      <section className={styles.hero} id="overview">
        <div className="content-width">
          <p className="eyebrow"><span aria-hidden="true">●</span>Model conversion</p>
          <h1>Convert IFC geometry into reusable delivery formats.</h1>
          <p className={styles.lead}>
            Upload one IFC model, choose the required output, and BIMAP validates,
            scans, converts, and returns the generated artifact through the authenticated
            account workflow.
          </p>
        </div>
      </section>

      <section className={styles.section} id="convert">
        <div className="content-width">
          <div className={styles.grid}>
            <form className={styles.panel} onSubmit={onSubmit}>
              <div className={styles.panelHeading}>
                <span>01</span>
                <div>
                  <p>Source model</p>
                  <h2>Upload IFC</h2>
                </div>
              </div>

              <label className={styles.dropzone}>
                <input
                  type="file"
                  accept=".ifc,application/octet-stream"
                  onChange={onFileChange}
                  disabled={workState === "working"}
                />
                <strong>{file?.name ?? "Choose an IFC model"}</strong>
                <span>
                  {file ? formatBytes(file.size) : "One .ifc file per conversion"}
                </span>
              </label>

              <fieldset className={styles.targets} disabled={workState === "working"}>
                <legend>Output format</legend>
                {targets.map((target) => (
                  <label key={target} data-active={targetFormat === target}>
                    <input
                      type="radio"
                      name="target-format"
                      value={target}
                      checked={targetFormat === target}
                      onChange={() => onTargetChange(target)}
                    />
                    <span>{target.toUpperCase()}</span>
                    <small>
                      {target === "glb"
                        ? "Binary glTF for web, real-time and downstream 3D workflows."
                        : "OBJ geometry with MTL material data, delivered as a ZIP package."}
                    </small>
                  </label>
                ))}
              </fieldset>

              <div className={styles.submitRow}>
                <div>
                  <span>Conversion allowance</span>
                  <strong>{allowanceLabel}</strong>
                </div>
                <button type="submit" disabled={!canSubmit}>
                  {workState === "working" ? "Converting…" : "Convert model"}
                  <span aria-hidden="true">→</span>
                </button>
              </div>

              {workState === "working" ? (
                <button
                  type="button"
                  className={styles.cancel}
                  onClick={() => abortRef.current?.abort()}
                >
                  Cancel request
                </button>
              ) : null}
            </form>

            <aside className={styles.statusPanel} aria-live="polite">
              <div className={styles.panelHeading}>
                <span>02</span>
                <div>
                  <p>Conversion state</p>
                  <h2>{workState === "complete" ? "Complete" : workState === "working" ? "Processing" : workState === "error" ? "Action required" : "Ready"}</h2>
                </div>
              </div>

              {status === "signed-out" ? (
                <div className={styles.notice} data-kind="warning">
                  <strong>Account required</strong>
                  <p>Model conversion consumes the conversion allowance attached to your BIMAP account.</p>
                  <div className={styles.authActions}>
                    <button type="button" onClick={() => openAuth("login")}>Sign in</button>
                    <button type="button" onClick={() => openAuth("signup")}>Create account</button>
                  </div>
                </div>
              ) : null}

              {message ? (
                <div className={styles.notice} data-kind={workState === "error" ? "error" : "info"}>
                  <strong>{workState === "error" ? "Conversion not completed" : "Status"}</strong>
                  <p>{message}</p>
                </div>
              ) : null}

              <dl className={styles.metadata}>
                <div><dt>Source</dt><dd>{file?.name ?? "No file selected"}</dd></div>
                <div><dt>Target</dt><dd>{targetFormat.toUpperCase()}</dd></div>
                <div><dt>IFC schema</dt><dd>{result?.ifcSchema ?? "—"}</dd></div>
                <div><dt>Output size</dt><dd>{result?.outputBytes !== null && result?.outputBytes !== undefined ? formatBytes(result.outputBytes) : "—"}</dd></div>
                <div><dt>Output SHA-256</dt><dd className={styles.hash}>{result?.outputSha256 ?? "—"}</dd></div>
              </dl>

              {result ? (
                <button
                  type="button"
                  className={styles.download}
                  onClick={() => triggerDownload(result)}
                >
                  Download {result.filename}
                  <span aria-hidden="true">↓</span>
                </button>
              ) : null}
            </aside>
          </div>
        </div>
      </section>

      <section className={styles.section} id="formats">
        <div className="content-width">
          <div className={styles.explainerGrid}>
            <article>
              <span>IFC</span>
              <h3>Authoritative source</h3>
              <p>The conversion service accepts open IFC source models and preserves the source as the conversion input rather than pretending proprietary RVT/RFA parsing exists.</p>
            </article>
            <article>
              <span>GLB</span>
              <h3>Compact 3D delivery</h3>
              <p>GLB packages geometry and material output in a single binary glTF artifact suitable for real-time and web-oriented 3D workflows.</p>
            </article>
            <article>
              <span>OBJ + MTL</span>
              <h3>Interchange package</h3>
              <p>OBJ output is packaged with its generated MTL material file in one ZIP so the conversion result remains complete.</p>
            </article>
          </div>
        </div>
      </section>

      <section className={styles.section} id="integrity">
        <div className="content-width">
          <div className={styles.integrity}>
            <p className="eyebrow"><span aria-hidden="true">●</span>Integrity</p>
            <h2>No simulated progress. No fabricated format support.</h2>
            <p>
              BIMAP reports an indeterminate working state while upload and conversion execute,
              validates the source before consuming the existing conversion entitlement, and returns
              source/output SHA-256 metadata with the completed artifact.
            </p>
          </div>
        </div>
      </section>
    </>
  );
}
