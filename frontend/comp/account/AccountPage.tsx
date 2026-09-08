"use client";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  SiteShell,
} from "@/comp/shell/SiteShell";

import {
  useAccount,
} from "./AccountProvider";

import {
  getAccountSummary,
  getApiErrorMessage,
  PLAN_OPTIONS,
  uploadAvatar,
  type AccountAudit,
  type AccountSummary,
} from "@/lib/account";

import type {
  TocItem,
} from "@/lib/types";

const toc:
  readonly TocItem[] = [
    {
      id: "account-profile",
      label: "Profile",
    },
    {
      id: "account-audits",
      label: "Audits",
    },
    {
      id: "account-purchases",
      label: "Purchases",
    },
    {
      id: "account-plan",
      label: "Plan",
    },
  ];

type AuditTab =
  | "inProgress"
  | "done"
  | "cancelled";

function formatUsage(
  usage:
    | AccountSummary["usage"]["audit"]
    | AccountSummary["usage"]["conversion"]
    | AccountSummary["usage"]["dataExtraction"],
) {
  if (usage.unlimited) {
    return "Unlimited";
  }

  return `${usage.used ?? 0} / ${usage.limit ?? 0}`;
}

function renewalLabel(
  renewal:
    | "weekly"
    | "monthly"
    | "none",
) {
  if (renewal === "weekly") {
    return "this week";
  }

  if (renewal === "monthly") {
    return "this month";
  }

  return "";
}

export function AccountPage() {
  const {
    account,
    status,
    openAuth,
    refresh,
    logout,
  } = useAccount();

  const [
    summary,
    setSummary,
  ] =
    useState<
      AccountSummary | null
    >(null);

  const [
    error,
    setError,
  ] =
    useState("");

  const [
    auditTab,
    setAuditTab,
  ] =
    useState<AuditTab>(
      "inProgress",
    );

  const [
    avatarBusy,
    setAvatarBusy,
  ] =
    useState(false);

  useEffect(() => {
    if (
      status !==
      "signed-in"
    ) {
      return;
    }

    let cancelled = false;

    void (
      async () => {
        try {
          const result =
            await getAccountSummary();

          if (!cancelled) {
            setSummary(result);
            setError("");
          }
        } catch (requestError) {
          if (!cancelled) {
            setError(
              getApiErrorMessage(
                requestError,
              ),
            );
          }
        }
      }
    )();

    return () => {
      cancelled = true;
    };
  }, [status]);

  const audits:
    readonly AccountAudit[] =
    useMemo(() => {
      if (!summary) {
        return [];
      }

      return summary.audits[
        auditTab
      ];
    }, [
      auditTab,
      summary,
    ]);

  if (
    status === "loading"
  ) {
    return (
      <SiteShell
        toc={toc}
        pageDescription=
          "Manage your BIMAP account, audits, purchases and plan."
      >
        <main className="account-page">
          <section className="account-page__loading">
            <span />
            <p>
              Loading account
            </p>
          </section>
        </main>
      </SiteShell>
    );
  }

  if (
    status === "signed-out" ||
    !account
  ) {
    return (
      <SiteShell
        toc={toc}
        pageDescription=
          "Manage your BIMAP account, audits, purchases and plan."
      >
        <main className="account-page">
          <section className="account-signed-out">
            <p className="eyebrow">
              <span
                aria-hidden="true"
              >
                ●
              </span>

              BIMAP account
            </p>

            <h1>
              Your workspace
              starts here.
            </h1>

            <p>
              Log in to view audit
              activity, purchased 3D
              and 2D content, and your
              current BIMAP plan.
            </p>

            <div>
              <button
                type="button"
                className="account-submit"
                onClick={() =>
                  openAuth(
                    "login",
                  )
                }
              >
                Log in
              </button>

              <button
                type="button"
                className="account-secondary-action"
                onClick={() =>
                  openAuth(
                    "signup",
                  )
                }
              >
                Create account
              </button>
            </div>
          </section>
        </main>
      </SiteShell>
    );
  }

  const currentPlan =
    summary?.currentPlan ??
    account.plan;

  const handleAvatar =
    async (
      file:
        File | undefined,
    ) => {
      if (!file) {
        return;
      }

      setAvatarBusy(true);
      setError("");

      try {
        await uploadAvatar(
          file,
        );

        await refresh();
      } catch (uploadError) {
        setError(
          getApiErrorMessage(
            uploadError,
          ),
        );
      } finally {
        setAvatarBusy(false);
      }
    };

  return (
    <SiteShell
      toc={toc}
      pageDescription=
        "Manage your BIMAP profile, audits, purchases and subscription plan."
    >
      <main className="account-page">

        <section
          className="account-profile-section"
          id="account-profile"
        >
          <div className="content-width">
            <div className="account-page__heading">
              <p className="eyebrow">
                <span
                  aria-hidden="true"
                >
                  ●
                </span>

                BIMAP account
              </p>

              <h1>
                {account.name}{" "}
                {account.surname}
              </h1>
            </div>

            {error ? (
              <div
                className="account-page__error"
                role="status"
              >
                {error}
              </div>
            ) : null}

            <div className="account-profile-grid">
              <div className="account-avatar-card">
                <div className="account-avatar-card__image">
                  <img
                    src={
                      account.avatarUrl ||
                      "/default-user.png"
                    }
                    alt={
                      `${account.name} ${account.surname}`
                    }
                  />
                </div>

                <div>
                  <p>
                    Profile image
                  </p>

                  <h2>
                    {account.username}
                  </h2>

                  <label
                    className="account-avatar-upload"
                  >
                    <input
                      type="file"
                      accept="image/*"
                      disabled={
                        avatarBusy
                      }
                      onChange={(
                        event,
                      ) => {
                        void handleAvatar(
                          event
                            .target
                            .files?.[0],
                        );

                        event.target.value =
                          "";
                      }}
                    />

                    <span>
                      {avatarBusy
                        ? "Uploading..."
                        : "Change image"}
                    </span>
                  </label>
                </div>
              </div>

              <dl className="account-profile-data">
                <div>
                  <dt>
                    Email
                  </dt>

                  <dd>
                    {account.email}
                  </dd>
                </div>

                <div>
                  <dt>
                    Phone
                  </dt>

                  <dd>
                    {
                      account.phoneE164
                    }
                  </dd>
                </div>

                <div>
                  <dt>
                    Occupation
                  </dt>

                  <dd>
                    {
                      account.occupation ||
                      "Not provided"
                    }
                  </dd>
                </div>

                <div>
                  <dt>
                    Business
                  </dt>

                  <dd>
                    {
                      account.business ||
                      "Not provided"
                    }
                  </dd>
                </div>
              </dl>
            </div>
          </div>
        </section>

<section
  className="section account-section"
  id="account-plan"
>
<section
  className="section account-section"
  id="account-audits"
>
  <div className="content-width">
    <div className="account-section__heading">
      <div>
        <p className="eyebrow">
          <span
            aria-hidden="true"
          >
            ●
          </span>

          Audit activity
        </p>

        <h2>
          Your audits
        </h2>
      </div>

      <div className="account-tabs">
        <button
          type="button"
          data-active={
            auditTab ===
            "inProgress"
          }
          onClick={() =>
            setAuditTab(
              "inProgress",
            )
          }
        >
          In progress
        </button>

        <button
          type="button"
          data-active={
            auditTab ===
            "done"
          }
          onClick={() =>
            setAuditTab(
              "done",
            )
          }
        >
          Done
        </button>

        <button
          type="button"
          data-active={
            auditTab ===
            "cancelled"
          }
          onClick={() =>
            setAuditTab(
              "cancelled",
            )
          }
        >
          Cancelled
        </button>
      </div>
    </div>

    <div className="account-record-list">
      {audits.length >
      0 ? (
        audits.map(
          (audit) => (
            <article
              key={
                audit.id
              }
            >
              <span>
                {
                  audit.status
                    .replace(
                      "_",
                      " ",
                    )
                }
              </span>

              <div>
                <h3>
                  {
                    audit.product
                  }
                </h3>

                <p>
                  {
                    audit.projectAlias ||
                    audit.id
                  }
                </p>
              </div>

              <small>
                {
                  audit.updatedAt ||
                  "No update timestamp"
                }
              </small>
            </article>
          ),
        )
      ) : (
        <div className="account-empty-state">
          <span>
            00
          </span>

          <div>
            <h3>
              No audits in this state.
            </h3>

            <p>
              Audit records appear
              here when they are
              associated with your
              account.
            </p>
          </div>
        </div>
      )}
    </div>
  </div>
</section>

<section
  className="
    section
    account-section
    account-section--surface
  "
  id="account-purchases"
>
  <div className="content-width">
    <div className="account-section__heading">
      <div>
        <p className="eyebrow">
          <span
            aria-hidden="true"
          >
            ●
          </span>

          Digital library
        </p>

        <h2>
          Your purchases
        </h2>
      </div>
    </div>

    <div className="account-purchase-columns">
      <div>
        <h3>
          3D content
        </h3>

        <div className="account-record-list">
          {summary &&
          summary
            .purchases
            .threeD
            .length >
            0 ? (
            summary
              .purchases
              .threeD
              .map(
                (
                  purchase,
                ) => (
                  <article
                    key={
                      purchase.id
                    }
                  >
                    <span>
                      {
                        purchase.format
                      }
                    </span>

                    <div>
                      <h4>
                        {
                          purchase.title
                        }
                      </h4>

                      <p>
                        3D purchase
                      </p>
                    </div>

                    {
                      purchase.downloadHref
                        ? (
                          <a
                            href={
                              purchase.downloadHref
                            }
                          >
                            Download
                          </a>
                        )
                        : (
                          <small>
                            Download unavailable
                          </small>
                        )
                    }
                  </article>
                ),
              )
          ) : (
            <p className="account-inline-empty">
              No 3D purchases
              recorded.
            </p>
          )}
        </div>
      </div>

      <div>
        <h3>
          2D content
        </h3>

        <div className="account-record-list">
          {summary &&
          summary
            .purchases
            .twoD
            .length >
            0 ? (
            summary
              .purchases
              .twoD
              .map(
                (
                  purchase,
                ) => (
                  <article
                    key={
                      purchase.id
                    }
                  >
                    <span>
                      {
                        purchase.format
                      }
                    </span>

                    <div>
                      <h4>
                        {
                          purchase.title
                        }
                      </h4>

                      <p>
                        2D purchase
                      </p>
                    </div>

                    {
                      purchase.downloadHref
                        ? (
                          <a
                            href={
                              purchase.downloadHref
                            }
                          >
                            Download
                          </a>
                        )
                        : (
                          <small>
                            Download unavailable
                          </small>
                        )
                    }
                  </article>
                ),
              )
          ) : (
            <p className="account-inline-empty">
              No 2D purchases
              recorded.
            </p>
          )}
        </div>
      </div>
    </div>
  </div>
</section>
  <div className="content-width">
    <div className="account-section__heading">
      <div>
        <p className="eyebrow">
          <span aria-hidden="true">
            ●
          </span>

          Subscription & usage
        </p>

        <h2>
          Your plan
        </h2>
      </div>
    </div>

    {summary ? (
      <>
        <div className="account-current-plan">
          <div className="account-current-plan__identity">
            <span>
              Current plan
            </span>

            <h3>
              {
                PLAN_OPTIONS.find(
                  (
                    plan,
                  ) =>
                    plan.code ===
                    currentPlan,
                )?.name ??
                currentPlan
              }
            </h3>

            <p>
              Your recurring usage
              automatically renews
              according to each
              entitlement period.
            </p>
          </div>

          <div className="account-current-plan__discount">
            <span>
              Purchase discount
            </span>

            <strong>
              {
                summary
                  .rewards
                  .basePurchaseDiscountPercent
              }
              %
            </strong>

            <small>
              Up to{" "}
              {
                summary
                  .rewards
                  .maxEffectivePurchaseDiscountPercent
              }
              % with points
            </small>
          </div>

          <div className="account-current-plan__points">
            <span>
              Reward points
            </span>

            <strong>
              {
                summary
                  .rewards
                  .points
                  .toLocaleString()
              }
            </strong>

            <small>
              Available balance
            </small>
          </div>
        </div>

        <div className="account-usage-grid">
          {[
            {
              label:
                "Audits",
              usage:
                summary
                  .usage
                  .audit,
            },
            {
              label:
                "Model conversions",
              usage:
                summary
                  .usage
                  .conversion,
            },
            {
              label:
                "Data extractions",
              usage:
                summary
                  .usage
                  .dataExtraction,
            },
          ].map(
            (
              item,
            ) => (
              <article
                className="account-usage-card"
                key={
                  item.label
                }
              >
                <div className="account-usage-card__heading">
                  <span>
                    {
                      item.label
                    }
                  </span>

                  <small>
                    {
                      renewalLabel(
                        item
                          .usage
                          .renewal,
                      )
                    }
                  </small>
                </div>

                <strong>
                  {
                    formatUsage(
                      item.usage,
                    )
                  }
                </strong>

                {!item
                  .usage
                  .unlimited ? (
                  <>
                    <div
                      className="account-usage-meter"
                      aria-hidden="true"
                    >
                      <span
                        style={{
                          width:
                            `${
                              Math.min(
                                100,
                                (
                                  (
                                    item
                                      .usage
                                      .used ??
                                    0
                                  ) /
                                  Math.max(
                                    1,
                                    item
                                      .usage
                                      .limit ??
                                    1,
                                  )
                                ) *
                                  100,
                              )
                            }%`,
                        }}
                      />
                    </div>

                    <div className="account-usage-card__meta">
                      <span>
                        Remaining{" "}
                        {
                          item
                            .usage
                            .remaining ??
                          0
                        }
                      </span>

                      <span>
                        Bonus{" "}
                        {
                          item
                            .usage
                            .bonusCredits
                        }
                      </span>
                    </div>
                  </>
                ) : (
                  <p>
                    No recurring
                    usage limit.
                  </p>
                )}
              </article>
            ),
          )}
        </div>

        <div className="account-reward-summary">
          <div>
            <span>
              Base discount
            </span>

            <strong>
              {
                summary
                  .rewards
                  .basePurchaseDiscountPercent
              }
              %
            </strong>
          </div>

          <div>
            <span>
              Points discount capacity
            </span>

            <strong>
              +
              {
                summary
                  .rewards
                  .maxRewardDiscountPercent
              }
              %
            </strong>
          </div>

          <div>
            <span>
              Maximum combined discount
            </span>

            <strong>
              {
                summary
                  .rewards
                  .maxEffectivePurchaseDiscountPercent
              }
              %
            </strong>
          </div>
        </div>
      </>
    ) : (
      <div className="account-inline-empty">
        Account usage information
        is unavailable.
      </div>
    )}

    <div className="account-plan-grid">
      {PLAN_OPTIONS.map(
        (
          plan,
        ) => (
          <article
            className="account-plan-card"
            data-active={
              currentPlan ===
              plan.code
            }
            key={
              plan.code
            }
          >
            <div>
              <span>
                {plan.name}
              </span>

              {currentPlan ===
              plan.code ? (
                <strong>
                  Current plan
                </strong>
              ) : null}
            </div>

            <h3>
              {
                plan.discount
              }
              %
            </h3>

            <p>
              Base purchase
              discount
            </p>

            <small>
              {
                plan.description
              }
            </small>
          </article>
        ),
      )}
    </div>

    <div className="account-actions-row">
      <button
        type="button"
        className="account-secondary-action"
        onClick={() => {
          void logout();
        }}
      >
        Log out
      </button>
    </div>
  </div>
</section>
      </main>
    </SiteShell>
  );
}
