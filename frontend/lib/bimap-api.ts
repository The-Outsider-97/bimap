import {
  apiJsonRequest,
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
  readonly code:
    BimapProductCode;

  readonly display_name:
    string;

  readonly scope:
    BimapProductScope;

  readonly description:
    string | null;

  readonly input_groups:
    readonly string[];

  readonly output_artifacts:
    readonly string[];

  readonly metadata:
    Readonly<
      Record<
        string,
        unknown
      >
    >;
};


export type ProductViewDto = {
  readonly product:
    ProductDefinitionDto;

  readonly tiers:
    readonly Readonly<
      Record<
        string,
        unknown
      >
    >[];

  readonly limits:
    readonly Readonly<
      Record<
        string,
        unknown
      >
    >[];
};


export type LivenessDto = {
  readonly mode:
    "liveness";

  readonly state:
    string;

  readonly live:
    boolean;
};


export type ReadinessDto = {
  readonly mode:
    "readiness";

  readonly state:
    string;

  readonly ready:
    boolean;
};


export type BimapOrderState =
  | "draft"
  | "uploading"
  | "upload_validated"
  | "entitled"
  | "payment_pending"
  | "paid"
  | "queued"
  | "ingesting"
  | "analyzing"
  | "governance_review"
  | "packaging"
  | "delivered"
  | "upload_rejected"
  | "payment_failed"
  | "analysis_failed"
  | "review_required"
  | "refunded"
  | "cancelled"
  | "expired";


export type BimapOrderDto = {
  readonly schema_version:
    string;

  readonly order_id:
    string;

  readonly account_id:
    string;

  readonly product_code:
    BimapProductCode;

  readonly tier_code:
    string | null;

  readonly project_alias:
    string | null;

  readonly state:
    BimapOrderState;

  readonly created_at:
    string;

  readonly updated_at:
    string;

  readonly upload_session_id:
    string | null;

  readonly retention_expires_at:
    string | null;

  readonly version:
    number;

  readonly metadata?:
    Readonly<
      Record<
        string,
        unknown
      >
    >;

  readonly events?:
    readonly Readonly<
      Record<
        string,
        unknown
      >
    >[];
};


export type PaymentCheckoutDto = {
  readonly order_id:
    string;

  readonly checkout_id:
    string;

  readonly provider_name:
    string;

  readonly amount:
    string;

  readonly currency:
    string;

  readonly customer_action_url:
    string | null;

  readonly expires_at:
    string | null;
};


function requireIdempotencyKey(
  value: string,
): string {
  const normalized =
    value.trim();

  if (!normalized) {
    throw new TypeError(
      "Idempotency key cannot be empty.",
    );
  }

  return normalized;
}


function idempotencyHeaders(
  value: string,
): HeadersInit {
  return {
    "Idempotency-Key":
      requireIdempotencyKey(
        value,
      ),
  };
}


export async function listProducts(
  signal?: AbortSignal,
): Promise<
  readonly ProductViewDto[]
> {
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
  productCode:
    BimapProductCode,
  signal?: AbortSignal,
): Promise<
  ProductViewDto | null
> {
  const products =
    await listProducts(
      signal,
    );

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
  return apiRequest<
    LivenessDto
  >(
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
  return apiRequest<
    ReadinessDto
  >(
    "/health/ready",
    {
      method: "GET",
      signal,
    },
  );
}


export function createOrder(
  input: {
    productCode:
      BimapProductCode;

    tierCode?:
      string | null;

    projectAlias?:
      string | null;
  },
): Promise<BimapOrderDto> {
  return apiJsonRequest<
    BimapOrderDto
  >(
    "/orders",
    {
      method: "POST",
      body: {
        product_code:
          input.productCode,

        ...(
          input.tierCode
            ? {
                tier_code:
                  input.tierCode,
              }
            : {}
        ),

        ...(
          input.projectAlias
            ? {
                project_alias:
                  input.projectAlias,
              }
            : {}
        ),
      },
    },
  );
}


export function getOrder(
  orderId: string,
  signal?: AbortSignal,
): Promise<BimapOrderDto> {
  return apiRequest<
    BimapOrderDto
  >(
    `/orders/${
      encodeURIComponent(
        orderId,
      )
    }`,
    {
      method: "GET",
      signal,
    },
  );
}


export function cancelOrder(
  orderId: string,
  idempotencyKey: string,
): Promise<BimapOrderDto> {
  return apiJsonRequest<
    BimapOrderDto
  >(
    `/orders/${
      encodeURIComponent(
        orderId,
      )
    }/cancel`,
    {
      method: "POST",
      headers:
        idempotencyHeaders(
          idempotencyKey,
        ),
    },
  );
}


export function beginOrderUploads(
  orderId: string,
  idempotencyKey: string,
): Promise<BimapOrderDto> {
  return apiJsonRequest<
    BimapOrderDto
  >(
    `/orders/${
      encodeURIComponent(
        orderId,
      )
    }/uploads`,
    {
      method: "POST",
      headers:
        idempotencyHeaders(
          idempotencyKey,
        ),
    },
  );
}


export function validateOrderUploads(
  orderId: string,
  manifest:
    Readonly<
      Record<
        string,
        unknown
      >
    >,
  idempotencyKey: string,
): Promise<BimapOrderDto> {
  return apiJsonRequest<
    BimapOrderDto
  >(
    `/orders/${
      encodeURIComponent(
        orderId,
      )
    }/validate`,
    {
      method: "POST",
      headers:
        idempotencyHeaders(
          idempotencyKey,
        ),
      body:
        manifest,
    },
  );
}


export function grantOrderEntitlement(
  orderId: string,
  idempotencyKey: string,
): Promise<BimapOrderDto> {
  return apiJsonRequest<
    BimapOrderDto
  >(
    `/orders/${
      encodeURIComponent(
        orderId,
      )
    }/entitle`,
    {
      method: "POST",
      headers:
        idempotencyHeaders(
          idempotencyKey,
        ),
    },
  );
}


export function beginOrderCheckout(
  orderId: string,
  idempotencyKey: string,
): Promise<
  PaymentCheckoutDto
> {
  return apiJsonRequest<
    PaymentCheckoutDto
  >(
    `/orders/${
      encodeURIComponent(
        orderId,
      )
    }/checkout`,
    {
      method: "POST",
      headers:
        idempotencyHeaders(
          idempotencyKey,
        ),
    },
  );
}
