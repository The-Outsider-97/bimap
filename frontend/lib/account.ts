import {
  apiJsonRequest,
  apiRequest,
  BimapApiError,
} from "@/lib/api";

export type AccountPlan =
  | "basic"
  | "pro"
  | "plus"
  | "business";

export type AuthMode =
  | "login"
  | "signup";

  export type UsageKind =
  | "audit"
  | "conversion"
  | "data_extraction";

export type AccountUsage = {
  used: number | null;
  limit: number | null;
  remaining: number | null;
  unlimited: boolean;

  renewal:
    | "weekly"
    | "monthly"
    | "none";
  periodStart?: string | null;
  periodEnd?: string | null;

  bonusCredits: number;
};

export type AccountRewards = {
  points: number;

  basePurchaseDiscountPercent:
    number;

  maxEffectivePurchaseDiscountPercent:
    number;

  maxRewardDiscountPercent:
    number;
};

export type AccountSummary = {
  audits: {
    inProgress:
      readonly AccountAudit[];
    done:
      readonly AccountAudit[];
    cancelled:
      readonly AccountAudit[];
  };

  purchases: {
    threeD:
      readonly AccountPurchase[];
    twoD:
      readonly AccountPurchase[];
  };

  currentPlan: AccountPlan;

  usage: {
    audit: AccountUsage;
    conversion: AccountUsage;
    dataExtraction:
      AccountUsage;
  };

  rewards: AccountRewards;
};






export type AccountProfile = {
  userId: string;
  username: string;
  name: string;
  surname: string;
  occupation?: string | null;
  business?: string | null;
  phoneE164: string;
  email: string;
  avatarUrl?: string | null;
  plan: AccountPlan;
};

export type SignupPayload = {
  name: string;
  surname: string;
  occupation?: string;
  business?: string;
  country: string;
  phoneE164: string;
  username: string;
  email: string;
  password: string;
};

export type SignupResponse = {
  verificationRequired: true;
  username: string;
  emailMasked?: string;
  phoneMasked?: string;
};

export type VerifySignupPayload = {
  username: string;
  emailCode: string;
  smsCode: string;
};

export type LoginPayload = {
  username: string;
  password: string;
};

export type AccountAudit = {
  id: string;
  product: string;
  projectAlias?: string | null;
  status:
    | "in_progress"
    | "done"
    | "cancelled";
  updatedAt?: string | null;
};

export type AccountPurchase = {
  id: string;
  title: string;
  kind: "3d" | "2d";
  format: string;
  purchasedAt?: string | null;
  downloadHref?: string | null;
};

export const PLAN_OPTIONS = [
  {
    code: "basic",
    name: "Basic",
    description:
      "Free access with limited platform usage.",
    discount: 0,
  },
  {
    code: "pro",
    name: "Pro",
    description:
      "Higher usage limits than Basic.",
    discount: 5,
  },
  {
    code: "plus",
    name: "Plus",
    description:
      "Higher usage limits than Pro.",
    discount: 10,
  },
  {
    code: "business",
    name: "Business",
    description:
      "Unlimited platform usage for business workflows.",
    discount: 15,
  },
] as const satisfies readonly {
  code: AccountPlan;
  name: string;
  description: string;
  discount: number;
}[];

export function getApiErrorMessage(
  error: unknown,
): string {
  if (
    error instanceof BimapApiError &&
    error.payload &&
    typeof error.payload === "object"
  ) {
    const payload =
      error.payload as Record<
        string,
        unknown
      >;

    const candidates = [
      payload.detail,
      payload.message,
      payload.title,
    ];

    for (
      const candidate
      of candidates
    ) {
      if (
        typeof candidate ===
          "string" &&
        candidate.trim()
      ) {
        return candidate.trim();
      }
    }
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "The request could not be completed.";
}

export function signUp(
  payload: SignupPayload,
): Promise<SignupResponse> {
  return apiJsonRequest<SignupResponse>(
    "/auth/signup",
    {
      method: "POST",
      body: payload,
    },
  );
}

export function verifySignup(
  payload:
    VerifySignupPayload,
): Promise<AccountProfile> {
  return apiJsonRequest<AccountProfile>(
    "/auth/verify-signup",
    {
      method: "POST",
      body: payload,
    },
  );
}

export function login(
  payload: LoginPayload,
): Promise<AccountProfile> {
  return apiJsonRequest<AccountProfile>(
    "/auth/login",
    {
      method: "POST",
      body: payload,
    },
  );
}

export function logout():
  Promise<void> {
  return apiJsonRequest<void>(
    "/auth/logout",
    {
      method: "POST",
    },
  );
}

export function resendSignupCodes(
  username: string,
): Promise<SignupResponse> {
  return apiJsonRequest<SignupResponse>(
    "/auth/resend-signup-codes",
    {
      method: "POST",
      body: {
        username,
      },
    },
  );
}

export function getCurrentAccount():
  Promise<AccountProfile> {
  return apiRequest<AccountProfile>(
    "/account/me",
  );
}

export function getAccountSummary():
  Promise<AccountSummary> {
  return apiRequest<AccountSummary>(
    "/account/summary",
  );
}

export function uploadAvatar(
  file: File,
): Promise<AccountProfile> {
  const body = new FormData();
  body.set("avatar", file);

  return apiRequest<AccountProfile>(
    "/account/avatar",
    {
      method: "POST",
      body,
    },
  );
}
