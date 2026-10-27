"""Natural Language -> LLM Strategy Generation Pipeline sharing AST/Sandbox/Backtest/Approval gates (P10)."""

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
from quant_platform.nl_generator.spec_parser import NLStrategySpec, parse_nl_prompt_to_spec

__all__ = [
    "BacktestGateConfig",
    "BacktestGateFailedError",
    "DeterministicRuleLLMProvider",
    "LLMCodeGenerator",
    "NLStrategyGenerationPipeline",
    "NLStrategySpec",
    "OpenAICompatibleLLMProvider",
    "parse_nl_prompt_to_spec",
]
