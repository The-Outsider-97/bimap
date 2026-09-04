import {
  apiRequest,
} from "@/lib/api";


export type BimapProductCode =
  | "family_audit"
  | "bim_qa"
  | "combined_audit";


export type BimapProductScope =
  | "family"
  | "project"
  | "combined";


export type ProductDefinitionDto = {
  readonly code: BimapProductCode;
  readonly display_name: string;
  readonly scope: BimapProductScope;
  readonly description: string | null;
  readonly input_groups: readonly string[];
  readonly output_artifacts: readonly string[];
  readonly metadata: Readonly<
    Record<string, unknown>
  >;
};


export type ProductViewDto = {
  readonly product: ProductDefinitionDto;

  /*
   * Tier and limit contracts already belong to the Python domain layer.
   *
   * Until a dedicated frontend DTO/schema is formally exported, keep these
   * structures opaque instead of duplicating their evolving schema here.
   */
  readonly tiers: readonly Readonly<
    Record<string, unknown>
  >[];

  readonly limits: readonly Readonly<
    Record<string, unknown>
  >[];
};


export type LivenessDto = {
  readonly mode: "liveness";
  readonly state: string;
  readonly live: boolean;
};


export type ReadinessDto = {
  readonly mode: "readiness";
  readonly state: string;
  readonly ready: boolean;
};


export async function listProducts(
  signal?: AbortSignal,
): Promise<readonly ProductViewDto[]> {
  return apiRequest<
    readonly ProductViewDto[]
  >(
    "/products",
    {
      method: "GET",
      signal,
    },
  );
}


export async function getProduct(
  productCode: BimapProductCode,
  signal?: AbortSignal,
): Promise<ProductViewDto | null> {
  const products =
    await listProducts(signal);

  return (
    products.find(
      (entry) =>
        entry.product.code ===
        productCode,
    ) ?? null
  );
}


export async function getLiveness(
  signal?: AbortSignal,
): Promise<LivenessDto> {
  return apiRequest<LivenessDto>(
    "/health/live",
    {
      method: "GET",
      signal,
    },
  );
}


export async function getReadiness(
  signal?: AbortSignal,
): Promise<ReadinessDto> {
  return apiRequest<ReadinessDto>(
    "/health/ready",
    {
      method: "GET",
      signal,
    },
  );
}