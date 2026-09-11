"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import { BimapApiError } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/account";
import {
  beginOrderUploads,
  createOrder,
  getOrder,
  stageOrderModelUpload,
  type BimapProductCode,
  type BimapStagedUploadDto,
} from "@/lib/bimap-api";
import {
  allElementTargets,
  findingsFromWorkspace,
  getAuditStatus,
  getAuditWorkspace,
  issueAuditReportDownload,
  listAuditReports,
  normalizeElementAlias,
  ruleResultsFromWorkspace,
  startAudit,
  targetsForFinding,
  type AuditElementTarget,
  type AuditFindingDto,
  type AuditSeverity,
  type AuditStatusDto,
  type AuditWorkspaceDto,
  type ReportListDto,
} from "@/lib/audit-api";

import { BIMModelViewer } from "./BIMModelViewer";
import styles from "./AuditWorkspace.module.css";


type Props = {
  productCode: BimapProductCode;
};

type SeverityFilter = "all" | AuditSeverity;

type AuditPhase =
  | "idle"
  | "submitting"
  | "queued"
  | "processing"
  | "complete"
  | "error";

type AuditDropzoneProps = {
  label: string;
  accept: string;
  supported: string;
  multiple?: boolean;
  files: readonly File[];
  onFiles: (files: readonly File[]) => void;
  disabled?: boolean;
};


function AuditDropzone({
  label,
  accept,
  supported,
  multiple = false,
  files,
  onFiles,
  disabled = false,
}: AuditDropzoneProps) {
  const inputRef =
    useRef<HTMLInputElement | null>(null);

  const commit = (
    candidates: FileList | readonly File[],
  ) => {
    if (disabled) {
      return;
    }

    const selected =
      Array.from(candidates);

    onFiles(
      multiple
        ? selected
        : selected.slice(0, 1),
    );
  };

  return (
    <div
      className={styles.auditDropzone}
      data-disabled={disabled}
      onDragOver={(event) => {
        event.preventDefault();
      }}
      onDrop={(event) => {
        event.preventDefault();

        commit(
          event.dataTransfer.files,
        );
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        onChange={(event) => {
          commit(
            event.target.files ?? [],
          );

          event.target.value = "";
        }}
      />

      <span className={styles.auditDropzoneLabel}>
        {label}
      </span>

      <strong>
        Drop {multiple ? "models" : "model"} here
      </strong>

      <span>or</span>

      <button
        type="button"
        disabled={disabled}
        onClick={() =>
          inputRef.current?.click()
        }
      >
        Choose {multiple ? "models" : "model"}
      </button>

      <small>
        Supported: {supported}
      </small>

      {files.length > 0 ? (
        <div className={styles.auditSelectedFiles}>
          {files.map((file) => (
            <div
              key={`${file.name}:${file.size}:${file.lastModified}`}
            >
              <strong>{file.name}</strong>

              <span>
                {formatFileSize(file.size)}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const SEVERITY_ORDER: readonly AuditSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "informational",
];


function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function phaseForStatus(status: AuditStatusDto | null): AuditPhase {
  if (!status) {
    return "idle";
  }
  if (status.is_exception) {
    return "error";
  }
  if (status.is_delivered || status.state === "governance_review" || status.state === "packaging") {
    return "complete";
  }
  if (status.state === "queued") {
    return "queued";
  }
  if (status.is_processing) {
    return "processing";
  }
  return "idle";
}

function exportJson(filename: string, value: unknown): void {
  const blob = new Blob([JSON.stringify(value, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

function extensionOf(
  file: File,
): string {
  const index =
    file.name.lastIndexOf(".");

  return index < 0
    ? ""
    : file.name.slice(index).toLowerCase();
}


function formatFileSize(
  bytes: number,
): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${
    (bytes / (1024 * 1024)).toFixed(1)
  } MB`;
}


function validateAuditSources(
  productCode: BimapProductCode,
  familySources: readonly File[],
  projectSource: File | null,
): string | null {
  if (productCode === "family_audit") {
    if (
      familySources.length !== 1 ||
      extensionOf(familySources[0]) !== ".rfa"
    ) {
      return "Family Audit requires exactly one RFA model.";
    }

    return null;
  }

  if (productCode === "bim_qa") {
    if (!projectSource) {
      return "BIM QA requires one RVT or IFC model.";
    }

    if (
      ![".rvt", ".ifc"].includes(
        extensionOf(projectSource),
      )
    ) {
      return "BIM QA accepts RVT or IFC models.";
    }

    return null;
  }

  if (familySources.length === 0) {
    return "Combined Audit requires at least one RFA family model.";
  }

  if (
    familySources.some(
      (file) =>
        extensionOf(file) !== ".rfa",
    )
  ) {
    return "Combined Audit family sources must be RFA models.";
  }

  if (!projectSource) {
    return "Combined Audit requires one RVT or IFC project model.";
  }

  if (
    ![".rvt", ".ifc"].includes(
      extensionOf(projectSource),
    )
  ) {
    return "Combined Audit project source must be RVT or IFC.";
  }

  return null;
}

function findingMatches(
  finding: AuditFindingDto,
  search: string,
  severity: SeverityFilter,
): boolean {
  if (severity !== "all" && finding.severity !== severity) {
    return false;
  }
  const query = search.trim().toLowerCase();
  if (!query) {
    return true;
  }
  return [
    finding.finding_id,
    finding.rule_id,
    finding.title,
    finding.category,
    finding.severity,
    finding.status,
    finding.explanation,
    finding.remediation,
    ...finding.evidence_refs,
  ].some((value) => value.toLowerCase().includes(query));
}


export function AuditWorkspace({ productCode }: Props) {
  const [orderId, setOrderId] = useState("");
  const [familySourceFiles, setFamilySourceFiles] = useState<readonly File[]>([]);
  const [projectSourceFile, setProjectSourceFile] = useState<File | null>(null);
  const [uploadingAuditSource, setUploadingAuditSource] = useState(false);
  const [auditUploadMessage, setAuditUploadMessage] = useState<string | null>(null);

  const [status, setStatus] = useState<AuditStatusDto | null>(null);
  const [workspace, setWorkspace] = useState<AuditWorkspaceDto | null>(null);
  const [reports, setReports] = useState<ReportListDto | null>(null);
  const [phase, setPhase] = useState<AuditPhase>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const [selectedTargetKey, setSelectedTargetKey] = useState<string | null>(null);
  const [modelUrl, setModelUrl] = useState<string | null>(null);
  const [modelName, setModelName] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const modelUrlRef = useRef<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialOrder = params.get("order")?.trim();
    if (initialOrder) {
      setOrderId(initialOrder);
    }
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
      }
      if (modelUrlRef.current) {
        URL.revokeObjectURL(modelUrlRef.current);
      }
    };
  }, []);

  const findings = useMemo(
    () => findingsFromWorkspace(workspace),
    [workspace],
  );
  const evidence = workspace?.payload.deterministic.context.evidence ?? [];
  const ruleResults = useMemo(
    () => ruleResultsFromWorkspace(workspace),
    [workspace],
  );

  const targetsByFinding = useMemo(() => {
    const index = new Map<string, readonly AuditElementTarget[]>();
    for (const finding of findings) {
      index.set(finding.finding_id, targetsForFinding(finding, evidence));
    }
    return index;
  }, [evidence, findings]);

  const viewerTargets = useMemo(
    () => allElementTargets(findings, evidence),
    [evidence, findings],
  );

  const filteredFindings = useMemo(
    () => findings.filter((finding) => findingMatches(finding, search, severity)),
    [findings, search, severity],
  );

  const selectedFinding = useMemo(
    () => findings.find((finding) => finding.finding_id === selectedFindingId) ?? null,
    [findings, selectedFindingId],
  );

  const selectedTargets = selectedFinding
    ? targetsByFinding.get(selectedFinding.finding_id) ?? []
    : [];

  useEffect(() => {
    if (findings.length === 0) {
      setSelectedFindingId(null);
      setSelectedTargetKey(null);
      return;
    }
    if (!selectedFindingId || !findings.some((item) => item.finding_id === selectedFindingId)) {
      const first = findings[0];
      const firstTarget = targetsByFinding.get(first.finding_id)?.[0] ?? null;
      setSelectedFindingId(first.finding_id);
      setSelectedTargetKey(firstTarget?.key ?? null);
    }
  }, [findings, selectedFindingId, targetsByFinding]);

  const severityCounts = useMemo(() => {
    const counts: Record<AuditSeverity, number> = {
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
      informational: 0,
    };
    for (const finding of findings) {
      counts[finding.severity] += 1;
    }
    return counts;
  }, [findings]);

  const loadReports = useCallback(async (targetOrderId: string) => {
    try {
      setReports(await listAuditReports(targetOrderId));
    } catch (error) {
      if (!(error instanceof BimapApiError && error.status === 404)) {
        setMessage(getApiErrorMessage(error));
      }
    }
  }, []);

  const loadWorkspace = useCallback(async (targetOrderId: string, silent = false) => {
    try {
      const result = await getAuditWorkspace(targetOrderId);
      if (result.product_code !== productCode) {
        setPhase("error");
        setMessage(
          `Order product ${result.product_code} does not match this ${productCode} workspace.`,
        );
        return null;
      }
      setWorkspace(result);
      setPhase("complete");
      if (!silent) {
        setMessage(`Audit result ${result.job_id} loaded.`);
      }
      void loadReports(targetOrderId);
      return result;
    } catch (error) {
      if (error instanceof BimapApiError && error.status === 404) {
        if (!silent) {
          setMessage("The audit has not produced a completed workspace result yet.");
        }
        return null;
      }
      setPhase("error");
      setMessage(getApiErrorMessage(error));
      return null;
    }
  }, [loadReports, productCode]);

  const refreshStatus = useCallback(async (targetOrderId: string, silent = false) => {
    try {
      const next = await getAuditStatus(targetOrderId);
      setStatus(next);
      setPhase(phaseForStatus(next));
      if (!silent) {
        setMessage(`Audit state: ${next.state.replaceAll("_", " ")}.`);
      }
      if (
        next.state === "governance_review" ||
        next.state === "packaging" ||
        next.state === "delivered" ||
        next.state === "review_required"
      ) {
        await loadWorkspace(targetOrderId, true);
      }
      return next;
    } catch (error) {
      setPhase("error");
      setMessage(getApiErrorMessage(error));
      return null;
    }
  }, [loadWorkspace]);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback((targetOrderId: string) => {
    stopPolling();
    pollRef.current = window.setInterval(() => {
      void refreshStatus(targetOrderId, true).then((next) => {
        if (
          next &&
          (next.is_exception ||
            next.is_delivered ||
            next.state === "governance_review" ||
            next.state === "review_required")
        ) {
          stopPolling();
        }
      });
    }, 2500);
  }, [refreshStatus, stopPolling]);


const onStart = useCallback(
  async () => {
    const validationError =
      validateAuditSources(
        productCode,
        familySourceFiles,
        projectSourceFile,
      );

    if (validationError) {
      setPhase("error");
      setMessage(validationError);
      return;
    }

    const files =
      productCode === "family_audit"
        ? [...familySourceFiles]
        : productCode === "bim_qa"
          ? [projectSourceFile!]
          : [
              ...familySourceFiles,
              projectSourceFile!,
            ];

    stopPolling();

    setPhase("submitting");
    setUploadingAuditSource(true);
    setMessage("Preparing audit…");
    setAuditUploadMessage(
      "Uploading and validating model sources…",
    );

    setWorkspace(null);
    setReports(null);
    setSelectedFindingId(null);
    setSelectedTargetKey(null);

    try {
      /*
       * A source selection represents a new audit unless this
       * workspace was explicitly opened for an existing draft/uploading order.
       */
      let order;

      if (orderId.trim()) {
        order =
          await getOrder(
            orderId.trim(),
          );

        if (
          order.product_code !==
          productCode
        ) {
          throw new Error(
            `Audit product ${order.product_code} does not match ${productCode}.`,
          );
        }
      } else {
        order =
          await createOrder({
            productCode,
          });

        setOrderId(
          order.order_id,
        );
      }

      if (order.state === "draft") {
        order =
          await beginOrderUploads(
            order.order_id,
            crypto.randomUUID(),
          );
      }

      if (order.state !== "uploading") {
        throw new Error(
          `Audit cannot accept new models while in ` +
          `"${order.state.replaceAll("_", " ")}" state.`,
        );
      }

      const uploaded:
        BimapStagedUploadDto[] = [];

      for (
        const [index, file]
        of files.entries()
      ) {
        setAuditUploadMessage(
          `Uploading ${index + 1} of ${files.length}: ${file.name}`,
        );

        uploaded.push(
          await stageOrderModelUpload(
            order.order_id,
            file,
          ),
        );
      }

      setAuditUploadMessage(
        "Models validated. Preparing audit evidence…",
      );

      const response =
        await startAudit(
          order.order_id,
          {
            jobId:
              crypto.randomUUID(),

            idempotencyKey:
              crypto.randomUUID(),

            sources:
              uploaded.map(
                (source) => ({
                  source_ref:
                    source.source_ref,

                  filename:
                    source.filename,
                }),
              ),

            metadata: {
              interface:
                "audit_workspace",

              requested_product:
                productCode,
            },
          },
        );

      setStatus(
        response.status,
      );

      setPhase(
        phaseForStatus(
          response.status,
        ),
      );

      setAuditUploadMessage(null);

      setMessage(
        "Audit submitted successfully.",
      );

      startPolling(
        order.order_id,
      );
    } catch (error) {
      setPhase("error");

      setMessage(
        getApiErrorMessage(error),
      );
    } finally {
      setUploadingAuditSource(
        false,
      );
    }
  },
  [
    familySourceFiles,
    orderId,
    productCode,
    projectSourceFile,
    startPolling,
    stopPolling,
  ],
);

  const onLoadExisting = useCallback(async () => {
    const targetOrderId = orderId.trim();
    if (!targetOrderId) {
      setPhase("error");
      setMessage("Order ID is required.");
      return;
    }
    await refreshStatus(targetOrderId);
    await loadWorkspace(targetOrderId, true);
  }, [loadWorkspace, orderId, refreshStatus]);

  const selectFinding = useCallback((finding: AuditFindingDto) => {
    setSelectedFindingId(finding.finding_id);
    const firstTarget = targetsByFinding.get(finding.finding_id)?.[0] ?? null;
    setSelectedTargetKey(firstTarget?.key ?? null);
  }, [targetsByFinding]);

  const onElementSelected = useCallback((alias: string) => {
    const normalized = normalizeElementAlias(alias);
    for (const finding of findings) {
      const targets = targetsByFinding.get(finding.finding_id) ?? [];
      const match = targets.find((target) =>
        target.aliases.some((candidate) => normalizeElementAlias(candidate) === normalized),
      );
      if (match) {
        setSelectedFindingId(finding.finding_id);
        setSelectedTargetKey(match.key);
        return;
      }
    }
  }, [findings, targetsByFinding]);

  const onModelFile = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    if (modelUrlRef.current) {
      URL.revokeObjectURL(modelUrlRef.current);
      modelUrlRef.current = null;
    }
    if (!file) {
      setModelUrl(null);
      setModelName(null);
      return;
    }

    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (extension !== ".glb" && extension !== ".gltf") {
      event.target.value = "";
      setModelUrl(null);
      setModelName(null);
      setMessage("The embedded navigator accepts GLB or self-contained GLTF viewer geometry.");
      return;
    }

    const url = URL.createObjectURL(file);
    modelUrlRef.current = url;
    setModelUrl(url);
    setModelName(file.name);
  }, []);

  const clearModel = useCallback(() => {
    if (modelUrlRef.current) {
      URL.revokeObjectURL(modelUrlRef.current);
      modelUrlRef.current = null;
    }
    setModelUrl(null);
    setModelName(null);
  }, []);

  const downloadReport = useCallback(async (artifactId: string) => {
    const targetOrderId = orderId.trim();
    if (!targetOrderId) {
      return;
    }
    try {
      const grant = await issueAuditReportDownload(targetOrderId, artifactId);
      window.open(grant.download_url, "_blank", "noopener,noreferrer");
    } catch (error) {
      setMessage(getApiErrorMessage(error));
    }
  }, [orderId]);

  const activeTarget = selectedTargetKey
    ? viewerTargets.find((target) => target.key === selectedTargetKey) ?? null
    : null;

  return (
    <section className={styles.workspace} id="audit-workspace">
      <div className={styles.workspaceHeader}>
        <div>
          <p className="eyebrow"><span aria-hidden="true">●</span>Audit workspace</p>
          <h2>Run, inspect and trace the audit in one interface.</h2>
          <p>
            Findings remain linked to their evidence and BIM object identifiers, so a selected issue can drive the model navigator directly to the affected object.
          </p>
        </div>
        <div className={styles.phase} data-phase={phase}>
          <span>{phase.replaceAll("_", " ")}</span>
          <strong>{status?.state.replaceAll("_", " ") ?? "No audit loaded"}</strong>
        </div>
      </div>

      <div className={styles.auditSourcePanel}>
        {productCode === "family_audit" ? (
          <AuditDropzone
            label="Audit model"
            accept=".rfa"
            supported="RFA"
            files={familySourceFiles}
            disabled={uploadingAuditSource}
            onFiles={setFamilySourceFiles}
          />
        ) : null}

        {productCode === "bim_qa" ? (
          <AuditDropzone
            label="Project model"
            accept=".rvt,.ifc"
            supported="RVT, IFC"
            files={
              projectSourceFile
                ? [projectSourceFile]
                : []
            }
            disabled={uploadingAuditSource}
            onFiles={(files) =>
              setProjectSourceFile(
                files[0] ?? null,
              )
            }
          />
        ) : null}

        {productCode === "combined_audit" ? (
          <div className={styles.combinedAuditSources}>
            <AuditDropzone
              label="Family models"
              accept=".rfa"
              supported="RFA"
              multiple
              files={familySourceFiles}
              disabled={uploadingAuditSource}
              onFiles={setFamilySourceFiles}
            />

            <AuditDropzone
              label="Project model"
              accept=".rvt,.ifc"
              supported="RVT, IFC"
              files={
                projectSourceFile
                  ? [projectSourceFile]
                  : []
              }
              disabled={uploadingAuditSource}
              onFiles={(files) =>
                setProjectSourceFile(
                  files[0] ?? null,
                )
              }
            />
          </div>
        ) : null}

        {auditUploadMessage ? (
          <div className={styles.auditUploadMessage}>
            {auditUploadMessage}
          </div>
        ) : null}

        <div className={styles.auditActions}>
          <button
            type="button"
            className={styles.runAuditButton}
            disabled={
              uploadingAuditSource ||
              phase === "submitting" ||
              phase === "processing"
            }
            onClick={() => void onStart()}
          >
            {uploadingAuditSource
              ? "Preparing audit…"
              : productCode === "combined_audit"
                ? "Run combined audit"
                : "Run audit"}
          </button>

          {orderId && workspace ? (
            <button
              type="button"
              className={styles.secondaryButton}
              onClick={() =>
                exportJson(
                  `bimap-audit-${workspace.order_id}.json`,
                  workspace.payload,
                )
              }
            >
              Export JSON
            </button>
          ) : null}
        </div>
      </div>

      <form className={styles.auditControls} onSubmit={onStart}>
        <label>
          <span>AUDIT ID</span>
          <input
            value={orderId}
            onChange={(event) => setOrderId(event.target.value)}
            placeholder="Order identifier"
            autoComplete="off"
          />
        </label>
        <label>
          <span>Evidence manifest</span>
          <input
            value={manifestRef}
            onChange={(event) => setManifestRef(event.target.value)}
            placeholder="manifest://…"
            autoComplete="off"
          />
        </label>
        <label className={styles.wideControl}>
          <span>Evidence IDs</span>
          <input
            value={evidenceRefText}
            onChange={(event) => setEvidenceRefText(event.target.value)}
            placeholder="EV-0001, EV-0002 …"
            autoComplete="off"
          />
        </label>
        <div className={styles.controlButtons}>
          <button type="submit" disabled={phase === "submitting" || phase === "processing"}>
            {phase === "submitting" ? "Submitting…" : "Run audit"}
          </button>
          <button type="button" className={styles.secondaryButton} onClick={onLoadExisting}>
            Load result
          </button>
          <button
            type="button"
            className={styles.secondaryButton}
            onClick={() => workspace && exportJson(`bimap-audit-${workspace.order_id}.json`, workspace.payload)}
            disabled={!workspace}
          >
            Export JSON
          </button>
        </div>
      </form>

      {message ? <div className={styles.message} data-phase={phase}>{message}</div> : null}

      <div className={styles.statistics} aria-label="Audit statistics">
        <article><span>Findings</span><strong>{findings.length}</strong></article>
        <article data-severity="critical"><span>Critical</span><strong>{severityCounts.critical}</strong></article>
        <article data-severity="high"><span>High</span><strong>{severityCounts.high}</strong></article>
        <article><span>Rules executed</span><strong>{ruleResults.length}</strong></article>
        <article><span>Evidence items</span><strong>{evidence.length}</strong></article>
        <article><span>Mapped objects</span><strong>{viewerTargets.length}</strong></article>
      </div>

      <div className={styles.mainGrid}>
        <div className={styles.viewerColumn}>
          <div className={styles.modelControls}>
            <label>
              <input type="file" accept=".glb,.gltf,model/gltf-binary,model/gltf+json" onChange={onModelFile} />
              <span>{modelName ?? "Load viewer geometry (.glb/.gltf)"}</span>
            </label>
            {modelName ? <button type="button" onClick={clearModel}>Clear</button> : null}
          </div>
          <BIMModelViewer
            modelUrl={modelUrl}
            targets={viewerTargets}
            selectedTargetKey={selectedTargetKey}
            selectedSeverity={selectedFinding?.severity ?? null}
            onElementSelected={onElementSelected}
          />
        </div>

        <aside className={styles.findingsPanel}>
          <div className={styles.panelHeading}>
            <div>
              <span>Findings</span>
              <strong>{filteredFindings.length} / {findings.length}</strong>
            </div>
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search finding, rule, element…"
            />
          </div>

          <div className={styles.severityFilters}>
            <button type="button" data-active={severity === "all"} onClick={() => setSeverity("all")}>All</button>
            {SEVERITY_ORDER.map((item) => (
              <button
                type="button"
                key={item}
                data-active={severity === item}
                data-severity={item}
                onClick={() => setSeverity(item)}
              >
                {item} <span>{severityCounts[item]}</span>
              </button>
            ))}
          </div>

          <div className={styles.findingList}>
            {filteredFindings.map((finding) => {
              const targets = targetsByFinding.get(finding.finding_id) ?? [];
              return (
                <button
                  type="button"
                  key={finding.finding_id}
                  className={styles.findingCard}
                  data-selected={finding.finding_id === selectedFindingId}
                  data-severity={finding.severity}
                  onClick={() => selectFinding(finding)}
                >
                  <div>
                    <span>{finding.severity}</span>
                    <small>{finding.status}</small>
                  </div>
                  <strong>{finding.title}</strong>
                  <p>{finding.explanation}</p>
                  <footer>
                    <span>{finding.rule_id}</span>
                    <span>{targets.length > 0 ? `${targets.length} object${targets.length === 1 ? "" : "s"}` : "Evidence only"}</span>
                  </footer>
                </button>
              );
            })}
            {filteredFindings.length === 0 ? (
              <div className={styles.emptyState}>
                <strong>No matching findings</strong>
                <p>{workspace ? "Adjust the filters or search query." : "Load a completed audit result."}</p>
              </div>
            ) : null}
          </div>
        </aside>
      </div>

      <div className={styles.lowerGrid}>
        <section className={styles.detailPanel}>
          <div className={styles.panelHeadingRow}>
            <div>
              <span>Selected finding</span>
              <h3>{selectedFinding?.title ?? "No finding selected"}</h3>
            </div>
            {selectedFinding ? (
              <span className={styles.severityBadge} data-severity={selectedFinding.severity}>
                {selectedFinding.severity}
              </span>
            ) : null}
          </div>

          {selectedFinding ? (
            <>
              <dl className={styles.findingMeta}>
                <div><dt>Finding ID</dt><dd>{selectedFinding.finding_id}</dd></div>
                <div><dt>Rule</dt><dd>{selectedFinding.rule_id}</dd></div>
                <div><dt>Category</dt><dd>{selectedFinding.category}</dd></div>
                <div><dt>Confidence</dt><dd>{Math.round(selectedFinding.confidence * 100)}%</dd></div>
              </dl>

              <div className={styles.targetSection}>
                <span>Affected BIM objects</span>
                {selectedTargets.length > 0 ? (
                  <div className={styles.targetList}>
                    {selectedTargets.map((target) => (
                      <button
                        type="button"
                        key={target.key}
                        data-active={selectedTargetKey === target.key}
                        onClick={() => setSelectedTargetKey(target.key)}
                      >
                        <strong>{target.label}</strong>
                        <span>{target.kind ?? target.aliases[0] ?? "Clash location"}</span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <p>No spatial object identifier is present in this finding&apos;s evidence.</p>
                )}
              </div>

              <div className={styles.comparisonGrid}>
                <div>
                  <span>Observed</span>
                  <pre>{formatValue(selectedFinding.observed_value)}</pre>
                </div>
                <div>
                  <span>Expected</span>
                  <pre>{formatValue(selectedFinding.expected_value)}</pre>
                </div>
              </div>

              <div className={styles.guidanceGrid}>
                <article><span>Remediation</span><p>{selectedFinding.remediation}</p></article>
                <article><span>Verification</span><p>{selectedFinding.verification_method}</p></article>
              </div>
            </>
          ) : (
            <div className={styles.emptyState}>
              <p>Select a finding to inspect its evidence, remediation and BIM object mapping.</p>
            </div>
          )}
        </section>

        <aside className={styles.reportPanel}>
          <div className={styles.panelHeadingRow}>
            <div>
              <span>Report</span>
              <h3>Audit artifacts</h3>
            </div>
            <button
              type="button"
              className={styles.textButton}
              disabled={!orderId.trim()}
              onClick={() => orderId.trim() && void loadReports(orderId.trim())}
            >
              Refresh
            </button>
          </div>

          <dl className={styles.reportSummary}>
            <div><dt>Order</dt><dd>{workspace?.order_id ?? status?.order_id ?? "—"}</dd></div>
            <div><dt>Job</dt><dd>{workspace?.job_id ?? status?.job_id ?? "—"}</dd></div>
            <div><dt>Completed</dt><dd>{formatDate(workspace?.completed_at)}</dd></div>
            <div><dt>Reports</dt><dd>{reports?.found_count ?? 0}</dd></div>
          </dl>

          <div className={styles.artifactList}>
            {reports?.items.flatMap((report) =>
              report.artifacts.map((artifact) => (
                <button
                  type="button"
                  key={`${report.report_id}:${artifact.artifact_id}`}
                  onClick={() => void downloadReport(artifact.artifact_id)}
                >
                  <span>
                    <strong>{artifact.filename}</strong>
                    <small>Report {report.report_version}</small>
                  </span>
                  <span aria-hidden="true">↓</span>
                </button>
              )),
            )}
            {(!reports || reports.found_count === 0) ? (
              <div className={styles.emptyState}>
                <strong>No released report artifacts</strong>
                <p>Artifacts appear here after BIMAP reporting and release complete.</p>
              </div>
            ) : null}
          </div>
        </aside>
      </div>

      {activeTarget?.path ? (
        <div className={styles.traceBar}>
          <span>Evidence path</span>
          <code>{activeTarget.path}</code>
        </div>
      ) : null}
    </section>
  );
}
