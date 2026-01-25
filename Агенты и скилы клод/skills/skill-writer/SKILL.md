---
name: skill-writer
description: Create Agent Skills for Claude Code with proper structure and validation. Use when the user wants to create, write, or design a new Skill, needs help with SKILL.md files, frontmatter, or troubleshooting skill discovery.
---

# Skill Writer

Create well-structured Agent Skills for Claude Code that follow best practices and validation requirements.

## Quick start

Create a minimal Skill:

```bash
mkdir -p .claude/skills/my-skill
```

```yaml
---
name: my-skill
description: What it does. Use when [trigger conditions].
---

# My Skill

## Instructions
1. Step one
2. Step two
```

## When to use

- Creating a new Agent Skill
- Writing or updating SKILL.md files
- Designing skill structure and frontmatter
- Troubleshooting skill discovery issues
- Converting existing prompts into Skills

## Instructions

### 1. Determine scope

**Ask clarifying questions**:
- What specific capability should this Skill provide?
- When should Claude use this Skill?
- What tools or resources does it need?
- Personal use or team sharing?

**Keep it focused**: One Skill = one capability
- **Good**: "PDF form filling", "Excel data analysis"
- **Too broad**: "Document processing", "Data tools"

### 2. Choose location

**Personal Skills** (`~/.claude/skills/skill-name/`):
- Individual workflows and preferences
- Experimental Skills

**Project Skills** (`.claude/skills/skill-name/`):
- Team workflows (committed to git)
- Project-specific expertise

### 3. Write frontmatter

```yaml
---
name: skill-name
description: Brief description of what this does and when to use it
---
```

**Requirements**:
- `name`: lowercase, numbers, hyphens only (max 64 chars), must match directory name
- `description`: max 1024 chars, include BOTH what it does AND when to use it

**Optional**: `allowed-tools` to restrict tool access:
```yaml
allowed-tools: Read, Grep, Glob
```

### 4. Write effective description

**Formula**: `[What it does] + [When to use it] + [Key triggers]`

**Good**:
```yaml
description: Extract text and tables from PDF files, fill forms, merge documents. Use when working with PDF files or when the user mentions PDFs, forms, or document extraction.
```

**Too vague**:
```yaml
description: Helps with documents
```

**Tips**:
- Include file extensions (.pdf, .xlsx, .json)
- Mention common user phrases ("analyze", "extract", "generate")
- Add context clues ("Use when...")

### 5. Structure content

```markdown
# Skill Name

Brief overview.

## Quick start
Simple example to get started.

## Instructions
Step-by-step guidance for Claude.

## Examples
Concrete usage examples.

## Requirements
Dependencies or prerequisites.
```

### 6. Add supporting files (optional)

For complex Skills, use progressive disclosure:

```
skill-name/
├── SKILL.md (required)
├── reference.md (detailed docs)
├── examples.md (extended examples)
└── scripts/ (utilities)
```

Reference from SKILL.md: `For details, see [reference.md](reference.md).`

### 7. Test the Skill

1. Restart Claude Code to load the Skill
2. Ask relevant questions matching the description
3. Verify Claude uses it automatically

### 8. Debug if needed

If Claude doesn't use the Skill:
- Make description more specific with trigger words
- Check file location: `.claude/skills/skill-name/SKILL.md`
- Validate YAML syntax: `cat SKILL.md | head -n 10`
- Run debug mode: `claude --debug`

## Validation checklist

- [ ] Directory name matches frontmatter `name`
- [ ] Name is lowercase, hyphens only, max 64 chars
- [ ] Description < 1024 chars, includes "what" and "when"
- [ ] YAML frontmatter is valid (no tabs)
- [ ] Instructions are step-by-step
- [ ] Examples are concrete

For detailed patterns and examples, see [reference.md](reference.md).

## Output format

When creating a Skill, I will:
1. Ask clarifying questions about scope
2. Suggest name and location
3. Create SKILL.md with proper frontmatter
4. Include clear instructions and examples
5. Add supporting files if needed
6. Validate against all requirements
