import type {
  SLAIAgentOutputDto,
  SLAIMappedResultDto,
} from "@/lib/audit-api";


type Props = {
  result:
    | SLAIMappedResultDto
    | null;
};


const USER_ANALYSIS_AGENTS =
  new Set([
    "collaborative",
    "knowledge",
    "reasoning",
    "planning",
    "language",
    "observability",
  ]);


function renderPayload(
  value: unknown,
): string {
  if (
    value === null
    || value === undefined
  ) {
    return "No output.";
  }

  if (typeof value === "string") {
    return value;
  }

  try {
    return JSON.stringify(
      value,
      null,
      2,
    );
  } catch {
    return String(value);
  }
}


function labelForAgent(
  agent: string,
): string {
  switch (
    agent.toLowerCase()
  ) {
    case "collaborative":
      return "Coordination assessment";

    case "knowledge":
      return "Reference knowledge";

    case "reasoning":
      return "Reasoning analysis";

    case "planning":
      return "Remediation planning";

    case "language":
      return "Explanation";

    case "observability":
      return "SLAI diagnostics";

    default:
      return agent;
  }
}


function AgentOutput({
  output,
}: {
  output: SLAIAgentOutputDto;
}) {
  return (
    <article
      data-agent={output.agent}
      data-phase={output.phase}
    >
      <header>
        <strong>
          {labelForAgent(
            output.agent,
          )}
        </strong>

        <span>
          {" "}
          — {output.phase}
        </span>
      </header>

      {!output.succeeded ? (
        <p role="status">
          This SLAI analysis step did
          not complete successfully.
        </p>
      ) : output.serializable ? (
        <pre>
          {renderPayload(
            output.payload,
          )}
        </pre>
      ) : (
        <p>
          This output is intentionally
          unavailable because it could
          not be represented safely in
          the BIMAP workspace contract.
        </p>
      )}

      {output.note ? (
        <p>
          {output.note}
        </p>
      ) : null}
    </article>
  );
}


export function SLAIInsights({
  result,
}: Props) {
  if (!result) {
    return null;
  }

  const outputs =
    result.agent_outputs.filter(
      (output) =>
        USER_ANALYSIS_AGENTS.has(
          output.agent.toLowerCase(),
        ),
    );

  return (
    <section
      aria-labelledby="slai-analysis-title"
      data-slai-release-allowed={
        result.automatic_release_allowed
      }
    >
      <header>
        <h2 id="slai-analysis-title">
          SLAI analysis
        </h2>

        <p>
          Supplemental interpretation of
          the deterministic BIM audit.
          SLAI does not replace or modify
          the authoritative findings.
        </p>
      </header>

      <dl>
        <div>
          <dt>Release assessment</dt>
          <dd>
            {result.gate_blocked
              ? "Blocked"
              : result.gate_review_required
                ? "Review required"
                : result.automatic_release_allowed
                  ? "Clear"
                  : "Not cleared"}
          </dd>
        </div>

        <div>
          <dt>Correlation ID</dt>
          <dd>
            {result.correlation_id}
          </dd>
        </div>

        <div>
          <dt>SLAI completed</dt>
          <dd>
            {new Date(
              result.completed_at,
            ).toLocaleString()}
          </dd>
        </div>
      </dl>

      {result.terminated_early ? (
        <aside role="status">
          SLAI processing terminated
          early
          {result.termination_reason
            ? `: ${result.termination_reason}`
            : "."}
        </aside>
      ) : null}

      <section
        aria-labelledby="slai-governance-title"
      >
        <h3 id="slai-governance-title">
          Governance
        </h3>

        {result.governance_gates.map(
          (gate) => (
            <div key={gate.gate}>
              <strong>
                {gate.gate}
              </strong>

              <span>
                {" "}
                — {gate.disposition}
              </span>

              {gate.reason_codes.length
              > 0 ? (
                <ul>
                  {gate.reason_codes.map(
                    (reason) => (
                      <li key={reason}>
                        {reason}
                      </li>
                    ),
                  )}
                </ul>
              ) : null}
            </div>
          ),
        )}
      </section>

      {outputs.length > 0 ? (
        <section
          aria-labelledby="slai-output-title"
        >
          <h3 id="slai-output-title">
            Supplemental intelligence
          </h3>

          {outputs.map(
            (output, index) => (
              <AgentOutput
                key={
                  `${output.phase}:`
                  + `${output.agent}:`
                  + `${index}`
                }
                output={output}
              />
            ),
          )}
        </section>
      ) : (
        <p>
          No supplemental SLAI analysis
          is available for this audit.
        </p>
      )}

      {result.mapping_warnings.length
      > 0 ? (
        <section
          aria-labelledby="slai-warning-title"
        >
          <h3 id="slai-warning-title">
            Integration warnings
          </h3>

          <ul>
            {result.mapping_warnings.map(
              (warning) => (
                <li key={warning}>
                  {warning}
                </li>
              ),
            )}
          </ul>
        </section>
      ) : null}
    </section>
  );
}
