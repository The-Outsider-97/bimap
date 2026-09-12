import {
  apiRequest,
  apiResponse,
} from "@/lib/api";


export type ExtractionSourceFormat =
  | "ifc"
  | "rvt"
  | "rfa"
  | "dwg"
  | "dxf"
  | "fbx"
  | "obj"
  | "glb"
  | "stl"
  | "ply";

export type ExtractionDataset =
  | "elements"
  | "properties"
  | "quantities"
  | "materials";

export type ExtractionEmailStatus =
  | "not_requested"
  | "accepted"
  | "failed";

export type DataExtractionCapability = {
  readonly source_format: ExtractionSourceFormat;
  readonly extensions: readonly string[];
  readonly datasets: readonly ExtractionDataset[];
  readonly package_content_type: string;
  readonly package_extension: string;
  readonly included_artifacts: readonly ["pdf", "json"];
};

export type DataExtractionCapabilities = {
  readonly sources: readonly DataExtractionCapability[];
  readonly email_available: boolean;
};

export type DataExtractionDownload = {
  readonly blob: Blob;
  readonly filename: string;
  readonly extractionId: string;
  readonly sourceSha256: string | null;
  readonly packageSha256: string | null;
  readonly sourceFormat: ExtractionSourceFormat | null;
  readonly sourceSchema: string | null;
  readonly entityCount: number | null;
  readonly packageBytes: number | null;
  readonly jsonFilename: string | null;
  readonly pdfFilename: string | null;
  readonly emailStatus: ExtractionEmailStatus;
};


function header(
  response: Response,
  name: string,
): string | null {
  return response.headers.get(name)?.trim() || null;
}


function nonNegativeInteger(
  response: Response,
  name: string,
): number | null {
  const raw = header(response, name);

  if (raw === null) {
    return null;
  }

  const parsed = Number(raw);

  return Number.isSafeInteger(parsed) && parsed >= 0
    ? parsed
    : null;
}


function sourceFormat(
  response: Response,
): ExtractionSourceFormat | null {
  const value = header(
    response,
    "x-bimap-source-format",
  );

  switch (value) {
    case "ifc":
    case "rvt":
    case "rfa":
    case "dwg":
    case "dxf":
    case "fbx":
    case "obj":
    case "glb":
    case "stl":
    case "ply":
      return value;
    default:
      return null;
  }
}


function emailStatus(
  response: Response,
): ExtractionEmailStatus {
  const value = header(
    response,
    "x-bimap-email-status",
  );

  if (
    value === "accepted" ||
    value === "failed" ||
    value === "not_requested"
  ) {
    return value;
  }

  return "not_requested";
}


export function getDataExtractionCapabilities(
  signal?: AbortSignal,
): Promise<DataExtractionCapabilities> {
  return apiRequest<DataExtractionCapabilities>(
    "/data-extractions/capabilities",
    {
      method: "GET",
      signal,
    },
  );
}


export async function extractModelData(
  input: {
    file: File;
    datasets:
      readonly ExtractionDataset[];
    extractionId: string;
    idempotencyKey: string;
    emailResult: boolean;
    project?: string;
    utcOffsetMinutes: number;
    signal?: AbortSignal;
  },
): Promise<DataExtractionDownload> {
  if (!(input.file instanceof File)) {
    throw new TypeError(
      "A source model file is required.",
    );
  }

  if (!input.extractionId.trim()) {
    throw new TypeError(
      "Extraction ID cannot be empty.",
    );
  }

  if (!input.idempotencyKey.trim()) {
    throw new TypeError(
      "Idempotency key cannot be empty.",
    );
  }

  const selected = Array.from(
    new Set(input.datasets),
  );

  if (selected.length === 0) {
    throw new TypeError(
      "Select at least one extraction dataset.",
    );
  }

  if (
    !Number.isSafeInteger(
      input.utcOffsetMinutes,
    ) ||
    input.utcOffsetMinutes < -840 ||
    input.utcOffsetMinutes > 840
  ) {
    throw new TypeError(
      "UTC offset is invalid.",
    );
  }

  const body = new FormData();
  body.set(
    "source",
    input.file,
    input.file.name,
  );
  body.set(
    "extraction_id",
    input.extractionId,
  );
  body.set(
    "datasets",
    selected.join(","),
  );
  body.set(
    "email_result",
    input.emailResult
      ? "true"
      : "false",
  );

  const project = input.project?.trim() ?? "";

  if (project) {
    body.set(
      "project",
      project,
    );
  }

  body.set(
    "utc_offset_minutes",
    String(
      input.utcOffsetMinutes,
    ),
  );

  const response = await apiResponse(
    "/data-extractions",
    {
      method: "POST",
      headers: {
        "Idempotency-Key":
          input.idempotencyKey,
      },
      body,
      signal: input.signal,
    },
  );

  const filename =
    header(
      response,
      "x-bimap-package-filename",
    ) ??
    "bimap_data_extraction.zip";

  return {
    blob: await response.blob(),
    filename,
    extractionId:
      header(
        response,
        "x-bimap-extraction-id",
      ) ??
      input.extractionId,
    sourceSha256:
      header(
        response,
        "x-bimap-source-sha256",
      ),
    packageSha256:
      header(
        response,
        "x-bimap-package-sha256",
      ),
    sourceFormat:
      sourceFormat(response),
    sourceSchema:
      header(
        response,
        "x-bimap-source-schema",
      ) ??
      header(
        response,
        "x-bimap-ifc-schema",
      ),
    entityCount:
      nonNegativeInteger(
        response,
        "x-bimap-entity-count",
      ) ??
      nonNegativeInteger(
        response,
        "x-bimap-product-count",
      ),
    packageBytes:
      nonNegativeInteger(
        response,
        "x-bimap-package-bytes",
      ),
    jsonFilename:
      header(
        response,
        "x-bimap-json-filename",
      ),
    pdfFilename:
      header(
        response,
        "x-bimap-pdf-filename",
      ),
    emailStatus:
      emailStatus(response),
  };
}
