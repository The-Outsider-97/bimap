import {
  apiRequest,
  apiResponse,
} from "@/lib/api";


export type ModelSourceFormat =
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

export type ModelTargetFormat =
  | "glb"
  | "obj";

export type ModelConversionCapability = {
  readonly source_format: ModelSourceFormat;
  readonly extensions: readonly string[];
  readonly target_formats: readonly ModelTargetFormat[];
};

export type ModelConversionCapabilities = {
  readonly sources: readonly ModelConversionCapability[];
};

export type ModelConversionDownload = {
  readonly blob: Blob;
  readonly filename: string;
  readonly conversionId: string;
  readonly sourceSha256: string | null;
  readonly outputSha256: string | null;
  readonly sourceFormat: ModelSourceFormat | null;
  readonly sourceSchema: string | null;
  readonly outputBytes: number | null;
};


function header(
  response: Response,
  name: string,
): string | null {
  return response.headers.get(name)?.trim() || null;
}


function sourceFormat(
  response: Response,
): ModelSourceFormat | null {
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


function readOutputBytes(
  response: Response,
): number | null {
  const raw = header(
    response,
    "x-bimap-output-bytes",
  );

  if (raw === null) {
    return null;
  }

  const parsed = Number(raw);

  return Number.isSafeInteger(parsed) && parsed >= 0
    ? parsed
    : null;
}


export function getModelConversionCapabilities(
  signal?: AbortSignal,
): Promise<ModelConversionCapabilities> {
  return apiRequest<ModelConversionCapabilities>(
    "/conversions/capabilities",
    {
      method: "GET",
      signal,
    },
  );
}


export async function convertModel(
  input: {
    file: File;
    targetFormat: ModelTargetFormat;
    conversionId: string;
    idempotencyKey: string;
    signal?: AbortSignal;
  },
): Promise<ModelConversionDownload> {
  if (!(input.file instanceof File)) {
    throw new TypeError(
      "A source model file is required.",
    );
  }

  if (!input.conversionId.trim()) {
    throw new TypeError(
      "Conversion ID cannot be empty.",
    );
  }

  if (!input.idempotencyKey.trim()) {
    throw new TypeError(
      "Idempotency key cannot be empty.",
    );
  }

  const body = new FormData();
  body.set(
    "source",
    input.file,
    input.file.name,
  );
  body.set(
    "target_format",
    input.targetFormat,
  );
  body.set(
    "conversion_id",
    input.conversionId,
  );

  const response = await apiResponse(
    "/conversions",
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
      "x-bimap-output-filename",
    ) ??
    `converted-model.${input.targetFormat === "obj" ? "zip" : "glb"}`;

  return {
    blob: await response.blob(),
    filename,
    conversionId:
      header(
        response,
        "x-bimap-conversion-id",
      ) ?? input.conversionId,
    sourceSha256:
      header(
        response,
        "x-bimap-source-sha256",
      ),
    outputSha256:
      header(
        response,
        "x-bimap-output-sha256",
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
    outputBytes:
      readOutputBytes(response),
  };
}
