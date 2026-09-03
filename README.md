# bimap
BIMAP is an independent BIM quality intelligence service for Revit families and project deliverables. It transforms BIM checking results and Revit-family data into evidence-backed, prioritized quality decisions.


Clone https://github.com/The-Outsider-97/bimap.git into SLAI/applications/, and move bimap.py to the SLAI root directory. The directory will therefore look something like:

```txt
SLAI/
├── __init__.py
├── main.py
├── bimap.py
├── README.md
├── requirements.txt
├── checkpointing/
├── data/
├── deployment/
├── logs/
├── modules/
├── monitoring/
├── src/
│   ├── agents/
│   ├── functions/
│   ├── tuning/
│   └── utils/
│
└── applications/
    │
    └── bimap/
        ├── __init__.py
        ├── __main__.py
        ├── bootstrap.py
        ├── version.py
        │
        ├── audit_engine/
        │   ├── __init__.py
        │   ├── README.md
        │   ├── context.py
        │   ├── engine.py
        │   ├── result.py
        │   │
        │   ├── ingestion/
        │   │   ├── __init__.py
        │   │   ├── dispatcher.py
        │   │   ├── manifest.py
        │   └── project_evidence.py
        │   │
        │   ├── normalization/
        │   │   ├── __init__.py
        │   │   ├── evidence_normalizer.py
        │   │   ├── family_normalizer.py
        │   │   └── schema_export.py
        │   │
        │   ├── rules/
        │   │   ├── __init__.py
        │   │   ├── base.py
        │   │   ├── executor.py
        │   │   ├── registry.py
        │   │   └── versions.py
        │   │
        │   ├── rfa/
        │   │   ├── __init__.py
        │   │   └── auditor.py
        │   │
        │   ├── bim_qa/
        │   │   ├── __init__.py
        │   │   ├── auditor.py
        │   │   └── requirement_matrix.py
        │   │
        │   ├── combined/
        │   │   ├── __init__.py
        │   │   ├── auditor.py
        │   │   ├── evidence_graph.py
        │   │   └── versions.py
        │   │
        │   ├── validation/
        │   │   ├── __init__.py
        │   │   ├── coverage.py
        │   │   ├── evidence.py
        │   │   └── findings.py
        │   │
        │   └── utils/
        │       ├── __init__.py
        │       ├── engine_errors.py
        │       └── engine_helpers.py
        │
        ├── contracts/
        │   ├── __init__.py
        │   ├── README.md
        │   │
        │   ├── versions.py
        │   ├── evidence.py
        │   ├── family_evidence.py
        │   ├── project_evidence.py
        │   ├── requirement.py
        │   ├── finding.py
        │   ├── order.py
        │   ├── audit_job.py
        │   ├── report_manifest.py
        │   ├── schema_export.py
        │   │
        │   ├── schema/
        │   │   ├── README.md
        │   │   └── generated/                 # generated; created by schema exporter
        │   │       ├── evidence-v1.0.0.schema.json
        │   │       ├── family_evidence-v1.0.0.schema.json
        │   │       ├── project_evidence-v1.0.0.schema.json
        │   │       ├── requirement-v1.0.0.schema.json
        │   │       ├── finding-v1.0.0.schema.json
        │   │       ├── order-v1.0.0.schema.json
        │   │       ├── audit_job-v1.0.0.schema.json
        │   │       └── report_manifest-v1.0.0.schema.json
        │   │
        │   └── utils/
        │       ├── __init__.py
        │       ├── contracts_errors.py
        │       └── contracts_helpers.py
        │
        ├── domain/
        │   ├── __init__.py
        │   │
        │   ├── evidence/
        │   │   ├── __init__.py
        │   │   ├── provenance.py
        │   │   ├── models.py
        │   │   └── project_evidence.py
        │   │
        │   ├── findings/
        │   │   ├── __init__.py
        │   │   ├── severity.py
        │   │   ├── confidence.py
        │   │   ├── models.py
        │   │   └── schema_export.py        # legacy/empty placeholder; do not extend
        │   │
        │   ├── governance/
        │   │   ├── __init__.py
        │   │   ├── decisions.py
        │   │   └── review.py
        │   │
        │   ├── orders/
        │   │   ├── __init__.py
        │   │   ├── states.py
        │   │   ├── events.py
        │   │   ├── models.py
        │   │   └── transitions.py
        │   │
        │   ├── products/
        │   │   ├── __init__.py
        │   │   ├── models.py
        │   │   └── limits.py
        │   │
        │   ├── reports/
        │   │   ├── __init__.py
        │   │   └── coverage.py
        │   │
        │   ├── requirements/
        │   │   ├── __init__.py
        │   │   └── models.py
        │   │
        │   └── utils/
        │       ├── __init__.py
        │       ├── domain_errors.py
        │       └── domain_helpers.py
        │
        ├── reporting/
        │   ├── __init__.py
        │   ├── README.md
        │   │
        │   ├── artifact_manifest.py
        │   ├── package_builder.py
        │   ├── report_builder.py
        │   │
        │   ├── serializers/
        │   │   ├── __init__.py
        │   │   ├── evidence_manifest.py
        │   │   ├── findings_json.py
        │   │   ├── remediation_csv.py
        │   │   └── requirement_matrix.py
        │   │
        │   ├── templates/
        │   │   └── README.md
        │   │
        │   └── utils/
        │       ├── __init__.py
        │       ├── reporting_errors.py
        │       └── reporting_helpers.py
        │
        ├── slai/
        │   ├── __init__.py
        │   ├── README.md
        │   │
        │   ├── adapter.py
        │   ├── agent_policy.py
        │   ├── governance.py
        │   ├── health.py
        │   ├── job_envelope.py
        │   ├── orchestration.py
        │   ├── result_mapper.py
        │   │
        │   └── utils/
        │       ├── __all__.py
        │       ├── slai_errors.py
        │       └── slai_helpers.py
```

# Canonical BIMAP dependency hierarchy
```txt
LEVEL 8
┌─────────────────────────────────────────┐
│ __main__.py                             │
└─────────────────────┬───────────────────┘
                      ↓
LEVEL 7
┌─────────────────────────────────────────┐
│ bootstrap.py                            │
│ composition root                        │
└────────┬──────────┬──────────┬──────────┘
         ↓          ↓          ↓
LEVEL 6
┌────────────┐ ┌───────────┐ ┌──────────────┐
│ api/       │ │ workers/  │ │ concrete     │
│            │ │           │ │ adapters     │
└─────┬──────┘ └─────┬─────┘ └───────┬──────┘
      ↓              ↓               │
LEVEL 5                              │
┌────────────────────────────────────▼─────┐
│ app/ commands / queries / services       │
└───────────┬─────────────────┬────────────┘
            ↓                 ↓
LEVEL 4
┌──────────────────┐    ┌──────────────────┐
│ audit_engine/    │    │ app/ports/       │
└────────┬─────────┘    └──────────────────┘
         ↓
LEVEL 3
┌──────────────────┐    ┌──────────────────┐
│ slai/ adapter    │    │ reporting/       │
└────────┬─────────┘    └─────────┬────────┘
         │                        │
         └───────────┬────────────┘
                     ↓
LEVEL 2
┌─────────────────────────────────────────┐
│ contracts/                              │
└─────────────────────┬───────────────────┘
                      ↓
LEVEL 1
┌─────────────────────────────────────────┐
│ domain/                                 │
│ core BIMAP business concepts            │
└─────────────────────────────────────────┘
```

# Exact file-level import hierarchy for SLAI integration
```txt
bimap/bootstrap.py
│
├── imports bimap/audit_engine/engine.py
├── imports bimap/app/services/audit_service.py
└── imports bimap/slai/adapter.py
                    │
                    ▼
bimap/slai/adapter.py
│
├── imports bimap/app/ports/slai.py
├── imports bimap/slai/orchestration.py
├── imports bimap/slai/job_envelope.py
├── imports bimap/slai/result_mapper.py
├── imports bimap/slai/governance.py
└── imports bimap/slai/health.py
                    │
                    ▼
bimap/slai/orchestration.py
│
├── imports bimap/slai/agent_policy.py
│
├── imports src/agents/agent_factory.py
│       └── AgentFactory
│
└── imports src/agents/collaborative/shared_memory.py
        └── SharedMemory
                    │
                    ▼
              AgentFactory
                    │
     ┌──────────────┼─────────────────────────┐
     │              │                         │
     ▼              ▼                         ▼
Collaborative     Reader                   Knowledge
     │              │                         │
     ├──────────────┼─────────────────────────┤
     ▼              ▼                         ▼
 Reasoning       Planning                   Quality
     │              │                         │
     ├──────────────┼─────────────────────────┤
     ▼              ▼                         ▼
  Privacy        Safety                   Evaluation
     │              │                         │
     └──────────────┼─────────────────────────┘
                    ▼
                 Language
                    │
                    ▼
              Observability
```

# The entire BIMAP + SLAI execution architecture
```txt
                         CUSTOMER
                            │
                            ▼
                     BIMAP frontend
                            │
                            ▼
                       BIMAP API
                            │
                            ▼
                       AuditService
                            │
              ┌─────────────┴──────────────┐
              │                            │
              ▼                            │
        BIMAP AuditEngine                  │
              │                            │
      deterministic rules                  │
              │                            │
              ▼                            │
      deterministic findings               │
              │                            │
              └─────────────┐              │
                            ▼              │
                     SlaiJobEnvelope       │
                            │              │
                            ▼              │
                       SlaiAdapter ◄───────┘
                            │
                            ▼
                     SlaiOrchestrator
                            │
                  ┌─────────┴─────────┐
                  │                   │
                  ▼                   ▼
           AgentFactory          SharedMemory
                  │                   │
                  └─────────┬─────────┘
                            │
         ┌──────────────────┼────────────────────┐
         │                  │                    │
         ▼                  ▼                    ▼
     Quality            Privacy           Collaborative
      ingress            ingress
         │                  │
         └────────┬─────────┘
                  ▼
                Reader
                  │
                  ▼
              Knowledge
                  │
                  ▼
              Reasoning
                  │
                  ▼
               Planning
                  │
                  ▼
            Collaborative
            coordination
                  │
        ┌─────────┼──────────┐
        ▼         ▼          ▼
     Quality  Evaluation   Safety
        │         │          │
        └─────────┼──────────┘
                  ▼
               Privacy
                  │
                  ▼
               Language
                  │
                  ▼
            Result Mapper
                  │
                  ▼
          Governance Mapper
                  │
                  ▼
             SlaiAdapter
                  │
                  ▼
             AuditService
                  │
                  ▼
          Final BIMAP Result
                  │
                  ▼
              Reporting
                  │
                  ▼
        PDF / JSON / CSV / ZIP
```
