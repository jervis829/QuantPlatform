"""Natural Language & Multi-Agent Research Committee Strategy Generation Pipeline (P10 + Auto-Research Loop)."""

from quant_platform.nl_generator.pipeline import (
    BacktestGateConfig,
    BacktestGateFailedError,
    NLStrategyGenerationPipeline,
)
from quant_platform.nl_generator.providers import (
    DeterministicRuleLLMProvider,
    LLMCodeGenerator,
    OpenAICompatibleLLMProvider,
)
from quant_platform.nl_generator.research_committee import (
    AutoResearchCommittee,
    CommitteeRoundRecord,
    DirectorAgent,
    DirectorThesis,
    MarketRegimeProfile,
    QuantCoderAgent,
    RiskCriticAgent,
    RiskCritique,
    build_market_regime_profile,
)
from quant_platform.nl_generator.spec_parser import NLStrategySpec, parse_nl_prompt_to_spec

__all__ = [
    "AutoResearchCommittee",
    "BacktestGateConfig",
    "BacktestGateFailedError",
    "CommitteeRoundRecord",
    "DeterministicRuleLLMProvider",
    "DirectorAgent",
    "DirectorThesis",
    "LLMCodeGenerator",
    "MarketRegimeProfile",
    "NLStrategyGenerationPipeline",
    "NLStrategySpec",
    "OpenAICompatibleLLMProvider",
    "QuantCoderAgent",
    "RiskCriticAgent",
    "RiskCritique",
    "build_market_regime_profile",
    "parse_nl_prompt_to_spec",
]
