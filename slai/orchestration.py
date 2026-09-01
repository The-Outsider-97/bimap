"""
The actual bridge into the SLAI repository.
SlaiOrchestrator
│
├── SharedMemory
│
├── AgentFactory
│
└── selected SLAI agents
"""

from __future__ import annotations

import yaml

from .utils.slai_errors import *
from .utils.slai_helpers import *
from .agent_policy import *
from .job_envelope import *
from src.agents.agent_factory import AgentFactory
from src.agents.collaborative.shared_memory import SharedMemory
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("SLAI Orchestrator")
printer = PrettyPrinter()


class SLAIOrchestrator:
    def __init__(self) -> None:
        self.shared_memory = SharedMemory()
        self.factory = AgentFactory()
        
        self.collaborative = self.factory.get_agent("collaborative", shared_memory=self.shared_memory) # Coordinate analysis workflow and shared state
        self.evaluation = self.factory.get_agent("evaluation", shared_memory=self.shared_memory) # Evaluate evidence quality, completeness, and relevance. Report completeness and calibrated performance metrics
        self.reader = self.factory.get_agent("reader", shared_memory=self.shared_memory) #  Parse PDF/CSV/XLSX/text packages and recovery
        self.knowledge = self.factory.get_agent("knowledge", shared_memory=self.shared_memory) # Organization/project standards and evidence context
        self.language = self.factory.get_agent("language", shared_memory=self.shared_memory) # Customer-facing explanations and summaries
        self.observability = self.factory.get_agent("observability", shared_memory=self.shared_memory) # SLAI runtime health, performance, traces, failure evidence, and observability 
        self.planning = self.factory.get_agent("planning", shared_memory=self.shared_memory) # Analysis sequence and remediation ordering
        self.privacy = self.factory.get_agent("privacy", shared_memory=self.shared_memory) # PII classification, minimization, retention/audit decisions
        self.quality = self.factory.get_agent("quality", shared_memory=self.shared_memory) # Input/output structural, statistical, semantic gate
        self.reasoning = self.factory.get_agent("reasoning", shared_memory=self.shared_memory) # Cross-evidence reasoning and issue relationships
        self.safety = self.factory.get_agent("safety", shared_memory=self.shared_memory) # Safety and compliance checks. High-risk workflow gate and unsupported-action protection

        self.agent_policy = SLAIAgentPolicy()
        self.job_envelope = SLAIJobEnvelope()

        self.collaborative.initialize()
        self.evaluation.initialize()
        self.reader.initialize()
        self.knowledge.initialize()
        self.language.initialize()
        self.observability.initialize()
        self.planning.initialize()
        self.privacy.initialize()
        self.quality.initialize()
        self.reasoning.initialize()
        self.safety.initialize()
        logger(f"SLAI Orchestrator successfully initialized")

    def orchestrate(self, job_envelope: SLAIJobEnvelope) -> None:
        """
        Orchestrates the execution of SLAI agents based on the provided job envelope.
        """
        logger(f"Orchestrating job with ID: {job_envelope.job_id}")
        # Implement the orchestration logic here
        # This may involve invoking various SLAI agents based on the job envelope
        pass

__all__ = ["SLAIOrchestrator"]
