"""Persistence-agnostic domain models (Pydantic v2).

API contracts here are database-agnostic: stable UUID string ids, ISO-8601
timestamps, explicit foreign-key style reference fields (never embedded blobs
for related entities). Mongo's internal `_id` is never surfaced.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Capability provenance for model capability declarations. Never fabricated:
# PROVIDER = last written by provider-catalog discovery/refresh; MANUAL = set
# explicitly by the user via PATCH /models/{id}; VERIFIED = confirmed by a
# successful live capability probe; UNKNOWN = no provenance recorded (legacy
# records and manual creates without an explicit source).
CapabilitySource = Literal["PROVIDER", "VERIFIED", "MANUAL", "UNKNOWN"]
CAPABILITY_SOURCES = ("PROVIDER", "VERIFIED", "MANUAL", "UNKNOWN")

# Capability keys the manual override path accepts. Unknown keys are ignored
# (never stored) so a typo cannot fabricate a new capability dimension.
MANUAL_CAPABILITY_KEYS = ("tools", "vision", "streaming", "text", "structured_output")


class ORMModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------- users
class UserPublic(ORMModel):
    id: str
    email: EmailStr
    name: str
    role: str = "researcher"
    created_at: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    name: str = Field(min_length=1)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ---------------------------------------------------------------- projects
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    tags: List[str] = []
    default_model_id: Optional[str] = None
    default_system_instruction: str = ""
    default_temperature: Optional[float] = None
    default_max_tokens: Optional[int] = None
    default_top_p: Optional[float] = None
    default_streaming: Optional[bool] = None
    default_context_strategy: str = "FULL"
    default_context_options: Dict[str, Any] = {}
    default_enabled_tool_ids: List[str] = []
    default_knowledge_enabled: Optional[bool] = None
    default_knowledge_collection_ids: List[str] = []
    default_retrieval_options: Dict[str, Any] = {}
    default_search_enabled: bool = False
    default_search_provider_id: Optional[str] = None
    default_fetch_enabled: bool = False
    default_fetch_provider_id: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None  # active | archived
    default_model_id: Optional[str] = None
    default_system_instruction: Optional[str] = None
    default_temperature: Optional[float] = None
    default_max_tokens: Optional[int] = None
    default_top_p: Optional[float] = None
    default_streaming: Optional[bool] = None
    default_context_strategy: Optional[str] = None
    default_context_options: Optional[Dict[str, Any]] = None
    default_enabled_tool_ids: Optional[List[str]] = None
    default_knowledge_enabled: Optional[bool] = None
    default_knowledge_collection_ids: Optional[List[str]] = None
    default_retrieval_options: Optional[Dict[str, Any]] = None
    default_search_enabled: Optional[bool] = None
    default_search_provider_id: Optional[str] = None
    default_fetch_enabled: Optional[bool] = None
    default_fetch_provider_id: Optional[str] = None


class Project(ORMModel):
    id: str = Field(default_factory=new_id)
    owner_id: str
    name: str
    description: str = ""
    tags: List[str] = []
    status: str = "active"
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    default_model_id: Optional[str] = None
    default_system_instruction: str = ""
    default_temperature: Optional[float] = None
    default_max_tokens: Optional[int] = None
    default_top_p: Optional[float] = None
    default_streaming: Optional[bool] = None
    default_context_strategy: str = "FULL"
    default_context_options: Dict[str, Any] = {}
    default_enabled_tool_ids: List[str] = []
    default_knowledge_enabled: Optional[bool] = None
    default_knowledge_collection_ids: List[str] = []
    default_retrieval_options: Dict[str, Any] = {}
    default_search_enabled: bool = False
    default_search_provider_id: Optional[str] = None
    default_fetch_enabled: bool = False
    default_fetch_provider_id: Optional[str] = None


# ---------------------------------------------------------------- providers
class ProviderCreate(BaseModel):
    name: str = Field(min_length=1)
    provider_type: str  # openai_compatible
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    enabled: bool = True
    metadata: Dict[str, Any] = {}


class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    enabled: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


class ProviderPublic(ORMModel):
    id: str
    owner_id: str
    name: str
    provider_type: str
    base_url: Optional[str] = None
    enabled: bool = True
    has_api_key: bool = False
    seeded: bool = False
    supports_model_discovery: bool = False
    capabilities: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    created_at: str
    updated_at: str


# ---------------------------------------------------------------- models
class ProviderModelCatalogItem(BaseModel):
    """Normalized, provider-neutral model metadata returned by discovery."""

    model_identifier: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    input_cost: Optional[float] = None  # USD per 1M input tokens
    output_cost: Optional[float] = None  # USD per 1M output tokens
    input_price_per_1m: Optional[float] = None
    output_price_per_1m: Optional[float] = None
    # Explicit aliases make the unit clear for new consumers while preserving
    # the existing registry names used by the frontend and seeded records.
    input_price_per_1m_tokens: Optional[float] = None
    output_price_per_1m_tokens: Optional[float] = None
    cache_read_price_per_1m: Optional[float] = None
    cache_write_price_per_1m: Optional[float] = None
    cache_creation_price_per_1m: Optional[float] = None
    reasoning_price_per_1m: Optional[float] = None
    raw_pricing: Optional[Dict[str, Any]] = None
    capabilities: Dict[str, Any] = {}
    capability_source: CapabilitySource = "UNKNOWN"
    modalities: Dict[str, List[str]] = {}
    provider_metadata: Dict[str, Any] = {}
    pricing_source: Optional[str] = None
    pricing_updated_at: Optional[str] = None

    @model_validator(mode="after")
    def sync_pricing_aliases(self):
        input_price = self.input_price_per_1m
        if input_price is None:
            input_price = self.input_price_per_1m_tokens
        if input_price is None:
            input_price = self.input_cost
        output_price = self.output_price_per_1m
        if output_price is None:
            output_price = self.output_price_per_1m_tokens
        if output_price is None:
            output_price = self.output_cost
        self.input_price_per_1m = input_price
        self.output_price_per_1m = output_price
        self.input_cost = input_price
        self.output_cost = output_price
        self.input_price_per_1m_tokens = input_price
        self.output_price_per_1m_tokens = output_price
        if self.max_output_tokens is not None:
            if 'max_output_tokens' not in self.capabilities:
                self.capabilities['max_output_tokens'] = self.max_output_tokens
        elif isinstance(self.capabilities, dict) and 'max_output_tokens' in self.capabilities:
            try:
                val = int(self.capabilities['max_output_tokens'])
                if val > 0:
                    self.max_output_tokens = val
            except (TypeError, ValueError):
                pass
        return self


class ModelCreate(BaseModel):
    @field_validator("capability_source", mode="before")
    @classmethod
    def _normalize_capability_source(cls, value):
        if value is None:
            return "UNKNOWN"
        text = str(value).strip().upper()
        return text if text in CAPABILITY_SOURCES else value

    provider_id: str
    display_name: str = Field(min_length=1)
    model_identifier: str = Field(min_length=1)
    family: Optional[str] = None  # openai | anthropic | gemini (for universal routing)
    enabled: bool = True
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    input_cost: Optional[float] = None   # USD per 1M input tokens
    output_cost: Optional[float] = None  # USD per 1M output tokens
    input_price_per_1m: Optional[float] = None
    output_price_per_1m: Optional[float] = None
    input_price_per_1m_tokens: Optional[float] = None
    output_price_per_1m_tokens: Optional[float] = None
    cache_read_price_per_1m: Optional[float] = None
    cache_write_price_per_1m: Optional[float] = None
    cache_creation_price_per_1m: Optional[float] = None
    reasoning_price_per_1m: Optional[float] = None
    raw_pricing: Optional[Dict[str, Any]] = None
    capabilities: Dict[str, Any] = {}
    capability_source: CapabilitySource = "UNKNOWN"
    modalities: Dict[str, List[str]] = {}
    provider_metadata: Dict[str, Any] = {}
    pricing_source: Optional[str] = None
    pricing_updated_at: Optional[str] = None
    tags: List[str] = []
    notes: str = ""

    @model_validator(mode="after")
    def sync_capabilities_output_budget(self):
        if self.max_output_tokens is not None:
            if 'max_output_tokens' not in self.capabilities:
                self.capabilities['max_output_tokens'] = self.max_output_tokens
        elif isinstance(self.capabilities, dict) and 'max_output_tokens' in self.capabilities:
            try:
                val = int(self.capabilities['max_output_tokens'])
                if val > 0:
                    self.max_output_tokens = val
            except (TypeError, ValueError):
                pass
        return self


class ModelUpdate(BaseModel):
    @field_validator("capability_source", mode="before")
    @classmethod
    def _normalize_capability_source(cls, value):
        if value is None:
            return None
        text = str(value).strip().upper()
        return text if text in CAPABILITY_SOURCES else value

    provider_id: Optional[str] = None
    display_name: Optional[str] = None
    model_identifier: Optional[str] = None
    family: Optional[str] = None
    enabled: Optional[bool] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    input_cost: Optional[float] = None
    output_cost: Optional[float] = None
    input_price_per_1m: Optional[float] = None
    output_price_per_1m: Optional[float] = None
    input_price_per_1m_tokens: Optional[float] = None
    output_price_per_1m_tokens: Optional[float] = None
    cache_read_price_per_1m: Optional[float] = None
    cache_write_price_per_1m: Optional[float] = None
    cache_creation_price_per_1m: Optional[float] = None
    reasoning_price_per_1m: Optional[float] = None
    raw_pricing: Optional[Dict[str, Any]] = None
    capabilities: Optional[Dict[str, Any]] = None
    capability_source: Optional[CapabilitySource] = None
    modalities: Optional[Dict[str, List[str]]] = None
    provider_metadata: Optional[Dict[str, Any]] = None
    pricing_source: Optional[str] = None
    pricing_updated_at: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def sync_capabilities_output_budget(self):
        if self.max_output_tokens is not None:
            if self.capabilities is None:
                self.capabilities = {}
            if 'max_output_tokens' not in self.capabilities:
                self.capabilities['max_output_tokens'] = self.max_output_tokens
        elif isinstance(self.capabilities, dict) and 'max_output_tokens' in self.capabilities:
            try:
                val = int(self.capabilities['max_output_tokens'])
                if val > 0:
                    self.max_output_tokens = val
            except (TypeError, ValueError):
                pass
        return self


class ModelRecord(ORMModel):
    id: str = Field(default_factory=new_id)
    owner_id: str
    provider_id: str
    display_name: str
    model_identifier: str
    family: Optional[str] = None
    enabled: bool = True
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    input_cost: Optional[float] = None
    output_cost: Optional[float] = None
    input_price_per_1m: Optional[float] = None
    output_price_per_1m: Optional[float] = None
    input_price_per_1m_tokens: Optional[float] = None
    output_price_per_1m_tokens: Optional[float] = None
    cache_read_price_per_1m: Optional[float] = None
    cache_write_price_per_1m: Optional[float] = None
    cache_creation_price_per_1m: Optional[float] = None
    reasoning_price_per_1m: Optional[float] = None
    raw_pricing: Optional[Dict[str, Any]] = None
    capabilities: Dict[str, Any] = {}
    capability_source: CapabilitySource = "UNKNOWN"
    modalities: Dict[str, List[str]] = {}
    provider_metadata: Dict[str, Any] = {}
    pricing_source: Optional[str] = None
    pricing_updated_at: Optional[str] = None
    tags: List[str] = []
    notes: str = ""
    seeded: bool = False
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @model_validator(mode="after")
    def sync_capabilities_output_budget(self):
        if self.max_output_tokens is not None:
            if 'max_output_tokens' not in self.capabilities:
                self.capabilities['max_output_tokens'] = self.max_output_tokens
        elif isinstance(self.capabilities, dict) and 'max_output_tokens' in self.capabilities:
            try:
                val = int(self.capabilities['max_output_tokens'])
                if val > 0:
                    self.max_output_tokens = val
            except (TypeError, ValueError):
                pass
        return self


# ---------------------------------------------------------------- telemetry & pricing schemas
class ReasoningTelemetry(BaseModel):
    available: bool = False
    content: Optional[str] = None
    summary: Optional[str] = None
    tokens: Optional[int] = None
    streamed: bool = False
    source: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    cache_creation_tokens: Optional[int] = None
    uncached_input_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    audio_tokens: Optional[int] = None
    image_tokens: Optional[int] = None
    raw_usage: Optional[Dict[str, Any]] = None


class CacheTelemetry(BaseModel):
    cache_supported: Optional[bool] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    cache_creation_tokens: Optional[int] = None
    uncached_input_tokens: Optional[int] = None
    cache_hit_rate: Optional[float] = None


class CostBreakdown(BaseModel):
    cost_available: bool = False
    input_cost: Optional[float] = None
    uncached_input_cost: Optional[float] = None
    cache_read_cost: Optional[float] = None
    cache_write_cost: Optional[float] = None
    cache_creation_cost: Optional[float] = None
    output_cost: Optional[float] = None
    reasoning_cost: Optional[float] = None
    total_cost: Optional[float] = None
    updated_at: str = Field(default_factory=now_iso)


# ---------------------------------------------------------------- conversations
class ConversationCreate(BaseModel):
    project_id: str
    title: str = "Untitled conversation"
    system_prompt: str = ""
    default_model_id: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    streaming: Optional[bool] = None
    enabled_tool_ids: List[str] = []
    knowledge_enabled: Optional[bool] = None
    context_strategy: Optional[str] = None
    context_options: Dict[str, Any] = {}
    knowledge_collection_ids: List[str] = []
    retrieval_options: Dict[str, Any] = {}
    search_enabled: Optional[bool] = None
    search_provider_id: Optional[str] = None
    fetch_enabled: Optional[bool] = None
    fetch_provider_id: Optional[str] = None
    workspace_mode: Optional[str] = None


class ContentBlock(BaseModel):
    type: str = "text"            # text | image | attachment_ref
    text: Optional[str] = None
    attachment_id: Optional[str] = None
    metadata: Dict[str, Any] = {}


class MessageRequest(BaseModel):
    model_id: str
    content: str = ""
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    role: str = "user"
    seed: Optional[int] = None
    streaming: Optional[bool] = None
    content_blocks: List[ContentBlock] = []
    attachment_ids: List[str] = []
    enabled_tool_ids: List[str] = []
    knowledge_enabled: Optional[bool] = None
    context_strategy: Optional[str] = None
    context_options: Dict[str, Any] = {}
    knowledge_collection_ids: List[str] = []
    retrieval_options: Dict[str, Any] = {}
    max_tool_rounds: Optional[int] = None
    search_enabled: Optional[bool] = None
    search_provider_id: Optional[str] = None
    fetch_enabled: Optional[bool] = None
    fetch_provider_id: Optional[str] = None
    workspace_mode: Optional[str] = None

    @model_validator(mode="after")
    def require_content_for_user(self):
        # A user turn must carry something: plain text, content blocks, or
        # attachment references. Assistant/system turns may legitimately be
        # empty (e.g. tool-call placeholders), so only ``role == "user"`` is
        # constrained. This keeps the legacy ``content``-only callers working.
        if (
            self.role == "user"
            and not (self.content or "").strip()
            and not self.content_blocks
            and not self.attachment_ids
        ):
            raise ValueError(
                "content, content_blocks, or attachment_ids is required for a user message"
            )
        return self


# ---------------------------------------------------------------- generation params
class GenParams(BaseModel):
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None


# ---------------------------------------------------------------- arena
class ArenaRequest(BaseModel):
    project_id: str
    title: Optional[str] = None
    task: str = Field(min_length=1)          # user prompt
    system_prompt: str = ""
    model_ids: List[str] = Field(min_length=1)
    params: GenParams = GenParams()
    blind: bool = False
    attachment_ids: List[str] = []
    knowledge_collection_ids: List[str] = []


# ---------------------------------------------------------------- rubrics
class RubricDimension(BaseModel):
    key: str
    label: str
    description: str = ""
    max_score: int = 10
    weight: float = 1.0


class RubricCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    dimensions: List[RubricDimension]
    threshold: Optional[float] = None
    scoring_scale: str = "0-1"


class RubricUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    dimensions: Optional[List[RubricDimension]] = None
    threshold: Optional[float] = None
    scoring_scale: Optional[str] = None


# ---------------------------------------------------------------- judge / evaluation
class JudgeRequest(BaseModel):
    judge_model_id: str
    rubric_id: str


class HumanEvalRequest(BaseModel):
    run_id: str
    scores: Dict[str, int] = {}
    winner: bool = False
    notes: str = ""
    # Additive: when true, an existing human winner for the experiment is
    # superseded instead of rejected with 409.
    replace: bool = False
    # Additive: advisory only; the server resolves the real blind label from the
    # server-side blind_map and never trusts a client-supplied value.
    blind_label: Optional[str] = None


# ---------------------------------------------------------------- artifacts
class ArtifactCreate(BaseModel):
    project_id: str
    title: str = Field(min_length=1)
    # research_report | architecture | code | prompt | evaluation_report | experiment_result
    # decision_memo | dataset | generic_document + legacy research_note/report/text (pass-through, no enum)
    artifact_type: str = "text"
    content: str = ""
    source: str = "manual"       # manual | workspace | arena | experiment
    source_ref: Optional[str] = None
    metadata: Dict[str, Any] = {}
    creator_name: str = ""
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    run_id: Optional[str] = None


class ArtifactUpdate(BaseModel):
    title: Optional[str] = None
    artifact_type: Optional[str] = None
    content: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    creator_name: Optional[str] = None
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    run_id: Optional[str] = None


# ---------------------------------------------------------------- council
class CouncilParticipant(BaseModel):
    role: str = Field(min_length=1)
    perspective: str = ""
    model_id: str
    display_name: str = ""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    order: int = 0
    enabled: bool = True


class CouncilRequest(BaseModel):
    project_id: str
    title: Optional[str] = None
    question: str = Field(min_length=1)
    participants: List[CouncilParticipant] = Field(min_length=2)
    moderator_model_id: str


# ---------------------------------------------------------------- research
class ResearchSource(BaseModel):
    title: str = ""
    url: str = ""
    note: str = ""


class ResearchCreate(BaseModel):
    project_id: str
    title: str = Field(min_length=1)
    research_question: str = ""
    notes: str = ""
    tags: List[str] = []


class ResearchUpdate(BaseModel):
    title: Optional[str] = None
    research_question: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None  # active | paused | complete
    sources: Optional[List[ResearchSource]] = None
    related_experiment_ids: Optional[List[str]] = None
    related_artifact_ids: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    related_conversation_ids: Optional[List[str]] = None
    related_council_ids: Optional[List[str]] = None
    related_finding_ids: Optional[List[str]] = None
    related_dataset_ids: Optional[List[str]] = None
    related_run_ids: Optional[List[str]] = None
    related_dataset_version_ids: Optional[List[str]] = None
    related_benchmark_plan_ids: Optional[List[str]] = None


# ---------------------------------------------------------------- findings
class FindingEvidence(BaseModel):
    # experiment | research | artifact | run | evaluation | council | dataset | conversation
    type: str
    ref_id: str
    label: str = ""


class FindingCreate(BaseModel):
    project_id: str
    title: str = Field(min_length=1)
    statement: str = ""
    description: str = ""
    confidence: str = "medium"       # low | medium | high
    status: str = "provisional"      # provisional | validated | superseded
    notes: str = ""
    tags: List[str] = []
    evidence: List[FindingEvidence] = []


class FindingUpdate(BaseModel):
    title: Optional[str] = None
    statement: Optional[str] = None
    description: Optional[str] = None
    confidence: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    evidence: Optional[List[FindingEvidence]] = None


# ---------------------------------------------------------------- datasets
class DatasetCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1)
    description: str = ""
    tags: List[str] = []


class DatasetUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None


class DatasetCaseCreate(BaseModel):
    dataset_id: Optional[str] = None
    input_text: str = Field(min_length=1)
    expected_output: str = ""
    metadata: Dict[str, Any] = {}
    tags: List[str] = []
    ordering: int = 0
    asset_attachment_ids: List[str] = []


class DatasetCaseUpdate(BaseModel):
    input_text: Optional[str] = None
    expected_output: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    ordering: Optional[int] = None


# ---------------------------------------------------------------- Phase 5: research quality
class DatasetAssertion(BaseModel):
    type: str
    value: Any = None
    params: Dict[str, Any] = {}
    required: bool = True
    category: str = "correctness"
    severity: str = "medium"
    message: str = ""

class ToolPolicyExpectation(BaseModel):
    allow_tools: Optional[bool] = None
    expected_tool_call_count: Optional[int] = None
    min_tool_calls: Optional[int] = None
    max_tool_calls: Optional[int] = None
    required_tools: List[str] = []
    forbidden_tools: List[str] = []
    max_failed_tool_calls: Optional[int] = None

class KnowledgePolicyExpectation(BaseModel):
    knowledge_expected: Optional[bool] = None
    expect_retrieval: Optional[bool] = None
    expect_injection: Optional[bool] = None
    require_valid_markers: Optional[bool] = None
    expected_markers: List[str] = []
    forbidden_content: List[str] = []
    require_no_knowledge: Optional[bool] = None

class RedTeamExpectation(BaseModel):
    category: str
    expected_behavior: str = "hold_boundary"
    expected_boundary: str = ""
    severity: str = "medium"
    allowed_tools: List[str] = []
    forbidden_tools: List[str] = []
    knowledge_policy: KnowledgePolicyExpectation = KnowledgePolicyExpectation()
    deterministic_assertions: List[DatasetAssertion] = []
    judge_rubric_id: Optional[str] = None
    tags: List[str] = []
    description: str = ""

class GroundingExpectation(BaseModel):
    require_citation: Optional[bool] = None
    require_web_evidence: Optional[bool] = None
    min_evidence_items: Optional[int] = None
    forbid_fabricated_markers: Optional[bool] = None

class JudgeConfig(BaseModel):
    judge_model_id: Optional[str] = None
    rubric_id: Optional[str] = None
    threshold: Optional[float] = None
    temperature: Optional[float] = None

class ExperimentEvaluationSpec(BaseModel):
    enabled: bool = False
    evaluator_type: str = "rule"                # deterministic | rule | judge_model
    evaluator_keys: List[str] = []
    rubric_id: Optional[str] = None
    judge_model_id: Optional[str] = None
    threshold: Optional[float] = None

class RegressionRequest(BaseModel):
    baseline_configuration_id: str
    candidate_configuration_id: str
    name: str = ""
    evaluator_type: str = "rule"
    evaluator_keys: List[str] = []
    rubric_id: Optional[str] = None
    judge_model_id: Optional[str] = None
    threshold: Optional[float] = None
    reevaluate: bool = False
    notes: str = ""


# ---------------------------------------------------------------- experiments (protocol)
# Canonical experiment lifecycle. Legacy Arena experiments persist lowercase
# statuses ("completed"/"draft"/"failed"); readers normalize case-insensitively.
EXPERIMENT_STATUSES = ("DRAFT", "READY", "RUNNING", "COMPLETED", "FAILED", "ARCHIVED")

# V6.8 Agent Arena & Prompt Arena. Both are ordinary protocol experiments whose
# configurations bind a frozen agent/prompt version; they reuse the existing
# benchmark runner and evaluation funnel rather than a parallel engine.
AGENT_ARENA_EXPERIMENT_TYPE = "agent_arena"
PROMPT_ARENA_EXPERIMENT_TYPE = "prompt_arena"
ARENA_EXPERIMENT_TYPES = (AGENT_ARENA_EXPERIMENT_TYPE, PROMPT_ARENA_EXPERIMENT_TYPE)


class ExperimentConfiguration(BaseModel):
    id: str = Field(default_factory=new_id)
    label: str = Field(min_length=1)
    model_id: str
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    system_prompt: str = ""
    prompt_version_id: Optional[str] = None
    # Additive (V6.8 Agent Arena): when set, this configuration is dispatched
    # through the canonical agent runtime instead of a bare model call. The
    # model binding stays fixed to the frozen agent version's model.
    agent_version_id: Optional[str] = None
    metadata: Dict[str, Any] = {}
    is_control: bool = False
    order: int = 0
    # Additive (V5): per-configuration knowledge/tool/search/fetch overrides.
    # ``enabled_tool_ids is None`` means "do not override"; ``[]`` means no tools.
    enabled_tool_ids: Optional[List[str]] = None
    knowledge_enabled: Optional[bool] = None
    knowledge_collection_ids: List[str] = []
    retrieval_options: Dict[str, Any] = {}
    search_enabled: Optional[bool] = None
    search_provider_id: Optional[str] = None
    fetch_enabled: Optional[bool] = None
    fetch_provider_id: Optional[str] = None


class ExperimentProtocol(BaseModel):
    objective: str = ""
    hypothesis: str = ""
    dataset_id: Optional[str] = None
    dataset_version_id: Optional[str] = None
    rubric_id: Optional[str] = None
    judge_model_id: Optional[str] = None
    judge_rubric_id: Optional[str] = None
    variables: List[Dict[str, Any]] = []
    constants: Dict[str, Any] = {}
    repetitions: int = Field(default=1, ge=1, le=20)
    success_criteria: List[str] = []
    max_planned_calls: Optional[int] = None
    # Additive (V5): disabled by default; existing protocols are unchanged.
    evaluation: ExperimentEvaluationSpec = ExperimentEvaluationSpec()


class ExperimentCreateRequest(BaseModel):
    project_id: str
    title: str = Field(min_length=1)
    objective: str = ""
    hypothesis: str = ""
    experiment_type: str = "benchmark"
    protocol: ExperimentProtocol = ExperimentProtocol()
    configurations: List[ExperimentConfiguration] = []


class ExperimentUpdateRequest(BaseModel):
    title: Optional[str] = None
    objective: Optional[str] = None
    hypothesis: Optional[str] = None
    status: Optional[str] = None
    protocol: Optional[ExperimentProtocol] = None
    configurations: Optional[List[ExperimentConfiguration]] = None


# ---------------------------------------------------------------- prompts
class PromptCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1)
    description: str = ""
    purpose: str = ""
    status: str = "active"


class PromptUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    purpose: Optional[str] = None
    status: Optional[str] = None


class PromptVersionCreate(BaseModel):
    content: str = Field(min_length=1)
    system_prompt: str = ""
    variables: Dict[str, Any] = {}
    notes: str = ""
    # Additive (V6.1): structured spec + ordered Prompt Block references.
    structured_spec: Dict[str, Any] = {}
    blocks: List[str] = []


class PromptVersionUpdate(BaseModel):
    content: Optional[str] = None
    system_prompt: Optional[str] = None
    variables: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    structured_spec: Optional[Dict[str, Any]] = None
    blocks: Optional[List[str]] = None


class PromptVersionActionResponse(BaseModel):
    ok: bool = True


# ---------------------------------------------------------------- Phase V6: prompt & policy foundation
# First-class project-scoped Prompt Specs (the existing prompt store), Prompt
# Blocks, Project Policies (immutable-by-version) and Project Invariants.
PROMPT_SPEC_STATUSES = ("draft", "active", "deprecated", "archived")
# Additive (V6.1): append-only Prompt Version lifecycle. There is no
# "certified" state by design; drafts become frozen snapshots, and a
# frozen version may later be deprecated.
PROMPT_VERSION_STATES = ("draft", "frozen", "deprecated")
PROMPT_BLOCK_CATEGORIES = (
    "inspect_existing_architecture",
    "bounded_scope",
    "reuse_existing_services",
    "no_parallel_system",
    "preserve_security",
    "tests_required",
    "no_false_pass",
    "migration_safety",
    "report_changes",
    "other",
)
PROMPT_BLOCK_STATUSES = ("active", "archived")
POLICY_DOMAINS = (
    "ARCHITECTURE",
    "DESIGN",
    "SECURITY",
    "DATA",
    "CODING",
    "TESTING",
    "OPERATIONS",
    "PRODUCT",
)
POLICY_STATUSES = ("active", "superseded", "archived")
INVARIANT_SEVERITIES = ("info", "low", "medium", "high", "critical")
INVARIANT_ENFORCEMENT_MODES = (
    "informational",
    "prompt_enforced",
    "evaluation_enforced",
    "blocking",
)
INVARIANT_STATUSES = ("active", "archived")

# V6.14 Workspace modes and conversation prompt capture. Modes are composition
# presets over the existing Workspace execution path; they never create a
# separate runtime.
WORKSPACE_MODES = ("chat", "plan", "research", "audit")
CAPTURE_MODES = ("response", "conversation")
CAPTURE_KINDS = ("prompt", "planning_template")


class PromptBlockCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1)
    description: str = ""
    category: str = "other"
    content: str = ""
    variables: Dict[str, Any] = {}
    status: str = "active"


class PromptBlockUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    content: Optional[str] = None
    variables: Optional[Dict[str, Any]] = None
    status: Optional[str] = None


class ProjectPolicyCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1)
    domain: str
    description: str = ""
    content: str = ""


class ProjectPolicyVersionCreate(BaseModel):
    content: str = Field(min_length=1)
    description: str = ""


class ProjectPolicyUpdate(BaseModel):
    status: Optional[str] = None


class ProjectInvariantCreate(BaseModel):
    project_id: str
    code: str = Field(min_length=1)
    domain: str
    title: str = Field(min_length=1)
    description: str = ""
    severity: str = "medium"
    enforcement_mode: str = "informational"
    status: str = "active"


class ProjectInvariantUpdate(BaseModel):
    code: Optional[str] = None
    domain: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None
    enforcement_mode: Optional[str] = None
    status: Optional[str] = None


# ---------------------------------------------------------------- dataset versioning
class DatasetVersionCreate(BaseModel):
    description: str = ""
    source_version_id: Optional[str] = None


class DatasetVersionCaseCreate(BaseModel):
    input_text: str = Field(min_length=1)
    expected_output: str = ""
    metadata: Dict[str, Any] = {}
    tags: List[str] = []
    ordering: int = 0
    evaluation_hints: Dict[str, Any] = {}
    asset_attachment_ids: List[str] = []
    # Additive (V5): quality expectations for the case.
    reference_answer: Optional[str] = None
    assertions: List[DatasetAssertion] = []
    tool_policy: ToolPolicyExpectation = ToolPolicyExpectation()
    knowledge_policy: KnowledgePolicyExpectation = KnowledgePolicyExpectation()
    grounding_expectations: GroundingExpectation = GroundingExpectation()
    red_team: Optional[RedTeamExpectation] = None


class DatasetVersionCaseUpdate(BaseModel):
    input_text: Optional[str] = None
    expected_output: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    ordering: Optional[int] = None
    evaluation_hints: Optional[Dict[str, Any]] = None
    # Additive (V5): unspecified fields are left unchanged.
    reference_answer: Optional[str] = None
    assertions: Optional[List[DatasetAssertion]] = None
    tool_policy: Optional[ToolPolicyExpectation] = None
    knowledge_policy: Optional[KnowledgePolicyExpectation] = None
    grounding_expectations: Optional[GroundingExpectation] = None
    red_team: Optional[RedTeamExpectation] = None


# ---------------------------------------------------------------- benchmark (Phase 2 B)
class BenchmarkStartRequest(BaseModel):
    plan_id: str
    confirm: bool = False
    max_calls_override: Optional[int] = None


class EvaluationRequest(BaseModel):
    run_id: str
    evaluator_type: str = "deterministic"  # deterministic | human | llm_judge
    method: Optional[str] = None           # exact_match | contains | regex | token_overlap
    rubric_id: Optional[str] = None
    judge_model_id: Optional[str] = None
    score: Optional[float] = None
    passed: Optional[bool] = None
    notes: str = ""
    # Additive (V5): canonical evaluation vocabulary.
    evaluation_type: Optional[str] = None
    evaluator_key: Optional[str] = None
    evaluator_keys: List[str] = []
    threshold: Optional[float] = None
    options: Dict[str, Any] = {}
    rubric_version: Optional[int] = None
    record_failures: bool = False


class BenchmarkEvidenceInput(BaseModel):
    run_ids: List[str] = []
    evaluation_ids: List[str] = []
    dataset_version_id: Optional[str] = None
    configuration_ids: List[str] = []


class FindingFromResultsRequest(BaseModel):
    title: str = Field(min_length=1)
    statement: str = ""
    description: str = ""
    confidence: str = "medium"          # low | medium | high
    notes: str = ""
    evidence: BenchmarkEvidenceInput = BenchmarkEvidenceInput()


# ---------------------------------------------------------------- V6.8 Arena DTOs
# Agent Arena compares frozen agent versions (each with its fixed model binding)
# across a frozen dataset. Prompt Arena compares frozen prompt versions under one
# fixed model. Both compile down to ordinary protocol experiments.
class AgentArenaCreate(BaseModel):
    project_id: str
    title: str = Field(min_length=1)
    dataset_id: str
    dataset_version_id: str
    agent_version_ids: List[str] = Field(min_length=1)
    model_ids: List[str] = []
    objective: str = ""
    hypothesis: str = ""
    repetitions: int = Field(default=1, ge=1, le=20)
    evaluation_keys: List[str] = []
    rubric_id: Optional[str] = None
    judge_model_id: Optional[str] = None


class PromptArenaCreate(BaseModel):
    project_id: str
    title: str = Field(min_length=1)
    dataset_id: str
    dataset_version_id: str
    prompt_version_ids: List[str] = Field(min_length=1)
    model_id: str
    agent_version_id: Optional[str] = None
    objective: str = ""
    hypothesis: str = ""
    repetitions: int = Field(default=1, ge=1, le=20)
    evaluation_keys: List[str] = []
    rubric_id: Optional[str] = None
    judge_model_id: Optional[str] = None


class ScorecardBuildRequest(BaseModel):
    project_id: str
    scope: str = "run"
    run_id: Optional[str] = None
    experiment_id: Optional[str] = None
    configuration_id: Optional[str] = None
    baseline_configuration_id: Optional[str] = None
    candidate_configuration_id: Optional[str] = None
    notes: str = ""


class FindingPromotionRequest(BaseModel):
    dataset_id: Optional[str] = None
    source_version_id: Optional[str] = None
    name: Optional[str] = None
    description: str = ""
    case_overrides: Dict[str, Any] = {}
    red_team_category: Optional[str] = None
    expected_behavior: Optional[str] = None


class FindingLinkRequest(BaseModel):
    type: str
    ref_id: str
    label: str = ""


class CertificationCandidateCreate(BaseModel):
    project_id: str
    label: str = Field(min_length=1)
    subject: Dict[str, Any] = {}
    evidence: Dict[str, Any] = {}
    environment: Dict[str, Any] = {}
    notes: str = ""


class CertificationCandidateUpdate(BaseModel):
    label: Optional[str] = None
    evidence: Optional[Dict[str, Any]] = None
    environment: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------- Phase 3 observability / context / tools
class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    system_prompt: Optional[str] = None
    default_model_id: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    streaming: Optional[bool] = None
    enabled_tool_ids: Optional[List[str]] = None
    knowledge_enabled: Optional[bool] = None
    context_strategy: Optional[str] = None
    context_options: Optional[Dict[str, Any]] = None
    knowledge_collection_ids: Optional[List[str]] = None
    retrieval_options: Optional[Dict[str, Any]] = None
    search_enabled: Optional[bool] = None
    search_provider_id: Optional[str] = None
    fetch_enabled: Optional[bool] = None
    fetch_provider_id: Optional[str] = None
    workspace_mode: Optional[str] = None


class BulkDeleteConversationsRequest(BaseModel):
    conversation_ids: List[str]
    project_id: Optional[str] = None



class AttachmentUpdate(BaseModel):
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    detached: Optional[bool] = None


class ToolUpdate(BaseModel):
    enabled: Optional[bool] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class FailureCreateRequest(BaseModel):
    run_id: Optional[str] = None
    trace_id: Optional[str] = None
    evaluation_id: Optional[str] = None
    category: str = Field(min_length=1)
    subcategory: str = ""
    source: str = "HUMAN"   # SYSTEM|HUMAN|JUDGE|RULE
    severity: str = "medium"  # low|medium|high|critical
    title: str = Field(min_length=1)
    description: str = ""
    evidence: Dict[str, Any] = {}
    confidence: Optional[float] = None
    tags: List[str] = []
    # Additive (V5 Marathon B): link a failure to a case/span/finding and
    # record its canonical taxonomy classification.
    dataset_case_id: Optional[str] = None
    span_id: Optional[str] = None
    finding_id: Optional[str] = None
    taxonomy_code: Optional[str] = None
    signals: Dict[str, Any] = {}


class FailureUpdateRequest(BaseModel):
    category: Optional[str] = None
    subcategory: Optional[str] = None
    source: Optional[str] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    note: str = ""


QUALITY_FAILURE_CATEGORIES = (
    "WRONG_ANSWER",
    "HALLUCINATION",
    "INSTRUCTION_VIOLATION",
    "INCOMPLETE",
    "CONTEXT_MISINTERPRETATION",
    "FORMAT_VIOLATION",
    "SECURITY_FAILURE",
    "TOOL_SELECTION_ERROR",
    "TOOL_EXECUTION_ERROR",
    "EXCESSIVE_COST",
    "EXCESSIVE_LATENCY",
    "OTHER",
)
FAILURE_SOURCES = ("SYSTEM", "HUMAN", "JUDGE", "RULE")
FAILURE_STATUSES = ("OPEN", "CONFIRMED", "DISMISSED", "RESOLVED")
CONTEXT_COMPONENT_TYPES = (
    "SYSTEM",
    "USER_TASK",
    "CONVERSATION_HISTORY",
    "ATTACHMENTS",
    "PROJECT_CONTEXT",
    "DATASET_CASE",
    "RETRIEVED_CONTEXT",
    "TOOL_DEFINITIONS",
    "OTHER",
    # Additive (V6.2 Context Lab): prompt/policy/invariant and tool-result
    # components. Existing values and their order are unchanged.
    "PROMPT",
    "POLICY",
    "INVARIANT",
    "TOOL_RESULT",
)
CONTEXT_STRATEGIES = ("FULL", "RECENT_MESSAGES", "MANUAL_SELECTION", "AUTO_TRUNCATE")

# Additive (V6.2 Context Lab): canonical source-class vocabulary for context
# components. Every component is annotated with exactly one source class; the
# mapping lives in ``app.services.context``.
CONTEXT_SOURCE_CLASSES = (
    "SYSTEM",
    "PROMPT",
    "POLICY",
    "INVARIANT",
    "KNOWLEDGE",
    "FILES",
    "PROJECT",
    "CONVERSATION",
    "TOOL_RESULT",
    "RETRIEVAL",
    "MANUAL",
    "OTHER",
)

# Additive (V6.2 Context Lab): Context Pack lifecycle and the source types a
# pack item may declare.
CONTEXT_PACK_STATUSES = ("draft", "frozen", "deprecated")
CONTEXT_PACK_SOURCE_TYPES = (
    "PROMPT",
    "POLICY",
    "INVARIANT",
    "PROJECT",
    "KNOWLEDGE",
    "MANUAL",
    "OTHER",
)
TRACE_TYPES = ("workspace", "arena", "council", "benchmark", "judge", "system", "tool_test", "red_team", "agent")
SPAN_TYPES = (
    "REQUEST",
    "CONTEXT_ASSEMBLY",
    "ATTACHMENT_PROCESSING",
    "MODEL_CALL",
    "STREAM",
    "TOOL_CALL",
    "TOOL_RESULT",
    "EVALUATION",
    "RETRY",
    "NORMALIZATION",
    "SYSTEM",
    "KNOWLEDGE_QUERY",
    "KNOWLEDGE_RETRIEVAL",
    "KNOWLEDGE_CONTEXT_INJECTION",
    "SANDBOX_CREATE",
    "CODE_EXECUTION",
    "SANDBOX_OUTPUT",
    "ARTIFACT_CAPTURE",
    # Additive (V4.2.3.8): internal sub-operations of a composite tool call.
    # These are never separate model ToolCalls; they nest under the parent
    # composite tool call (e.g. web_research -> search/fetch operations).
    "SEARCH_OPERATION",
    "FETCH_OPERATION",
    # Additive (V5 research quality): judge evaluation and regression execution
    # spans. Existing span types are unchanged.
    "JUDGE_EVALUATION",
    "REGRESSION_EXECUTION",
    # Additive (V5 Marathon B): red-team execution, scorecard build and
    # finding promotion spans. Existing span types are unchanged.
    "RED_TEAM_EXECUTION",
    "SCORECARD_BUILD",
    "FINDING_PROMOTION",
    # Additive (V6.3 Prompt Compiler): compilation and policy/invariant
    # resolution spans. Existing span types (including CONTEXT_ASSEMBLY) are
    # unchanged.
    "PROMPT_COMPILE",
    "POLICY_RESOLUTION",
    # Additive (V6.5 Agent Runtime): a resolved agent version's configuration
    # snapshot and its single execution. Existing span types are unchanged.
    "AGENT_CONFIGURATION",
    "AGENT_EXECUTION",
    # Additive (V6.7 Agent & Prompt Evaluation): one span per agent run
    # evaluation pass. Existing span types are unchanged.
    "AGENT_EVALUATION",
    # Additive (V6.9 Drift & Failure Analysis): one span per drift analysis.
    "DRIFT_ANALYSIS",
)

PROJECT_FILE_STATUSES = ("active", "detached", "deleted")
PROJECT_FILE_PROCESSING_STATUSES = ("pending", "ready", "failed", "unavailable")
KNOWLEDGE_COLLECTION_STATUSES = ("active", "deleted")
KNOWLEDGE_DOCUMENT_STATUSES = ("pending", "processing", "ready", "failed", "unavailable", "detached")
KNOWLEDGE_CHUNK_STRATEGIES = ("fixed", "paragraph")
CREDENTIAL_TYPES = ("API_KEY", "BEARER_TOKEN", "BASIC_AUTH", "CUSTOM_HEADER")
CUSTOM_TOOL_TYPES = ("http", "openapi", "code_interpreter")
TOOL_VERSION_SOURCES = ("manual", "openapi", "system")
CODE_EXECUTION_STATUSES = ("success", "error", "timeout", "unavailable")
PROJECT_ACTIVITY_TYPES = ("file_uploaded", "file_deleted", "knowledge_created", "knowledge_indexed", "knowledge_deleted", "tool_created", "tool_version_created", "tool_deleted", "credential_created", "credential_deleted", "code_executed")

# ---------------------------------------------------------------- Phase 5: research quality
V5_SCHEMA_VERSION = 2
EVALUATOR_VERSION = "v5.1.0"
EVALUATION_TYPES = ("deterministic", "rule", "judge_model", "human")
LEGACY_EVALUATOR_ALIASES = {"llm_judge": "judge_model", "ai_judge": "judge_model", "deterministic": "deterministic", "rule": "rule", "human": "human"}
EVALUATION_OUTCOMES = ("PASS", "FAIL", "ERROR", "NOT_EVALUATED")
EVALUATION_STATUSES = ("completed", "error", "not_evaluated")
EVALUATION_FINDING_CATEGORIES = (
    "correctness", "grounding", "citation", "instruction_following", "tool_selection",
    "tool_failure", "efficiency", "runtime", "security", "data",
    # Additive (V6.7 Agent & Prompt Evaluation): agent/prompt quality categories.
    # Existing values and their order are unchanged.
    "policy_compliance", "invariant_compliance", "task_completion",
    "scope_adherence", "role_adherence", "context_usage", "drift",
    # Additive (V6.9 Drift & Failure Analysis): drift categories. Existing
    # values and their order are unchanged.
    "prompt_drift", "design_drift", "architecture_drift", "logic_drift",
)
EVALUATION_FINDING_SEVERITIES = ("info", "low", "medium", "high", "critical")
DETERMINISTIC_EVALUATOR_KEYS = ("exact_match", "contains", "regex", "token_overlap")
RULE_EVALUATOR_KEYS = (
    "assertions", "knowledge_grounding", "tool_policy", "red_team",
    # Additive (V6.7): agent-provenance rule evaluators. Existing keys are
    # unchanged; these only run when the caller selects them or the evidence
    # carries agent provenance.
    "agent_policy_compliance", "agent_invariant_compliance",
    "agent_tool_discipline", "agent_context_usage",
    # Additive (V6.9): the drift rule evaluator key. Existing keys unchanged.
    "drift",
)
ASSERTION_TYPES = (
    "output_contains", "output_not_contains", "output_exact_match", "output_regex",
    "output_token_overlap_min", "output_not_empty",
    "max_latency_ms", "max_output_tokens", "max_cost",
    "tool_call_count_equals", "no_tools", "required_tool", "forbidden_tool",
    "max_tool_calls", "max_failed_tool_calls",
    "knowledge_marker_valid", "knowledge_expected", "knowledge_absent", "grounded_citation",
)

# Additive (V5 Marathon B): red-team vocabulary and finding lifecycle.
RED_TEAM_CATEGORIES = ("instruction_conflict","prompt_injection","knowledge_injection","malicious_document_instructions","tool_misuse_attempt","forbidden_tool_request","credential_extraction_attempt","cross_project_data_attempt","citation_fabrication","unsupported_factual_confidence","context_poisoning","excessive_tool_loops")
RED_TEAM_EXPECTED_BEHAVIORS = ("hold_boundary","refuse","answer_without_tools","cite_only_provided","treat_document_as_untrusted","no_tool_calls","answer_normally")
FINDING_STATUSES = ("provisional","validated","superseded","resolved","reproduced","accepted-risk","needs-investigation")


class RegenerateRequest(BaseModel):
    model_id: str
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    seed: Optional[int] = None
    streaming: Optional[bool] = None
    enabled_tool_ids: List[str] = []
    knowledge_enabled: Optional[bool] = None
    context_strategy: Optional[str] = None
    context_options: Dict[str, Any] = {}
    knowledge_collection_ids: List[str] = []
    retrieval_options: Dict[str, Any] = {}
    max_tool_rounds: Optional[int] = None
    search_enabled: Optional[bool] = None
    search_provider_id: Optional[str] = None
    fetch_enabled: Optional[bool] = None
    fetch_provider_id: Optional[str] = None
    workspace_mode: Optional[str] = None


class ContinueConversationRequest(BaseModel):
    model_id: str
    continuation_prompt: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    seed: Optional[int] = None
    streaming: Optional[bool] = None
    enabled_tool_ids: List[str] = []
    knowledge_enabled: Optional[bool] = None
    context_strategy: Optional[str] = None
    context_options: Dict[str, Any] = {}
    knowledge_collection_ids: List[str] = []
    retrieval_options: Dict[str, Any] = {}
    max_tool_rounds: Optional[int] = None
    search_enabled: Optional[bool] = None
    search_provider_id: Optional[str] = None
    fetch_enabled: Optional[bool] = None
    fetch_provider_id: Optional[str] = None
    workspace_mode: Optional[str] = None



# ---------------------------------------------------------------- Phase 4: workspace platform DTOs
class ProjectFileUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None   # active | detached | deleted


class KnowledgeCollectionCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1)
    description: str = ""
    embedding_config: Dict[str, Any] = {}
    chunking_config: Dict[str, Any] = {}


class KnowledgeCollectionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    embedding_config: Optional[Dict[str, Any]] = None
    chunking_config: Optional[Dict[str, Any]] = None


class KnowledgeDocumentAttach(BaseModel):
    project_file_ids: List[str] = Field(min_length=1)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    collection_ids: List[str] = []
    top_k: int = 5
    score_threshold: Optional[float] = None
    project_id: Optional[str] = None


class ToolVersionCreate(BaseModel):
    tool_type: str            # http | openapi | code_interpreter
    version_label: Optional[str] = None
    input_schema: Dict[str, Any] = {}
    output_schema: Dict[str, Any] = {}
    permissions: List[str] = []
    risk_level: str = "low"
    implementation_config: Dict[str, Any] = {}
    source: str = "manual"


class CustomToolCreate(BaseModel):
    project_id: Optional[str] = None
    name: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_\-]+$")
    display_name: str = ""
    description: str = ""
    enabled: bool = True
    tags: List[str] = []
    version: ToolVersionCreate


class CustomToolUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    tags: Optional[List[str]] = None
    # When a version payload is supplied, a new immutable ToolVersion is created.
    version: Optional[ToolVersionCreate] = None


class ToolTestRequest(BaseModel):
    arguments: Dict[str, Any] = {}
    project_id: Optional[str] = None
    code: Optional[str] = None   # convenience for code_interpreter tests


class ToolCredentialCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.\-]+$")
    credential_type: str = "API_KEY"
    value: str = Field(min_length=1)
    metadata: Dict[str, Any] = {}


class ToolCredentialUpdate(BaseModel):
    credential_type: Optional[str] = None
    value: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class OpenAPIImportRequest(BaseModel):
    project_id: str
    spec: Optional[str] = None        # raw JSON/YAML text
    spec_url: Optional[str] = None    # fetched server-side behind SSRF guard
    base_url_override: Optional[str] = None


class OpenAPIOperationSelection(BaseModel):
    operation_id: str
    include: bool = True
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    risk_level: Optional[str] = None
    permissions: Optional[List[str]] = None
    credential_id: Optional[str] = None
    implementation_config: Optional[Dict[str, Any]] = None


class OpenAPIGenerateRequest(BaseModel):
    project_id: str
    spec: str
    base_url_override: Optional[str] = None
    operations: List[OpenAPIOperationSelection] = []


# ---------------------------------------------------------------- V6.2 Context Lab
# Context Packs are project-scoped, versioned collections of context items.
# Budget policies are project-scoped token budgets applied by the existing
# context assembler. Neither introduces a parallel runtime assembler;
# previews call ``app.services.context.assemble_context`` directly.
class ContextPackCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1)
    description: str = ""


class ContextPackUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None  # active | archived


class ContextPackItem(BaseModel):
    key: str
    title: str = ""
    source_type: str = "OTHER"
    content: str = ""
    order: int = 0
    metadata: Dict[str, Any] = {}


class ContextPackVersionCreate(BaseModel):
    description: str = ""
    items: List[ContextPackItem] = []


class ContextPackVersionUpdate(BaseModel):
    description: Optional[str] = None
    items: Optional[List[ContextPackItem]] = None


class ContextBudgetPolicyCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1)
    max_context_tokens: Optional[int] = None
    reserve_output_tokens: Optional[int] = None
    knowledge_budget: Optional[int] = None
    history_budget: Optional[int] = None
    file_budget: Optional[int] = None
    policy_budget: Optional[int] = None
    status: str = "active"


class ContextBudgetPolicyUpdate(BaseModel):
    name: Optional[str] = None
    max_context_tokens: Optional[int] = None
    reserve_output_tokens: Optional[int] = None
    knowledge_budget: Optional[int] = None
    history_budget: Optional[int] = None
    file_budget: Optional[int] = None
    policy_budget: Optional[int] = None
    status: Optional[str] = None


class ContextPreviewRequest(BaseModel):
    project_id: str
    system_prompt: str = ""
    messages: List[Dict[str, Any]] = []
    project_context: Optional[str] = None
    retrieved_context: List[Dict[str, Any]] = []
    attachments: List[Dict[str, Any]] = []
    tool_definitions: List[Dict[str, Any]] = []
    dataset_case: Optional[Dict[str, Any]] = None
    strategy: str = "FULL"
    context_options: Dict[str, Any] = {}
    budget_policy_id: Optional[str] = None
    max_context_tokens: Optional[int] = None
    reserved_output_tokens: Optional[int] = None


# ---------------------------------------------------------------- V6.3 Prompt Compiler
# The Prompt Compiler renders one frozen prompt version plus its resolved
# project assets (blocks, policies, invariants, context refs) into a
# deterministic, target-shaped payload. It never executes arbitrary templates:
# only bounded ``{{identifier}}`` substitution is performed, and untrusted
# content stays tagged as untrusted.
COMPILER_VERSION = "v6.3.0"
PROMPT_COMPILER_TARGETS = (
    "generic_chat",
    "openai_tools",
    "anthropic_tools",
    "gemini_tools",
    "coding_agent",
)
PROMPT_TRUST_CLASSES = (
    "TRUSTED_INSTRUCTION",
    "TRUSTED_POLICY",
    "UNTRUSTED_CONTENT",
    "TOOL_OUTPUT",
)
PROMPT_COMPILER_MODES = ("structured", "simple")


class PromptCompileRequest(BaseModel):
    project_id: str
    prompt_version_id: str
    agent_version_id: Optional[str] = None
    target: str = "generic_chat"
    mode: str = "structured"
    variables: Dict[str, Any] = {}
    block_ids: List[str] = []
    policy_ids: List[str] = []
    include_policies: bool = True
    include_invariants: bool = True
    invariant_ids: List[str] = []
    context_pack_version_ids: List[str] = []
    untrusted_context: List[Dict[str, Any]] = []
    tool_outputs: List[Dict[str, Any]] = []
    tool_definitions: List[Dict[str, Any]] = []


# ---------------------------------------------------------------- V6.4 Agent Registry & Versioning
# An Agent is a first-class, project-scoped asset with an append-only version
# history. Every version carries a FIXED model binding plus the V6.0-V6.3
# assets it composes (prompt version, context pack versions / budget policy,
# tools, knowledge collections, rubric). The ``routing`` field exists for future
# compatibility but only ``mode == "fixed"`` is accepted in V6.
AGENT_STATUSES = ("active", "archived")
AGENT_VERSION_STATES = ("draft", "frozen", "deprecated")
AGENT_ROLES = ("engineer", "architect", "reviewer", "researcher", "analyst", "writer", "other")
OUTPUT_CONTRACT_FORMATS = ("text", "json", "markdown")
AGENT_ROUTING_MODES = ("fixed",)

# V6.6 permission boundary vocabulary. The service layer imports this single
# tuple (``services.agent_permissions``) so denial kinds can never drift.
PERMISSION_DENIAL_KINDS = (
    "tool_not_found",
    "tool_foreign_project",
    "tool_disabled",
    "tool_denied",
    "tool_not_allowed",
    "tool_model_capability_missing",
    "knowledge_collection_not_found",
    "knowledge_collection_foreign_project",
    "knowledge_collection_disabled",
)


class AgentCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1)
    description: str = ""
    role: str = "engineer"
    purpose: str = ""
    responsibilities: List[str] = []
    non_responsibilities: List[str] = []
    status: str = "active"


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    role: Optional[str] = None
    purpose: Optional[str] = None
    responsibilities: Optional[List[str]] = None
    non_responsibilities: Optional[List[str]] = None
    status: Optional[str] = None


class AgentVersionCreate(BaseModel):
    model_id: str
    prompt_version_id: Optional[str] = None
    context_policy: Dict[str, Any] = {}
    tool_policy: Dict[str, Any] = {}
    knowledge_policy: Dict[str, Any] = {}
    runtime_policy: Dict[str, Any] = {}
    output_contract: Dict[str, Any] = {}
    evaluation_profile: Dict[str, Any] = {}
    routing: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}


class AgentVersionUpdate(BaseModel):
    model_id: Optional[str] = None
    prompt_version_id: Optional[str] = None
    context_policy: Optional[Dict[str, Any]] = None
    tool_policy: Optional[Dict[str, Any]] = None
    knowledge_policy: Optional[Dict[str, Any]] = None
    runtime_policy: Optional[Dict[str, Any]] = None
    output_contract: Optional[Dict[str, Any]] = None
    evaluation_profile: Optional[Dict[str, Any]] = None
    routing: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------- V6.5 Agent Runtime
# A run request resolves exactly one FROZEN agent version into existing runtime
# configuration, executes it through the canonical ``execute_model`` funnel and
# snapshots the resolved provenance back onto the run additively.
class AgentRunRequest(BaseModel):
    project_id: str
    # Additive (V6.8): optional; derived from the loaded agent version when absent.
    agent_id: Optional[str] = None
    agent_version_id: str
    task: str = Field(min_length=1)
    variables: Dict[str, Any] = {}
    dataset_case_id: Optional[str] = None
    dataset_version_id: Optional[str] = None
    experiment_id: Optional[str] = None
    configuration_id: Optional[str] = None
    attachments: List[Dict[str, Any]] = []
    reserved_output_tokens: Optional[int] = None


# ---------------------------------------------------------------- V6.7 Agent & Prompt Evaluation
# Evaluate one agent run through the canonical V5 evaluation framework. This is
# a DTO only: no V6-only evaluator store and no parallel judge exist.
class AgentEvaluationRequest(BaseModel):
    evaluator_keys: List[str] = []
    use_agent_profile: bool = True
    rubric_id: Optional[str] = None
    judge_model_id: Optional[str] = None
    threshold: Optional[float] = None
    record_failures: bool = False
    profile_type: str = "agent"  # agent | prompt
    dimensions: List[str] = []
    notes: str = ""


# ---------------------------------------------------------------- V6.9 Drift & Failure Analysis
# Drift analysis is additive quality analysis: it compares an observed change
# set / prompt output against declared contracts and surfaces observable
# drift findings. It never replaces or mutates the V5 failure taxonomy.
DRIFT_TYPES = ("PROMPT_DRIFT", "DESIGN_DRIFT", "ARCHITECTURE_DRIFT", "LOGIC_DRIFT")
DRIFT_SOURCES = ("deterministic", "judge_derived")


class DriftChangedFile(BaseModel):
    path: str
    language: str = ""
    added_lines: int = 0
    removed_lines: int = 0
    imports_added: List[str] = []
    routes_added: List[str] = []
    components_added: List[str] = []
    services_added: List[str] = []
    enums_added: List[str] = []
    constants_added: List[str] = []
    hardcoded_colors: List[str] = []
    design_tokens_added: List[str] = []


class DriftContracts(BaseModel):
    canonical_services: List[Dict[str, Any]] = []
    forbidden_patterns: List[Dict[str, Any]] = []
    design_tokens: List[str] = []
    allowed_layers: Dict[str, List[str]] = {}
    enums: List[Dict[str, Any]] = []
    duplicated_rules: List[Dict[str, Any]] = []


class DriftPromptContext(BaseModel):
    required_invariant_codes: List[str] = []
    required_phases: List[str] = []
    prohibited_scope: List[str] = []
    compiled_invariants_used: List[str] = []
    output_text: str = ""
    prompt_version_id: Optional[str] = None


class DriftAnalyzeRequest(BaseModel):
    project_id: str
    scope: str = "all"
    changed_files: List[DriftChangedFile] = []
    contracts: DriftContracts = DriftContracts()
    prompt_context: DriftPromptContext = DriftPromptContext()
    run_id: Optional[str] = None
    evaluation_id: Optional[str] = None
    experiment_id: Optional[str] = None
    judge_model_id: Optional[str] = None
    rubric_id: Optional[str] = None
    notes: str = ""

# ---------------------------------------------------------------- V6.14 Workspace Prompt Capture
# Capture/composer DTOs. A capture promotes an existing Workspace conversation
# (or one assistant response) into a Prompt Draft. It reuses the existing
# Prompt Lab (prompts / prompt_versions / prompt_blocks / project_policies /
# project_invariants) and never creates a parallel prompt system. Versions are
# created as DRAFT unless the caller explicitly requests a freeze for the
# Create Agent flow (agent versions may only bind frozen prompt versions).


class PromptCaptureRequest(BaseModel):
    project_id: str
    conversation_id: str
    message_ids: List[str] = []
    mode: str = "conversation"   # response | conversation
    prompt_id: Optional[str] = None


class PromptCaptureSaveRequest(BaseModel):
    project_id: str
    conversation_id: str
    message_ids: List[str] = []
    mode: str = "conversation"
    kind: str = "prompt"         # prompt | planning_template
    name: str = ""
    title: str = ""
    purpose: str = ""
    notes: str = ""
    prompt_id: Optional[str] = None
    include_block_ids: List[str] = []
    include_policy_ids: List[str] = []
    include_invariant_ids: List[str] = []
    create_missing_blocks: bool = False
    freeze_version: bool = False
    target: str = "generic_chat"
    compiler_mode: str = "structured"
    variables: Dict[str, Any] = {}


# ---------------------------------------------------------------- V6.14 Planning Decision Memory
# A per-conversation, provenance-aware decision ledger. It is NOT a second
# prompt system: each item is a bounded record of what the user confirmed,
# rejected, assumed, left open, or must verify during Workspace Plan mode.
# Status semantics are intentionally distinct and must never collapse into a
# single generic note.
PLANNING_ITEM_STATUSES = (
    "CONFIRMED_BY_USER",
    "ASSUMED_BY_MODEL",
    "TECHNICALLY_VERIFIED",
    "NEEDS_VERIFICATION",
    "REJECTED",
    "SUPERSEDED",
    "CONFLICTED",
)
PLANNING_ITEM_KINDS = (
    "decision",
    "constraint",
    "rejected_option",
    "assumption",
    "open_question",
    "verification_item",
)
PLANNING_SOURCE_TYPES = ("user", "assistant", "system", "tool", "manual", "research")
PLANNING_VERIFICATION_STATUSES = ("UNVERIFIED", "VERIFIED", "INVALIDATED")
PLANNING_CONFLICT_STATUSES = ("open", "resolved")
PLANNING_CONFLICT_RESOLUTIONS = ("accept_new", "keep_previous", "edit_manual")


class PlanningItemCreate(BaseModel):
    project_id: str
    conversation_id: str
    topic_key: str = ""
    category: str = "other"
    kind: str = "decision"
    value: str = ""
    status: str = "CONFIRMED_BY_USER"
    source_type: str = "manual"
    confidence: float = 1.0
    source_message_id: Optional[str] = None
    excerpt: str = ""


class PlanningItemUpdate(BaseModel):
    topic_key: Optional[str] = None
    category: Optional[str] = None
    kind: Optional[str] = None
    value: Optional[str] = None
    status: Optional[str] = None
    confidence: Optional[float] = None


class PlanningVerifyRequest(BaseModel):
    verification_status: str = "TECHNICALLY_VERIFIED"  # TECHNICALLY_VERIFIED | NEEDS_VERIFICATION
    evidence: str = ""


class PlanningConflictResolve(BaseModel):
    resolution: str
    value: str = ""


class PlanningQuestionsRequest(BaseModel):
    project_id: str
    conversation_id: str
    questions: List[str] = []


class PlanningIngestRequest(BaseModel):
    project_id: str
    message_ids: List[str] = []


class ConsolidatedPlanRequest(BaseModel):
    project_id: str
    include_policy_ids: List[str] = []
    include_invariant_ids: List[str] = []


# ---------------------------------------------------------------- V6.15 Structured Planning Graph
# A *derived* graph view over the V6.14 planning decision ledger. Decision nodes
# reference their authoritative ``planning_decisions`` row via ``decision_id``;
# the graph is rebuilt deterministically from the ledger and never becomes a
# source of truth. Node types and relations are bounded vocabularies.
PLANNING_NODE_TYPES = (
    "ROOT",
    "SECTION",
    "REQUIREMENT",
    "DECISION",
    "CONSTRAINT",
    "WORKFLOW",
    "ACTOR",
    "COMPONENT",
    "INTEGRATION",
    "RISK",
    "ASSUMPTION",
    "OPEN_QUESTION",
)
PLANNING_RELATIONS = (
    "CONTAINS",
    "DEPENDS_ON",
    "AFFECTS",
    "CONFLICTS_WITH",
    "DERIVED_FROM",
    "RELATES_TO",
)
PLANNING_NODE_STATUSES = (
    "STRUCTURAL",
    "CONFIRMED_BY_USER",
    "ASSUMED_BY_MODEL",
    "TECHNICALLY_VERIFIED",
    "NEEDS_VERIFICATION",
    "REJECTED",
    "SUPERSEDED",
    "CONFLICTED",
)


class PlanningGraphSyncRequest(BaseModel):
    project_id: str
    conversation_id: Optional[str] = None


class PlanningImpactRequest(BaseModel):
    project_id: str
    decision_id: Optional[str] = None
    node_id: Optional[str] = None


class PlanningReviewClear(BaseModel):
    project_id: str


# ---------------------------------------------------------------- V6.15 Living PRD
# A Living PRD is a *compiled artifact* derived from the V6.14 authoritative
# decision ledger and the V6.15 planning graph. It never rewrites a decision:
# every compiled item references its ledger row through ``decision_id`` and the
# readiness report exposes explicit per-dimension states instead of a fake
# percentage.
PRD_STATUSES = ("DRAFT", "REVIEW", "APPROVED", "SUPERSEDED")
READINESS_STATES = ("READY", "INCOMPLETE", "BLOCKED", "NEEDS_VERIFICATION")


class PrdGenerateRequest(BaseModel):
    project_id: str
    conversation_id: Optional[str] = None


class PrdApproveRequest(BaseModel):
    project_id: str
