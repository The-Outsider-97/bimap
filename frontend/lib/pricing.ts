import {
  apiJsonRequest,
} from "@/lib/api";

import type {
  AccountPlan,
} from "@/lib/account";

export type BillingCadence =
  | "monthly"
  | "yearly";

export const SUBSCRIPTION_CURRENCY =
  "EUR" as const;

export const ANNUAL_BILLABLE_MONTHS =
  11 as const;

export type PlanQuota = {
  readonly audit:
    | number
    | "unlimited";
  readonly conversion:
    | number
    | "unlimited";
  readonly dataExtraction:
    | number
    | "unlimited";
};

export type PlanDefinition = {
  readonly code:
    AccountPlan;
  readonly rank:
    number;
  readonly name:
    string;
  readonly description:
    string;
  readonly monthlyPriceEur:
    number;
  readonly baseDiscount:
    number;
  readonly maxDiscount:
    number;
  readonly quota:
    PlanQuota;
};

export const PLAN_CATALOG:
  readonly PlanDefinition[] = [
    {
      code: "basic",
      rank: 0,
      name: "Basic",
      description:
        "Free access with limited platform usage.",
      monthlyPriceEur: 0,
      baseDiscount: 0,
      maxDiscount: 20,
      quota: {
        audit: 5,
        conversion: 3,
        dataExtraction: 5,
      },
    },
    {
      code: "pro",
      rank: 1,
      name: "Pro",
      description:
        "Higher recurring usage limits than Basic.",
      monthlyPriceEur: 10,
      baseDiscount: 5,
      maxDiscount: 30,
      quota: {
        audit: 15,
        conversion: 9,
        dataExtraction: 15,
      },
    },
    {
      code: "plus",
      rank: 2,
      name: "Plus",
      description:
        "Higher recurring usage limits than Pro.",
      monthlyPriceEur: 30,
      baseDiscount: 10,
      maxDiscount: 40,
      quota: {
        audit: 25,
        conversion: 15,
        dataExtraction: 25,
      },
    },
    {
      code: "business",
      rank: 3,
      name: "Business",
      description:
        "Unlimited recurring platform usage for business workflows.",
      monthlyPriceEur: 90,
      baseDiscount: 15,
      maxDiscount: 50,
      quota: {
        audit: "unlimited",
        conversion: "unlimited",
        dataExtraction: "unlimited",
      },
    },
  ] as const;

export type SubscriptionChangeRequest = {
  readonly targetPlan:
    AccountPlan;
  readonly billingCadence:
    BillingCadence;
};

export type SubscriptionChangeResponse = {
  readonly currentPlan:
    AccountPlan;
  readonly targetPlan:
    AccountPlan;

  readonly status:
    | "trial_started"
    | "checkout_required"
    | "scheduled"
    | "changed";

  readonly trialEligible?:
    boolean;

  readonly trialEndsAt?:
    string | null;

  readonly checkoutUrl?:
    string | null;
};

export function getPlanPrice(
  plan:
    PlanDefinition,
  cadence:
    BillingCadence,
): number {
  if (
    cadence ===
    "monthly"
  ) {
    return plan.monthlyPriceEur;
  }

  return (
    plan.monthlyPriceEur *
    ANNUAL_BILLABLE_MONTHS
  );
}

export function getYearlySavings(
  plan:
    PlanDefinition,
): number {
  return plan.monthlyPriceEur;
}

export function getYearlyEffectiveMonthlyPrice(
  plan:
    PlanDefinition,
): number {
  return (
    getPlanPrice(
      plan,
      "yearly",
    ) / 12
  );
}

/**
 * This is deliberately a subscription boundary, not a direct plan mutation.
 *
 * Do not replace this request with a browser-accessible call to
 * AccountService.assign_plan(). Paid-plan entitlement changes must remain
 * server-authoritative and must validate trial/payment state first.
 */
export function requestSubscriptionChange(
  targetPlan:
    AccountPlan,
  billingCadence:
    BillingCadence,
): Promise<SubscriptionChangeResponse> {
  return apiJsonRequest<SubscriptionChangeResponse>(
    "/account/subscription/change",
    {
      method: "POST",
      body: {
        targetPlan,
        billingCadence,
      } satisfies SubscriptionChangeRequest,
    },
  );
}

export function getPlanDefinition(
  plan:
    AccountPlan,
): PlanDefinition {
  const definition =
    PLAN_CATALOG.find(
      (item) =>
        item.code === plan,
    );

  if (!definition) {
    throw new Error(
      `Unknown BIMAP plan: ${plan}`,
    );
  }

  return definition;
}

export function isUpgrade(
  currentPlan:
    AccountPlan,
  targetPlan:
    AccountPlan,
): boolean {
  return (
    getPlanDefinition(
      targetPlan,
    ).rank >
    getPlanDefinition(
      currentPlan,
    ).rank
  );
}

export function isDowngrade(
  currentPlan:
    AccountPlan,
  targetPlan:
    AccountPlan,
): boolean {
  return (
    getPlanDefinition(
      targetPlan,
    ).rank <
    getPlanDefinition(
      currentPlan,
    ).rank
  );
}
