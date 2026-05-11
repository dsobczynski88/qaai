---
name: code-review
description: Structured code review following team standards. Use when reviewing code or checking pull requests for security, error handling, performance, code quality, and testing issues.
---

Walk the code under review through each section below in order and report findings inline.

### 1. Security (always first)
- SQL/NoSQL injection, unvalidated input, missing auth, secrets in code, CORS issues

### 2. Error Handling
- Async operations in try/catch, errors logged with context, consistent error format, no empty catch

### 3. Performance
- N+1 queries, missing indexes, unbounded queries, large payloads without pagination

### 4. Code Quality
- Functions under 30 lines, no magic numbers, no `any` types, dead code removed

### 5. Testing
- Is this testable? Suggest specific test cases if none exist.

For each finding: **Severity** (CRITICAL/WARNING/SUGGESTION), **Location** (file:line), **Issue**, **Fix**
