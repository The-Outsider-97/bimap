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
} from "@/comp/account/AccountProvider";

import {
  getAccountSummary,
  getApiErrorMessage,
  type AccountPlan,
  type AccountSummary,
} from "@/lib/account";

import {
  PLAN_CATALOG,
  isDowngrade,
  isUpgrade,
  requestSubscriptionChange,
  type PlanDefinition,
} from "@/lib/pricing";

import type {
  TocItem,
} from "@/lib/types";

import styles from "./PricingPage.module.css";

const toc:
  readonly TocItem[] = [
    {
      id: "plans-overview",
      label: "Overview",
    },
    {
      id: "plans",
      label: "Plans",
    },
    {
      id: "trial-policy",
      label: "Free trial",
    },
    {
      id: "plan-comparison",
      label: "Comparison",
    },
  ];

function formatQuota(
  value:
    number
    | "unlimited",
  cadence:
    "month"
    | "week",
): string {
  if (
    value ===
    "unlimited"
  ) {
    return "Unlimited";
  }

  return `${value} / ${cadence}`;
}

function actionLabel(
  plan:
    PlanDefinition,
  currentPlan:
    AccountPlan,
  signedIn:
    boolean,
): string {
  if (!signedIn) {
    return "Log in to choose";
  }

  if (
    plan.code ===
    currentPlan
  ) {
    return "Current plan";
  }

  if (
    isUpgrade(
      currentPlan,
      plan.code,
    )
  ) {
    return `Choose ${plan.name}`;
  }

  return `Downgrade to ${plan.name}`;
}

export default function PricingPage() {
  const {
    account,
    status,
    openAuth,
    refresh,
  } = useAccount();

  const [
    summary,
    setSummary,
  ] =
    useState<
      AccountSummary | null
    >(null);

  const [
    loadingSummary,
    setLoadingSummary,
  ] =
    useState(false);

  const [
    actionPlan,
    setActionPlan,
  ] =
    useState<
      AccountPlan | null
    >(null);

  const [
    message,
    setMessage,
  ] =
    useState("");

  const [
    error,
    setError,
  ] =
    useState("");

  useEffect(() => {
    if (
      status !==
      "signed-in"
    ) {
      setSummary(null);
      return;
    }

    let cancelled = false;

    setLoadingSummary(true);

    void (
      async () => {
        try {
          const result =
            await getAccountSummary();

          if (!cancelled) {
            setSummary(result);
            setError("");
          }
        } catch (
          requestError
        ) {
          if (!cancelled) {
            setError(
              getApiErrorMessage(
                requestError,
              ),
            );
          }
        } finally {
          if (!cancelled) {
            setLoadingSummary(
              false,
            );
          }
        }
      }
    )();

    return () => {
      cancelled = true;
    };
  }, [status]);

  const currentPlan:
    AccountPlan =
    summary?.currentPlan ??
    account?.plan ??
    "basic";

  const current =
    useMemo(
      () =>
        PLAN_CATALOG.find(
          (plan) =>
            plan.code ===
            currentPlan,
        ) ??
        PLAN_CATALOG[0],
      [currentPlan],
    );

  const handlePlan =
    async (
      targetPlan:
        AccountPlan,
    ) => {
      setMessage("");
      setError("");

      if (
        status !==
          "signed-in" ||
        !account
      ) {
        openAuth("login");
        return;
      }

      if (
        targetPlan ===
        currentPlan
      ) {
        return;
      }

      setActionPlan(
        targetPlan,
      );

      try {
        const result =
          await requestSubscriptionChange(
            targetPlan,
          );

        if (
          result.checkoutUrl
        ) {
          window.location.assign(
            result.checkoutUrl,
          );
          return;
        }

        await refresh();

        const nextSummary =
          await getAccountSummary();

        setSummary(
          nextSummary,
        );

        if (
          result.status ===
          "trial_started"
        ) {
          setMessage(
            result.trialEndsAt
              ? `Your 14-day trial has started and runs until ${new Date(
                  result.trialEndsAt,
                ).toLocaleDateString()}.`
              : "Your 14-day trial has started.",
          );
        } else if (
          result.status ===
          "scheduled"
        ) {
          setMessage(
            "Your plan change has been scheduled.",
          );
        } else {
          setMessage(
            `Your BIMAP plan is now ${PLAN_CATALOG.find(
              (plan) =>
                plan.code ===
                result.currentPlan,
            )?.name ?? result.currentPlan}.`,
          );
        }
      } catch (
        requestError
      ) {
        setError(
          getApiErrorMessage(
            requestError,
          ),
        );
      } finally {
        setActionPlan(null);
      }
    };

  return (
    <SiteShell
      toc={toc}
      pageDescription=
        "Compare BIMAP plans, recurring usage limits, discounts and free-trial conditions."
    >
      <main
        className={
          styles.page
        }
      >
        <section
          className={
            styles.hero
          }
          id="plans-overview"
        >
          <div className="content-width">
            <p className="eyebrow">
              <span
                aria-hidden="true"
              >
                ●
              </span>

              Subscription & usage
            </p>

            <div
              className={
                styles.heroGrid
              }
            >
              <div>
                <h1>
                  Choose your
                  BIMAP plan.
                </h1>

                <p
                  className={
                    styles.heroCopy
                  }
                >
                  Compare recurring
                  audit, conversion
                  and data-extraction
                  capacity together
                  with each plan&apos;s
                  purchase-discount
                  limits.
                </p>
              </div>

              <aside
                className={
                  styles.currentPanel
                }
                aria-label=
                  "Current BIMAP plan"
              >
                <span>
                  Current plan
                </span>

                <strong>
                  {
                    current.name
                  }
                </strong>

                {status ===
                "signed-in" ? (
                  <small>
                    {
                      loadingSummary
                        ? "Refreshing account usage..."
                        : "Linked to your BIMAP account."
                    }
                  </small>
                ) : (
                  <button
                    type="button"
                    onClick={() =>
                      openAuth(
                        "login",
                      )
                    }
                  >
                    Log in
                    <span
                      aria-hidden="true"
                    >
                      ↗
                    </span>
                  </button>
                )}
              </aside>
            </div>

            {error ? (
              <div
                className={
                  styles.error
                }
                role="alert"
              >
                {error}
              </div>
            ) : null}

            {message ? (
              <div
                className={
                  styles.success
                }
                role="status"
              >
                {message}
              </div>
            ) : null}
          </div>
        </section>

        <section
          className={
            styles.plansSection
          }
          id="plans"
        >
          <div className="content-width">
            <div
              className={
                styles.sectionHeading
              }
            >
              <div>
                <p className="eyebrow">
                  <span
                    aria-hidden="true"
                  >
                    ●
                  </span>

                  Account tiers
                </p>

                <h2>
                  Four levels.
                  One workflow.
                </h2>
              </div>

              <p>
                Paid plan prices are
                intentionally not
                invented here. BIMAP
                should display the
                configured subscription
                price only after the
                billing catalog is
                authoritative.
              </p>
            </div>

            <div
              className={
                styles.planGrid
              }
            >
              {PLAN_CATALOG.map(
                (plan) => {
                  const active =
                    status ===
                      "signed-in" &&
                    plan.code ===
                      currentPlan;

                  const upgrade =
                    status ===
                      "signed-in" &&
                    isUpgrade(
                      currentPlan,
                      plan.code,
                    );

                  const busy =
                    actionPlan ===
                    plan.code;

                  return (
                    <article
                      className={
                        styles.planCard
                      }
                      data-active={
                        active
                      }
                      key={
                        plan.code
                      }
                    >
                      <div
                        className={
                          styles.planCardTop
                        }
                      >
                        <span>
                          {
                            plan.name
                          }
                        </span>

                        {active ? (
                          <strong>
                            Current
                          </strong>
                        ) : upgrade ? (
                          <strong>
                            Trial may
                            apply
                          </strong>
                        ) : null}
                      </div>

                      <div
                        className={
                          styles.discount
                        }
                      >
                        <strong>
                          {
                            plan.baseDiscount
                          }
                          %
                        </strong>

                        <span>
                          Base purchase
                          discount
                        </span>
                      </div>

                      <p
                        className={
                          styles.planDescription
                        }
                      >
                        {
                          plan.description
                        }
                      </p>

                      <dl
                        className={
                          styles.planMetrics
                        }
                      >
                        <div>
                          <dt>
                            Audits
                          </dt>
                          <dd>
                            {formatQuota(
                              plan
                                .quota
                                .audit,
                              "month",
                            )}
                          </dd>
                        </div>

                        <div>
                          <dt>
                            Conversions
                          </dt>
                          <dd>
                            {formatQuota(
                              plan
                                .quota
                                .conversion,
                              "week",
                            )}
                          </dd>
                        </div>

                        <div>
                          <dt>
                            Data
                            extraction
                          </dt>
                          <dd>
                            {formatQuota(
                              plan
                                .quota
                                .dataExtraction,
                              "week",
                            )}
                          </dd>
                        </div>

                        <div>
                          <dt>
                            Max. combined
                            discount
                          </dt>
                          <dd>
                            {
                              plan.maxDiscount
                            }
                            %
                          </dd>
                        </div>
                      </dl>

                      <div
                        className={
                          styles.planAction
                        }
                      >
                        <button
                          type="button"
                          disabled={
                            active ||
                            busy
                          }
                          onClick={() => {
                            void handlePlan(
                              plan.code,
                            );
                          }}
                        >
                          {busy
                            ? "Processing..."
                            : actionLabel(
                                plan,
                                currentPlan,
                                status ===
                                  "signed-in",
                              )}

                          {!active ? (
                            <span
                              aria-hidden="true"
                            >
                              ↗
                            </span>
                          ) : null}
                        </button>

                        {upgrade ? (
                          <small>
                            Trial eligibility
                            is verified by the
                            BIMAP backend before
                            entitlement changes.
                          </small>
                        ) : (
                          <small>
                            {active
                              ? "This tier currently owns your recurring account entitlements."
                              : isDowngrade(
                                    currentPlan,
                                    plan.code,
                                  )
                                ? "A lower-tier change is treated as a downgrade, not a trial."
                                : "Sign in to validate the change against your account."}
                          </small>
                        )}
                      </div>
                    </article>
                  );
                },
              )}
            </div>
          </div>
        </section>

        <section
          className={
            styles.trialSection
          }
          id="trial-policy"
        >
          <div className="content-width">
            <div
              className={
                styles.trialGrid
              }
            >
              <div>
                <p className="eyebrow">
                  <span
                    aria-hidden="true"
                  >
                    ●
                  </span>

                  14-day free trial
                </p>

                <h2>
                  Trial eligibility
                  follows plan history.
                </h2>
              </div>

              <div
                className={
                  styles.trialRules
                }
              >
                <article>
                  <span>
                    01
                  </span>

                  <div>
                    <h3>
                      Higher tier only
                    </h3>

                    <p>
                      A trial is available
                      only when the target
                      tier is above the
                      user&apos;s current
                      tier.
                    </p>
                  </div>
                </article>

                <article>
                  <span>
                    02
                  </span>

                  <div>
                    <h3>
                      First use
                    </h3>

                    <p>
                      The first move into
                      an eligible higher
                      tier receives a
                      14-day free trial.
                    </p>
                  </div>
                </article>

                <article>
                  <span>
                    03
                  </span>

                  <div>
                    <h3>
                      One-year
                      requalification
                    </h3>

                    <p>
                      After leaving a tier,
                      the user can receive
                      another trial for
                      that tier once at
                      least one calendar
                      year has passed.
                    </p>
                  </div>
                </article>
              </div>
            </div>
          </div>
        </section>

        <section
          className={
            styles.comparisonSection
          }
          id="plan-comparison"
        >
          <div className="content-width">
            <div
              className={
                styles.sectionHeading
              }
            >
              <div>
                <p className="eyebrow">
                  <span
                    aria-hidden="true"
                  >
                    ●
                  </span>

                  Comparison
                </p>

                <h2>
                  Usage at a glance.
                </h2>
              </div>
            </div>

            <div
              className={
                styles.tableWrap
              }
            >
              <table
                className={
                  styles.comparisonTable
                }
              >
                <thead>
                  <tr>
                    <th>
                      Entitlement
                    </th>

                    {PLAN_CATALOG.map(
                      (plan) => (
                        <th
                          key={
                            plan.code
                          }
                          data-active={
                            status ===
                              "signed-in" &&
                            plan.code ===
                              currentPlan
                          }
                        >
                          {
                            plan.name
                          }
                        </th>
                      ),
                    )}
                  </tr>
                </thead>

                <tbody>
                  <tr>
                    <th>
                      Audits
                    </th>
                    {PLAN_CATALOG.map(
                      (plan) => (
                        <td
                          key={
                            plan.code
                          }
                        >
                          {formatQuota(
                            plan
                              .quota
                              .audit,
                            "month",
                          )}
                        </td>
                      ),
                    )}
                  </tr>

                  <tr>
                    <th>
                      Model conversions
                    </th>
                    {PLAN_CATALOG.map(
                      (plan) => (
                        <td
                          key={
                            plan.code
                          }
                        >
                          {formatQuota(
                            plan
                              .quota
                              .conversion,
                            "week",
                          )}
                        </td>
                      ),
                    )}
                  </tr>

                  <tr>
                    <th>
                      Data extractions
                    </th>
                    {PLAN_CATALOG.map(
                      (plan) => (
                        <td
                          key={
                            plan.code
                          }
                        >
                          {formatQuota(
                            plan
                              .quota
                              .dataExtraction,
                            "week",
                          )}
                        </td>
                      ),
                    )}
                  </tr>

                  <tr>
                    <th>
                      Base purchase
                      discount
                    </th>
                    {PLAN_CATALOG.map(
                      (plan) => (
                        <td
                          key={
                            plan.code
                          }
                        >
                          {
                            plan.baseDiscount
                          }
                          %
                        </td>
                      ),
                    )}
                  </tr>

                  <tr>
                    <th>
                      Maximum combined
                      discount
                    </th>
                    {PLAN_CATALOG.map(
                      (plan) => (
                        <td
                          key={
                            plan.code
                          }
                        >
                          {
                            plan.maxDiscount
                          }
                          %
                        </td>
                      ),
                    )}
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </main>
    </SiteShell>
  );
}
