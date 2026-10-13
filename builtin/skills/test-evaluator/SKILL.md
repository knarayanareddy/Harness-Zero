---
name: test-evaluator
description: Comprehensive test suite execution, regression analysis, coverage checks, and self-healing bug validation.
version: 1.0.0
---

# Test Evaluator Playbook

## Purpose
Use this skill to design regression test suites, run test suites safely in sandboxed environments, and analyze failure logs.

## Methodology
1. **Discover Test Framework**: Detect pytest, vitest, cargo test, or go test.
2. **Execute In Sandbox**: Run tests with timeouts and sanitized environment variables.
3. **Parse Stderr & Traces**: Extract precise line numbers and assertion mismatches.
4. **Self-Healing Loop**: Feed failures back to the editor loop until tests pass.
