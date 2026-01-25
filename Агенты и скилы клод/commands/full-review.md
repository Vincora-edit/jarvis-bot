---
description: Run comprehensive parallel review with all team agents (BA, Architect, Developer, SDET, DevOps, Reviewer) and get consolidated report from Team Lead
---

# Full Team Review

You are orchestrating a comprehensive parallel review process. The user wants all specialized agents to review the current work simultaneously, then receive a consolidated report.

## Your Task

1. **Understand the context**: What is being reviewed? (Look at recent changes, files mentioned, or ask if unclear)

2. **Launch agents IN PARALLEL** using Task tool with `run_in_background: true`:

```
Launch these agents SIMULTANEOUSLY in a single message with multiple Task tool calls:

- business-analyst: "Review the requirements and acceptance criteria for: [context]. Identify gaps, edge cases, and unclear requirements."

- senior-architect: "Review the architecture and design for: [context]. Assess scalability, patterns, and technical debt."

- developer: "Review the implementation approach for: [context]. Check code quality, patterns, and potential issues."

- sdet-engineer: "Create test plan for: [context]. Define test cases, edge cases, and quality metrics."

- devops-engineer: "Review deployment and operational concerns for: [context]. Check infrastructure, monitoring, and rollback."

- senior-tech-lead-reviewer: "Deep code review for: [context]. Check security, performance, maintainability."
```

3. **Collect results** using AgentOutputTool for each agent

4. **Synthesize with team-lead perspective** and deliver consolidated report:

```markdown
# 📊 Full Team Review Report

## Executive Summary
[2-3 sentence overview of findings across all perspectives]

## 📋 Business Analysis (BA)
[Key findings from business-analyst]
- Requirements: ✅/⚠️/❌
- Edge cases identified: ...
- Questions: ...

## 🏗️ Architecture (Architect)
[Key findings from senior-architect]
- Design: ✅/⚠️/❌
- Scalability concerns: ...
- Recommendations: ...

## 💻 Implementation (Developer)
[Key findings from developer]
- Code quality: ✅/⚠️/❌
- Patterns: ...
- Suggestions: ...

## 🧪 Testing (SDET)
[Key findings from sdet-engineer]
- Test coverage: ✅/⚠️/❌
- Critical test cases: ...
- Quality risks: ...

## 🚀 Operations (DevOps)
[Key findings from devops-engineer]
- Deployment: ✅/⚠️/❌
- Monitoring: ...
- Concerns: ...

## 🔍 Code Review (Tech Lead)
[Key findings from senior-tech-lead-reviewer]
- Security: ✅/⚠️/❌
- Performance: ✅/⚠️/❌
- Overall: X/10

## 🎯 Consolidated Action Items

### 🚨 Critical (Must Fix)
1. [Item from any agent]
2. ...

### ⚠️ Important (Should Fix)
1. [Item]
2. ...

### 💡 Suggestions (Nice to Have)
1. [Item]
2. ...

## ✅ Ready to Ship?
[YES/NO with reasoning]

## Next Steps
1. [Prioritized action]
2. [Next action]
3. ...
```

## Important Notes

- Launch ALL agents in PARALLEL (single message, multiple Task calls, all with `run_in_background: true`)
- Wait for all agents to complete before synthesizing
- If an agent fails, note it in the report but continue
- Be concise - extract key insights, not full agent outputs
- Prioritize actionable items
- Give clear YES/NO on readiness to ship

## Arguments

$ARGUMENTS - Optional context about what to review. If empty, review recent changes or ask the user.
