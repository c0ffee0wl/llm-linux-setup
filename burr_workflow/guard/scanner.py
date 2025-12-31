"""LLM Guard scanner wrapper.

Provides a unified interface for scanning inputs and outputs using
the llm-guard library's scanner classes.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .vault import VaultManager

try:
    from llm_guard import scan_output, scan_prompt
    from llm_guard.input_scanners import (
        Anonymize,
        BanCode,
        BanSubstrings,
        BanTopics,
        Code,
        Gibberish,
        InvisibleText,
        Language,
        PromptInjection,
        Regex,
        Secrets,
        TokenLimit,
    )
    from llm_guard.output_scanners import (
        JSON,
        Deanonymize,
        FactualConsistency,
        LanguageSame,
        MaliciousURLs,
        NoRefusal,
        ReadingTime,
        Relevance,
        Sensitive,
        URLReachability,
    )
    from llm_guard.output_scanners import (
        BanCode as BanCodeOutput,
    )
    from llm_guard.output_scanners import (
        BanSubstrings as BanSubstringsOutput,
    )
    from llm_guard.output_scanners import (
        BanTopics as BanTopicsOutput,
    )
    from llm_guard.output_scanners import (
        Code as CodeOutput,
    )
    from llm_guard.output_scanners import (
        Gibberish as GibberishOutput,
    )
    from llm_guard.output_scanners import (
        Language as LanguageOutput,
    )
    from llm_guard.output_scanners import (
        Regex as RegexOutput,
    )
    LLM_GUARD_AVAILABLE = True
except ImportError:
    LLM_GUARD_AVAILABLE = False
    # Placeholders to avoid NameError
    scan_prompt = None  # type: ignore[assignment]
    scan_output = None  # type: ignore[assignment]


class GuardError(Exception):
    """Guard validation failed."""

    pass


@dataclass
class ScanResult:
    """Result of guard scanning."""

    sanitized: str
    passed: bool
    risk_scores: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    failed_scanners: list[str] = field(default_factory=list)


class GuardScanner:
    """Wrapper for LLM Guard scanners.

    Provides methods for scanning inputs before step execution and
    outputs after step execution. Supports 12 input scanners and
    17 output scanners.

    Input Scanners (12):
        - anonymize: Replace PII with placeholders (uses Vault)
        - prompt_injection: Detect jailbreak attempts (DeBERTa)
        - secrets: Detect API keys, passwords, tokens
        - invisible_text: Strip zero-width/invisible chars
        - token_limit: Prevent context overflow
        - ban_topics: Block specific subjects
        - ban_substrings: Block specific text patterns
        - ban_code: Block prompts containing code
        - code: Detect/allow specific languages
        - gibberish: Detect nonsense input
        - language: Restrict to specific languages
        - regex: Custom regex validation

    Output Scanners (17):
        - deanonymize: Restore anonymized entities (uses Vault)
        - sensitive: Detect/redact sensitive data
        - no_refusal: Detect model refusals
        - factual_consistency: Detect hallucinations (NLI model)
        - relevance: Check output relevance to prompt
        - json: Validate JSON structure
        - malicious_urls: Detect dangerous URLs
        - url_reachability: Verify URLs are accessible
        - language_same: Ensure same language as input
        - language: Restrict output language
        - reading_time: Limit response length
        - gibberish: Detect nonsense output
        - ban_topics: Block specific subjects
        - ban_substrings: Block specific text
        - ban_code: Block responses with code
        - code: Detect/allow code languages
        - regex: Custom regex validation
    """

    def __init__(self, vault_manager: "VaultManager") -> None:
        """Initialize guard scanner.

        Args:
            vault_manager: VaultManager for anonymize/deanonymize flow.

        Raises:
            ImportError: If llm-guard is not installed.
        """
        if not LLM_GUARD_AVAILABLE:
            raise ImportError(
                "llm-guard not installed. Run: pip install burr_workflow[guard]"
            )
        self._vault_manager = vault_manager

    def scan_input(
        self,
        content: str,
        scanners_config: dict[str, dict | None],
    ) -> ScanResult:
        """Scan input before step execution.

        Args:
            content: Input content to scan.
            scanners_config: Scanner name to config mapping.

        Returns:
            ScanResult with sanitized content and validation status.
        """
        scanners = self._build_input_scanners(scanners_config)
        if not scanners:
            return ScanResult(sanitized=content, passed=True)

        sanitized, results_valid, results_score = scan_prompt(scanners, content)

        failed = [name for name, valid in results_valid.items() if not valid]

        return ScanResult(
            sanitized=sanitized,
            passed=all(results_valid.values()),
            risk_scores=results_score,
            details={"valid": results_valid},
            failed_scanners=failed,
        )

    def scan_output(
        self,
        prompt: str,
        content: str,
        scanners_config: dict[str, dict | None],
    ) -> ScanResult:
        """Scan output after step execution.

        Args:
            prompt: Original input prompt (for relevance checks).
            content: Output content to scan.
            scanners_config: Scanner name to config mapping.

        Returns:
            ScanResult with sanitized content and validation status.
        """
        scanners = self._build_output_scanners(scanners_config)
        if not scanners:
            return ScanResult(sanitized=content, passed=True)

        sanitized, results_valid, results_score = scan_output(
            scanners, prompt, content
        )

        failed = [name for name, valid in results_valid.items() if not valid]

        return ScanResult(
            sanitized=sanitized,
            passed=all(results_valid.values()),
            risk_scores=results_score,
            details={"valid": results_valid},
            failed_scanners=failed,
        )

    def _build_input_scanners(self, config: dict[str, dict | None]) -> list:
        """Build input scanner instances from config.

        Args:
            config: Scanner name to parameters mapping.

        Returns:
            List of scanner instances.
        """
        scanners = []
        vault = self._vault_manager.vault

        for name, params in config.items():
            params = params or {}
            scanner = self._create_input_scanner(name, params, vault)
            if scanner:
                scanners.append(scanner)

        return scanners

    def _create_input_scanner(
        self, name: str, params: dict[str, Any], vault: Any
    ) -> Any:
        """Create single input scanner.

        Args:
            name: Scanner name (e.g., 'anonymize', 'prompt_injection').
            params: Scanner-specific parameters.
            vault: Vault instance for anonymize scanner.

        Returns:
            Scanner instance or None if unknown scanner.
        """
        name_lower = name.lower()

        if name_lower == "anonymize":
            # Required entities list
            entities = params.get("entities", [
                "CREDIT_CARD", "CRYPTO", "EMAIL_ADDRESS", "IBAN_CODE",
                "IP_ADDRESS", "PERSON", "PHONE_NUMBER", "US_SSN",
                "US_BANK_NUMBER", "CREDIT_CARD", "NRP", "URL"
            ])
            return Anonymize(vault=vault, pii_entities=entities)

        elif name_lower == "prompt_injection":
            threshold = params.get("threshold", 0.9)
            return PromptInjection(threshold=threshold)

        elif name_lower == "secrets":
            redact = params.get("redact", True)
            return Secrets(redact_mode="all" if redact else "none")

        elif name_lower == "invisible_text":
            return InvisibleText()

        elif name_lower == "token_limit":
            limit = params.get("limit", 4096)
            encoding_name = params.get("encoding", "cl100k_base")
            return TokenLimit(limit=limit, encoding_name=encoding_name)

        elif name_lower == "ban_topics":
            topics = params.get("topics", [])
            threshold = params.get("threshold", 0.75)
            return BanTopics(topics=topics, threshold=threshold)

        elif name_lower == "ban_substrings":
            substrings = params.get("substrings", [])
            case_sensitive = params.get("case_sensitive", False)
            return BanSubstrings(
                substrings=substrings,
                match_type="str",
                case_sensitive=case_sensitive,
            )

        elif name_lower == "ban_code":
            return BanCode()

        elif name_lower == "code":
            # Code scanner detects programming languages
            languages = params.get("languages", [])
            is_blocked = params.get("blocked", True)
            return Code(languages=languages, is_blocked=is_blocked)

        elif name_lower == "gibberish":
            threshold = params.get("threshold", 0.7)
            return Gibberish(threshold=threshold)

        elif name_lower == "language":
            valid_languages = params.get("languages", ["en"])
            return Language(valid_languages=valid_languages)

        elif name_lower == "regex":
            patterns = params.get("patterns", [])
            is_blocked = params.get("blocked", True)
            return Regex(patterns=patterns, is_blocked=is_blocked)

        else:
            # Unknown scanner - skip silently
            return None

    def _build_output_scanners(self, config: dict[str, dict | None]) -> list:
        """Build output scanner instances from config.

        Args:
            config: Scanner name to parameters mapping.

        Returns:
            List of scanner instances.
        """
        scanners = []
        vault = self._vault_manager.vault

        for name, params in config.items():
            params = params or {}
            scanner = self._create_output_scanner(name, params, vault)
            if scanner:
                scanners.append(scanner)

        return scanners

    def _create_output_scanner(
        self, name: str, params: dict[str, Any], vault: Any
    ) -> Any:
        """Create single output scanner.

        Args:
            name: Scanner name (e.g., 'deanonymize', 'sensitive').
            params: Scanner-specific parameters.
            vault: Vault instance for deanonymize scanner.

        Returns:
            Scanner instance or None if unknown scanner.
        """
        name_lower = name.lower()

        if name_lower == "deanonymize":
            return Deanonymize(vault=vault)

        elif name_lower == "sensitive":
            redact = params.get("redact", True)
            entities = params.get("entities", [
                "CREDIT_CARD", "CRYPTO", "EMAIL_ADDRESS", "IBAN_CODE",
                "IP_ADDRESS", "PERSON", "PHONE_NUMBER", "US_SSN",
            ])
            return Sensitive(
                entity_types=entities,
                redact=redact,
            )

        elif name_lower == "no_refusal":
            return NoRefusal()

        elif name_lower == "factual_consistency":
            threshold = params.get("threshold", 0.7)
            return FactualConsistency(minimum_score=threshold)

        elif name_lower == "relevance":
            threshold = params.get("threshold", 0.5)
            return Relevance(threshold=threshold)

        elif name_lower == "json":
            required_fields = params.get("required_fields", [])
            return JSON(required_elements=required_fields)

        elif name_lower == "malicious_urls":
            return MaliciousURLs()

        elif name_lower == "url_reachability":
            timeout = params.get("timeout", 5)
            return URLReachability(timeout=timeout)

        elif name_lower == "language_same":
            return LanguageSame()

        elif name_lower == "language":
            valid_languages = params.get("languages", ["en"])
            return LanguageOutput(valid_languages=valid_languages)

        elif name_lower == "reading_time":
            max_time = params.get("max_time", 5)  # minutes
            return ReadingTime(max_time=max_time)

        elif name_lower == "gibberish":
            threshold = params.get("threshold", 0.7)
            return GibberishOutput(threshold=threshold)

        elif name_lower == "ban_topics":
            topics = params.get("topics", [])
            threshold = params.get("threshold", 0.75)
            return BanTopicsOutput(topics=topics, threshold=threshold)

        elif name_lower == "ban_substrings":
            substrings = params.get("substrings", [])
            case_sensitive = params.get("case_sensitive", False)
            return BanSubstringsOutput(
                substrings=substrings,
                match_type="str",
                case_sensitive=case_sensitive,
            )

        elif name_lower == "ban_code":
            return BanCodeOutput()

        elif name_lower == "code":
            languages = params.get("languages", [])
            is_blocked = params.get("blocked", True)
            return CodeOutput(languages=languages, is_blocked=is_blocked)

        elif name_lower == "regex":
            patterns = params.get("patterns", [])
            is_blocked = params.get("blocked", True)
            return RegexOutput(patterns=patterns, is_blocked=is_blocked)

        else:
            # Unknown scanner - skip silently
            return None


def initialize_models(scanner_name: str) -> None:
    """Pre-download models for a scanner.

    Creates scanner instances to trigger model downloads for ML-based
    scanners. This avoids download delays during first workflow execution.

    Args:
        scanner_name: Name of scanner to initialize.

    Raises:
        ImportError: If llm-guard is not installed.
    """
    if not LLM_GUARD_AVAILABLE:
        raise ImportError(
            "llm-guard not installed. Run: pip install burr_workflow[guard]"
        )

    from llm_guard.vault import Vault

    scanner_lower = scanner_name.lower()

    if scanner_lower == "prompt_injection":
        # Downloads DeBERTa v2 model (~500MB)
        PromptInjection()

    elif scanner_lower in ("anonymize", "sensitive"):
        # Downloads Presidio + spaCy models (~200MB)
        vault = Vault()
        Anonymize(vault=vault)

    elif scanner_lower == "factual_consistency":
        # Downloads NLI model (~400MB)
        FactualConsistency()

    elif scanner_lower == "gibberish":
        Gibberish()

    elif scanner_lower == "no_refusal":
        NoRefusal()

    elif scanner_lower == "relevance":
        Relevance()

    elif scanner_lower == "ban_topics":
        BanTopics(topics=["test"])  # Needs at least one topic

    elif scanner_lower == "language":
        Language(valid_languages=["en"])

    # Non-ML scanners (no model downloads needed):
    # secrets, invisible_text, token_limit, ban_substrings, ban_code,
    # code, regex, deanonymize, json, malicious_urls, url_reachability,
    # language_same, reading_time
