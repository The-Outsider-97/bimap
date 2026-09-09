import {
  apiRequest,
  apiResponse,
} from "@/lib/api";

export type ModelSourceFormat = "ifc";
export type ModelTargetFormat = "glb" | "obj";

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
  readonly ifcSchema: string | null;
  readonly outputBytes: number | null;
};

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

function requireHeaderValue(
  response: Response,
  name: string,
): string | null {
  const value = response.headers.get(name);
  return value?.trim() || null;
}

function readOutputBytes(
  response: Response,
): number | null {
  const raw = requireHeaderValue(
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
    throw new TypeError("A source model file is required.");
  }
  if (!input.conversionId.trim()) {
    throw new TypeError("Conversion ID cannot be empty.");
  }
  if (!input.idempotencyKey.trim()) {
    throw new TypeError("Idempotency key cannot be empty.");
  }

  const body = new FormData();
  body.set("source", input.file, input.file.name);
  body.set("target_format", input.targetFormat);
  body.set("conversion_id", input.conversionId);

  const response = await apiResponse(
    "/conversions",
    {
      method: "POST",
      headers: {
        "Idempotency-Key": input.idempotencyKey,
      },
      body,
      signal: input.signal,
    },
  );

  const filename =
    requireHeaderValue(
      response,
      "x-bimap-output-filename",
    ) ??
    `converted-model.${input.targetFormat === "obj" ? "zip" : "glb"}`;

  return {
    blob: await response.blob(),
    filename,
    conversionId:
      requireHeaderValue(
        response,
        "x-bimap-conversion-id",
      ) ?? input.conversionId,
    sourceSha256: requireHeaderValue(
      response,
      "x-bimap-source-sha256",
    ),
    outputSha256: requireHeaderValue(
      response,
      "x-bimap-output-sha256",
    ),
    ifcSchema: requireHeaderValue(
      response,
      "x-bimap-ifc-schema",
    ),
    outputBytes: readOutputBytes(response),
  };
}
