---
description: Quick parallel review with Developer + Code Reviewer + SDET (faster than full-review)
---

# Quick Review

Fast review process using 3 key agents in parallel.

## Your Task

1. **Understand context**: What is being reviewed?

2. **Launch 3 agents IN PARALLEL** (single message, `run_in_background: true`):

- developer: "Review implementation for: [context]. Check code quality and patterns."
- senior-tech-lead-reviewer: "Code review for: [context]. Check security, performance, bugs."
- sdet-engineer: "Quick test assessment for: [context]. Key test cases needed."

3. **Collect and synthesize**:

```markdown
# ⚡ Quick Review Report

## Summary
[1-2 sentences]

## 💻 Developer
- Code quality: ✅/⚠️/❌
- Key points: ...

## 🔍 Code Review
- Security: ✅/⚠️/❌
- Performance: ✅/⚠️/❌
- Score: X/10

## 🧪 Testing
- Coverage needed: ...
- Critical tests: ...

## Action Items
1. [Critical]
2. [Important]
3. [Nice to have]

## Ready? [YES/NO]
```

## Arguments
$ARGUMENTS - What to review
