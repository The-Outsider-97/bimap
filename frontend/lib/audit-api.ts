import {
  apiJsonRequest,
  apiRequest,
} from "@/lib/api";
import type {
  BimapProductCode,
} from "@/lib/bimap-api";


export type AuditSeverity =
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "informational";

export type SLAIGateDisposition =
  | "allow"
  | "modify"
  | "review"
  | "block"
  | "unknown";


export type SLAIGovernanceGateDto = {
  readonly gate: string;
  readonly disposition: SLAIGateDisposition;
  readonly source_token: string | null;
  readonly reason_codes: readonly string[];
  readonly details: Readonly<
    Record<string, unknown>
  >;
};


export type SLAIAgentOutputDto = {
  readonly agent: string;
  readonly phase: string;
  readonly succeeded: boolean;
  readonly output_type: string | null;
  readonly serializable: boolean;
  readonly payload: unknown;
  readonly error:
    | Readonly<Record<string, unknown>>
    | null;
  readonly note: string | null;
};


export type SLAIMappedResultDto = {
  readonly job_id: string;
  readonly order_id: string;
  readonly correlation_id: string;

  /**
   * Exact deterministic FindingContracts passed
   * through the SLAI boundary unchanged.
   *
   * This collection is NOT a second SLAI-generated
   * finding set.
   */
  readonly authoritative_findings:
    readonly AuditFindingDto[];

  readonly governance_gates:
    readonly SLAIGovernanceGateDto[];

  readonly agent_outputs:
    readonly SLAIAgentOutputDto[];

  readonly started_at: string;
  readonly completed_at: string;

  readonly terminated_early: boolean;
  readonly termination_reason:
    | string
    | null;

  readonly privacy_sanitized_payload:
    unknown;

  readonly mapping_warnings:
    readonly string[];

  readonly gate_blocked: boolean;

  readonly gate_review_required:
    boolean;

  readonly requires_modified_payload:
    boolean;

  readonly automatic_release_allowed:
    boolean;
};

export type AuditFindingDto = {
  readonly schema_version: string;
  readonly finding_id: string;
  readonly scope: string;
  readonly rule_id: string;
  readonly title: string;
  readonly category: string;
  readonly automation_type: string;
  readonly severity: AuditSeverity;
  readonly confidence: number;
  readonly status: string;
  readonly observed_value: unknown;
  readonly expected_value: unknown;
  readonly evidence_refs: readonly string[];
  readonly explanation: string;
  readonly remediation: string;
  readonly verification_method: string;
};

export type AuditEvidenceDto = {
  readonly evidence_id: string;
  readonly provenance?: Readonly<Record<string, unknown>>;
  readonly logical_location?: {
    readonly page?: number;
    readonly row?: number;
    readonly element?: string;
    readonly path?: string;
  } | null;
  readonly extracted_value?: unknown;
  readonly confidence?: number | null;
};

export type AuditRuleResultDto = {
  readonly rule_id: string;
  readonly rule_version: string;
  readonly status: string;
  readonly observed_value: unknown;
  readonly expected_value: unknown;
  readonly evidence_refs: readonly string[];
  readonly metrics: Readonly<Record<string, unknown>>;
};

export type AuditContextDto = {
  readonly product_code: BimapProductCode;
  readonly project_id: string | null;
  readonly family_evidence_refs: readonly string[];
  readonly evidence: readonly AuditEvidenceDto[];
  readonly evidence_groups: Readonly<Record<string, readonly string[]>>;
  readonly source_manifest: Readonly<Record<string, unknown>>;
  readonly metadata: Readonly<Record<string, unknown>>;
};

export type AuditStageDto = {
  readonly findings?: readonly AuditFindingDto[];
  readonly rule_results?: readonly AuditRuleResultDto[];
  readonly [key: string]: unknown;
};

export type CombinedAuditStageDto = AuditStageDto & {
  readonly family_findings?: readonly AuditFindingDto[];
  readonly project_findings?: readonly AuditFindingDto[];
  readonly cross_scope_findings?: readonly AuditFindingDto[];
};

export type AuditDeterministicDto = {
  readonly product_code: BimapProductCode;
  readonly context: AuditContextDto;
  readonly ingestion_manifests: readonly Readonly<Record<string, unknown>>[];
  readonly coverage: Readonly<Record<string, unknown>>;
  readonly family_audit: AuditStageDto | null;
  readonly bim_qa: AuditStageDto | null;
  readonly combined_audit: CombinedAuditStageDto | null;
};

export type AuditExecutionPayloadDto = {
  readonly job: Readonly<Record<string, unknown>>;
  readonly deterministic: AuditDeterministicDto;
  readonly slai: SLAIMappedResultDto;
};

export type AuditWorkspaceDto = {
  readonly order_id: string;
  readonly job_id: string;
  readonly product_code: BimapProductCode;
  readonly completed_at: string;
  readonly payload: AuditExecutionPayloadDto;
};

export type AuditStatusDto = {
  readonly order_id: string;
  readonly product_code: BimapProductCode;
  readonly state: string;
  readonly updated_at: string;
  readonly order_version: number;
  readonly is_processing: boolean;
  readonly is_exception: boolean;
  readonly is_delivered: boolean;
  readonly is_terminal: boolean;
  readonly job_id: string | null;
  readonly job_order_version: number | null;
  readonly job_submitted_at: string | null;
};

export type StartAuditResponseDto = {
  readonly job: Readonly<Record<string, unknown>>;
  readonly queue: {
    readonly job_id: string;
    readonly queue_reference: string;
    readonly idempotency_key: string;
  };
  readonly status: AuditStatusDto;
};

export type ReportArtifactDto = {
  readonly artifact_id: string;
  readonly filename: string;
  readonly sha256: string;
  readonly size_bytes: number;
};

export type ReportManifestDto = {
  readonly report_id: string;
  readonly order_id: string;
  readonly report_version: string;
  readonly generated_at: string;
  readonly artifacts: readonly ReportArtifactDto[];
  readonly finding_refs: readonly string[];
  readonly requirement_refs: readonly string[];
  readonly evidence_refs: readonly string[];
  readonly [key: string]: unknown;
};

export type ReportListDto = {
  readonly items: readonly ReportManifestDto[];
  readonly missing_report_ids: readonly string[];
  readonly found_count: number;
  readonly missing_count: number;
  readonly requested_count: number;
};

export type ReportDownloadGrantDto = {
  readonly report_id: string;
  readonly artifact: ReportArtifactDto;
  readonly download_url: string;
  readonly expires_at: string;
};

export type AuditPoint3 = {
  readonly x: number;
  readonly y: number;
  readonly z: number;
};

export type AuditElementTarget = {
  readonly key: string;
  readonly evidenceId: string;
  readonly aliases: readonly string[];
  readonly label: string;
  readonly kind: string | null;
  readonly path: string | null;
  readonly point: AuditPoint3 | null;
};


const IDENTITY_KEYS = new Set([
  "element",
  "element_id",
  "elementid",
  "element_global_id",
  "elementglobalid",
  "global_id",
  "globalid",
  "guid",
  "ifc_guid",
  "ifcguid",
  "ifc_global_id",
  "ifcglobalid",
  "express_id",
  "expressid",
  "source_id",
  "sourceid",
  "tag",
  "revit_id",
  "revitid",
  "unique_id",
  "uniqueid",
]);

const LABEL_KEYS = [
  "element_name",
  "elementname",
  "name",
  "label",
  "title",
] as const;

const KIND_KEYS = [
  "ifc_class",
  "ifcclass",
  "source_class",
  "sourceclass",
  "category",
  "object_type",
  "objecttype",
] as const;

const POINT_KEYS = new Set([
  "point",
  "position",
  "centroid",
  "clash_point",
  "clashpoint",
  "location",
]);


function objectRecord(value: unknown): Readonly<Record<string, unknown>> | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Readonly<Record<string, unknown>>;
}

function scalarText(value: unknown): string | null {
  if (typeof value === "string") {
    const normalized = value.trim();
    return normalized || null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return null;
}

function canonicalKey(value: string): string {
  return value.replace(/[^A-Za-z0-9]/g, "").toLowerCase();
}

export function slaiOutputsForAgent(
  workspace: AuditWorkspaceDto | null,
  agent: string,
): readonly SLAIAgentOutputDto[] {
  if (!workspace) {
    return [];
  }

  const normalized =
    agent.trim().toLowerCase();

  if (!normalized) {
    return [];
  }

  return workspace.payload.slai.agent_outputs.filter(
    (output) =>
      output.agent.toLowerCase() === normalized,
  );
}


export function latestSlaiOutputForAgent(
  workspace: AuditWorkspaceDto | null,
  agent: string,
): SLAIAgentOutputDto | null {
  const outputs =
    slaiOutputsForAgent(
      workspace,
      agent,
    );

  return outputs.length > 0
    ? outputs[outputs.length - 1]
    : null;
}


export function slaiGovernanceGate(
  workspace: AuditWorkspaceDto | null,
  gate: string,
): SLAIGovernanceGateDto | null {
  if (!workspace) {
    return null;
  }

  const normalized =
    gate.trim().toLowerCase();

  return (
    workspace.payload.slai.governance_gates.find(
      (item) =>
        item.gate.toLowerCase()
        === normalized,
    )
    ?? null
  );
}

export function normalizeElementAlias(value: string): string {
  return value
    .trim()
    .replace(/^#/, "")
    .replace(/^ifc[:#]/i, "")
    .toLowerCase();
}

function uniqueAliases(values: Iterable<string>): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const raw of values) {
    const value = raw.trim();
    if (!value) {
      continue;
    }
    const normalized = normalizeElementAlias(value);
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    result.push(value);
  }
  return result;
}

function parsePoint(value: unknown): AuditPoint3 | null {
  if (Array.isArray(value) && value.length >= 3) {
    const [x, y, z] = value;
    if (
      typeof x === "number" && Number.isFinite(x) &&
      typeof y === "number" && Number.isFinite(y) &&
      typeof z === "number" && Number.isFinite(z)
    ) {
      return { x, y, z };
    }
  }

  const record = objectRecord(value);
  if (!record) {
    return null;
  }
  const x = record.x ?? record.X;
  const y = record.y ?? record.Y;
  const z = record.z ?? record.Z;
  if (
    typeof x === "number" && Number.isFinite(x) &&
    typeof y === "number" && Number.isFinite(y) &&
    typeof z === "number" && Number.isFinite(z)
  ) {
    return { x, y, z };
  }
  return null;
}

function inspectValue(
  value: unknown,
  depth = 0,
): {
  aliases: string[];
  label: string | null;
  kind: string | null;
  point: AuditPoint3 | null;
} {
  if (depth > 4) {
    return { aliases: [], label: null, kind: null, point: null };
  }

  const aliases: string[] = [];
  let label: string | null = null;
  let kind: string | null = null;
  let point: AuditPoint3 | null = null;

  if (Array.isArray(value)) {
    for (const item of value.slice(0, 64)) {
      const nested = inspectValue(item, depth + 1);
      aliases.push(...nested.aliases);
      label ??= nested.label;
      kind ??= nested.kind;
      point ??= nested.point;
    }
    return { aliases: uniqueAliases(aliases), label, kind, point };
  }

  const record = objectRecord(value);
  if (!record) {
    return { aliases, label, kind, point };
  }

  for (const [rawKey, item] of Object.entries(record)) {
    const key = canonicalKey(rawKey);
    const text = scalarText(item);

    if (IDENTITY_KEYS.has(key) && text !== null) {
      aliases.push(text);
    }
    if (label === null && LABEL_KEYS.some((candidate) => canonicalKey(candidate) === key)) {
      label = text;
    }
    if (kind === null && KIND_KEYS.some((candidate) => canonicalKey(candidate) === key)) {
      kind = text;
    }
    if (point === null && POINT_KEYS.has(key)) {
      point = parsePoint(item);
    }

    if (item !== null && typeof item === "object") {
      const nested = inspectValue(item, depth + 1);
      aliases.push(...nested.aliases);
      label ??= nested.label;
      kind ??= nested.kind;
      point ??= nested.point;
    }
  }

  return {
    aliases: uniqueAliases(aliases),
    label,
    kind,
    point,
  };
}

export function findingsFromWorkspace(
  workspace: AuditWorkspaceDto | null,
): readonly AuditFindingDto[] {
  if (!workspace) {
    return [];
  }
  const deterministic = workspace.payload.deterministic;

  if (deterministic.product_code === "family_audit") {
    return deterministic.family_audit?.findings ?? [];
  }
  if (deterministic.product_code === "bim_qa") {
    return deterministic.bim_qa?.findings ?? [];
  }

  const combined = deterministic.combined_audit;
  if (!combined) {
    return [];
  }
  return [
    ...(combined.family_findings ?? []),
    ...(combined.project_findings ?? []),
    ...(combined.cross_scope_findings ?? []),
  ];
}

export function ruleResultsFromWorkspace(
  workspace: AuditWorkspaceDto | null,
): readonly AuditRuleResultDto[] {
  if (!workspace) {
    return [];
  }
  const deterministic = workspace.payload.deterministic;
  if (deterministic.product_code === "family_audit") {
    return deterministic.family_audit?.rule_results ?? [];
  }
  if (deterministic.product_code === "bim_qa") {
    return deterministic.bim_qa?.rule_results ?? [];
  }
  return [
    ...(deterministic.family_audit?.rule_results ?? []),
    ...(deterministic.bim_qa?.rule_results ?? []),
  ];
}

export function targetsForFinding(
  finding: AuditFindingDto,
  evidence: readonly AuditEvidenceDto[],
): readonly AuditElementTarget[] {
  const evidenceIndex = new Map(
    evidence.map((item) => [item.evidence_id, item] as const),
  );
  const result: AuditElementTarget[] = [];
  const seen = new Set<string>();

  for (const evidenceId of finding.evidence_refs) {
    const item = evidenceIndex.get(evidenceId);
    if (!item) {
      continue;
    }

    const inspected = inspectValue(item.extracted_value);
    const locationElement = item.logical_location?.element?.trim() || null;
    const aliases = uniqueAliases([
      ...(locationElement ? [locationElement] : []),
      ...inspected.aliases,
    ]);
    const point = inspected.point;

    if (aliases.length === 0 && point === null) {
      continue;
    }

    const targetIdentity =
      aliases.map(normalizeElementAlias).sort().join("|") ||
      `${point?.x}:${point?.y}:${point?.z}`;
    const key = `${evidenceId}:${targetIdentity}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);

    result.push({
      key,
      evidenceId,
      aliases,
      label:
        inspected.label ??
        locationElement ??
        aliases[0] ??
        "Clash location",
      kind: inspected.kind,
      path: item.logical_location?.path?.trim() || null,
      point,
    });
  }

  return result;
}

export function allElementTargets(
  findings: readonly AuditFindingDto[],
  evidence: readonly AuditEvidenceDto[],
): readonly AuditElementTarget[] {
  const result: AuditElementTarget[] = [];
  const seen = new Set<string>();
  for (const finding of findings) {
    for (const target of targetsForFinding(finding, evidence)) {
      if (!seen.has(target.key)) {
        seen.add(target.key);
        result.push(target);
      }
    }
  }
  return result;
}

export type AuditSourceDto = {
  readonly source_ref: string;
  readonly filename: string;
};


export function startAudit(
  orderId: string,
  input: {
    jobId: string;
    idempotencyKey: string;
    sources: readonly AuditSourceDto[];
    metadata?: Readonly<Record<string, unknown>>;
  },
): Promise<StartAuditResponseDto> {
  if (input.sources.length === 0) {
    throw new TypeError(
      "At least one staged audit source is required.",
    );
  }

  return apiJsonRequest<StartAuditResponseDto>(
    `/orders/${encodeURIComponent(orderId)}/audit`,
    {
      method: "POST",
      headers: {
        "Idempotency-Key":
          input.idempotencyKey,
      },
      body: {
        job_id:
          input.jobId,

        sources:
          input.sources,

        ...(input.metadata
          ? {
              metadata:
                input.metadata,
            }
          : {}),
      },
    },
  );
}

export function getAuditStatus(
  orderId: string,
  signal?: AbortSignal,
): Promise<AuditStatusDto> {
  return apiRequest<AuditStatusDto>(
    `/orders/${encodeURIComponent(orderId)}/audit/status`,
    { method: "GET", signal },
  );
}

export function getAuditWorkspace(
  orderId: string,
  signal?: AbortSignal,
): Promise<AuditWorkspaceDto> {
  return apiRequest<AuditWorkspaceDto>(
    `/orders/${encodeURIComponent(orderId)}/audit/workspace`,
    { method: "GET", signal },
  );
}

export function listAuditReports(
  orderId: string,
  signal?: AbortSignal,
): Promise<ReportListDto> {
  return apiRequest<ReportListDto>(
    `/orders/${encodeURIComponent(orderId)}/reports`,
    { method: "GET", signal },
  );
}

export function issueAuditReportDownload(
  orderId: string,
  artifactId: string,
): Promise<ReportDownloadGrantDto> {
  return apiJsonRequest<ReportDownloadGrantDto>(
    `/orders/${encodeURIComponent(orderId)}/download/${encodeURIComponent(artifactId)}`,
    { method: "POST" },
  );
}
