"""Pydantic schemas for structured LLM output.

This module contains schema definitions for:
- Pentest findings (OWASP Risk Matrix 1-9)
- Command safety evaluation for auto mode
"""

from pydantic import BaseModel, Field
from typing import Literal


# Schema for LLM-generated pentest findings (OWASP Risk Matrix 1-9)
class FindingSchema(BaseModel):
    """Schema for LLM-analyzed penetration testing findings."""
    suggested_title: str = Field(description="Concise vulnerability title (max 60 chars)")
    severity: int = Field(ge=1, le=9, description="Severity 1-9 per OWASP Risk Matrix (likelihood × impact)")
    severity_rationale: str = Field(description="Brief explanation of the severity score")
    description: str = Field(description="Expanded technical description of the vulnerability")
    remediation: str = Field(description="Step-by-step remediation recommendations")


# Schema for command safety evaluation in auto mode
class SafetySchema(BaseModel):
    """Schema for LLM judge command safety evaluation."""
    analysis: str = Field(description="Step-by-step analysis of what the command does")
    risk_level: Literal["safe", "caution", "dangerous"] = Field(description="Risk classification")
    safe: bool = Field(description="True if safe or caution, false if dangerous")
    reason: str = Field(description="One short sentence summary (max 10 words)")


# Schema for watch mode responses
class WatchResponseSchema(BaseModel):
    """Schema for watch mode AI responses."""
    has_actionable_feedback: bool = Field(
        description="True if there is something actionable to report, false if nothing noteworthy"
    )
    feedback: str = Field(
        default="",
        description="Actionable feedback/suggestions if has_actionable_feedback is true, empty string otherwise"
    )
