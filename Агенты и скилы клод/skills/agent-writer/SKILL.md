---
name: agent-writer
description: Create specialized AI agents for Claude Code in .claude/agents/ directory. Use when the user wants to create a new agent, add a team member agent (reviewer, architect, BA, QA, DevOps), or design a custom expert persona for code review, analysis, or specialized tasks.
---

# Agent Writer

Create specialized agents for Claude Code that act as expert team members with distinct personas and expertise.

## Quick start

Create an agent file in `.claude/agents/`:

```markdown
---
name: my-agent-name
description: When to use this agent with examples
model: sonnet
color: blue
---

You are an expert [role] with [X] years of experience...

## Your Expertise
- Point 1
- Point 2

## Your Process
1. Step 1
2. Step 2

## Output Format
Structure your response as...
```

## Agent file structure

### Frontmatter (required)

```yaml
---
name: lowercase-hyphenated-name    # Must match filename (without .md)
description: Detailed description   # Include <example> blocks for trigger scenarios
model: sonnet | opus | haiku        # opus for complex reasoning, sonnet for balanced, haiku for fast
color: blue | green | cyan | etc    # Visual identifier
---
```

### Description format

Include trigger examples in description:

```
description: Use this agent when [scenario]. Trigger this agent when:\n\n<example>\nContext: [situation]\nuser: "[user message]"\nassistant: "[how Claude should respond]"\n<commentary>[why use this agent]</commentary>\n</example>
```

### System prompt (after frontmatter)

Structure the agent persona:

1. **Identity**: Who they are (role, years of experience, background)
2. **Expertise areas**: Specific skills and knowledge domains
3. **Philosophy/Approach**: How they think and work
4. **Process/Framework**: Step-by-step how they analyze/review
5. **Output format**: Structured response template
6. **Project-specific context**: Relevant details about this codebase

## Common agent types

### Code Reviewer
- Focus: Code quality, bugs, security, best practices
- Model: `opus` (needs deep reasoning)
- Include: Project-specific patterns, coding standards

### Business Analyst
- Focus: Requirements, user stories, acceptance criteria, edge cases
- Model: `sonnet` (balanced)
- Include: Business context, user personas, success metrics

### SDET / QA Engineer
- Focus: Test coverage, test cases, edge cases, quality assurance
- Model: `sonnet`
- Include: Testing frameworks, coverage requirements

### Senior Architect
- Focus: System design, scalability, patterns, tech debt
- Model: `opus` (complex decisions)
- Include: Architecture principles, tech stack constraints

### DevOps Engineer
- Focus: CI/CD, infrastructure, deployment, monitoring
- Model: `sonnet`
- Include: Cloud platform, deployment process, environments

### Security Specialist
- Focus: Vulnerabilities, OWASP, auth, data protection
- Model: `opus` (security is critical)
- Include: Security requirements, compliance needs

## Instructions for creating agents

### 1. Clarify the role
- What specific expertise does this agent provide?
- When should Claude automatically invoke this agent?
- What model is appropriate (complexity vs speed)?

### 2. Write compelling persona
- Give them a rich background (companies, years, achievements)
- Make them relatable but authoritative
- Include their philosophy/approach to work

### 3. Define clear process
- Step-by-step framework they follow
- Checklist of what they evaluate
- Prioritization system (critical/important/nice-to-have)

### 4. Structure output format
- Use consistent headers and sections
- Include scoring/rating systems where appropriate
- End with actionable recommendations

### 5. Add project context
- Reference project-specific patterns from CLAUDE.md
- Include relevant tech stack details
- Mention coding standards and conventions

## Best practices

- **Be specific**: Vague personas give vague reviews
- **Include examples**: Show exactly when to trigger
- **Balance depth**: Detailed but not overwhelming
- **Project-aware**: Reference actual project patterns
- **Actionable output**: Every critique has a solution
- **Positive reinforcement**: Acknowledge what's done well

## File location

```
.claude/agents/
├── business-analyst.md
├── code-reviewer.md
├── devops-engineer.md
├── qa-engineer.md
├── security-specialist.md
└── senior-architect.md
```

For detailed examples, see [reference.md](reference.md).
