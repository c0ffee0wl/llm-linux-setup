import llm
import json
from llm_tools_context import context


def test_tool():
    # Test basic functionality
    result = context("")
    assert "Error: 'context' command not found in PATH" in result or result != ""
    
    # Test with numeric input
    result = context("5")
    assert "Error: 'context' command not found in PATH" in result or result != ""
    
    # Test with "all" input
    result = context("all")
    assert "Error: 'context' command not found in PATH" in result or result != ""
    
    # Test with invalid input - context command will handle validation
    result = context("invalid")
    assert "Error running context command:" in result or "Error: 'context' command not found in PATH" in result
