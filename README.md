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
└── applications/
    └── bimap/
        ├── __init__.py
        ├── __main__.py
        ├── bootstrap.py
        ├── version.py
        └── contracts/
            ├── __init__.py
            ├── README.md
            │
            ├── versions.py
            ├── evidence.py
            ├── family_evidence.py
            ├── project_evidence.py
            ├── requirement.py
            ├── finding.py
            ├── order.py
            ├── audit_job.py
            ├── report_manifest.py
            ├── schema_export.py
            │
            ├── schema/
            │   ├── README.md
            │   └── generated/                 # generated; created by schema exporter
            │       ├── evidence-v1.0.0.schema.json
            │       ├── family_evidence-v1.0.0.schema.json
            │       ├── project_evidence-v1.0.0.schema.json
            │       ├── requirement-v1.0.0.schema.json
            │       ├── finding-v1.0.0.schema.json
            │       ├── order-v1.0.0.schema.json
            │       ├── audit_job-v1.0.0.schema.json
            │       └── report_manifest-v1.0.0.schema.json
            │
            └── utils/
                ├── __init__.py
                ├── contracts_errors.py
                └── contracts_helpers.py
```
