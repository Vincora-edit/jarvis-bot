# Skill Writer Reference

Detailed patterns, examples, and troubleshooting for creating Agent Skills.

## Common patterns

### Read-only Skill

```yaml
---
name: code-reader
description: Read and analyze code without making changes. Use for code review, understanding codebases, or documentation.
allowed-tools: Read, Grep, Glob
---

# Code Reader

## Instructions
1. Use Read to view file contents
2. Use Grep to search within files
3. Use Glob to find files by pattern
```

### Script-based Skill

```yaml
---
name: data-processor
description: Process CSV and JSON data files with Python scripts. Use when analyzing data files or transforming datasets.
---

# Data Processor

## Instructions

1. Use the processing script:
```bash
python scripts/process.py input.csv --output results.json
```

2. Validate output with:
```bash
python scripts/validate.py results.json
```
```

### Multi-file Skill

```yaml
---
name: api-designer
description: Design REST APIs following best practices. Use when creating API endpoints, designing routes, or planning API architecture.
---

# API Designer

Quick start: See [examples.md](examples.md)
Detailed reference: See [reference.md](reference.md)

## Instructions
1. Gather requirements
2. Design endpoints
3. Document with OpenAPI spec
4. Review against best practices
```

## Best practices

1. **One Skill, one purpose**: Don't create mega-Skills
2. **Specific descriptions**: Include trigger words users will say
3. **Clear instructions**: Write for Claude, not humans
4. **Concrete examples**: Show real code, not pseudocode
5. **List dependencies**: Mention required packages in description
6. **Test with teammates**: Verify activation and clarity
7. **Version your Skills**: Document changes in content
8. **Use progressive disclosure**: Put advanced details in separate files

## Troubleshooting

### Skill doesn't activate

- Make description more specific with trigger words
- Include file types and operations in description
- Add "Use when..." clause with user phrases
- Verify file location: `.claude/skills/skill-name/SKILL.md`

### Multiple Skills conflict

- Make descriptions more distinct
- Use different trigger words
- Narrow the scope of each Skill

### Skill has errors

- Check YAML syntax (no tabs, proper indentation)
- Verify file paths (use forward slashes)
- Ensure scripts have execute permissions
- List all dependencies

### YAML validation

Common issues:
- Missing opening or closing `---`
- Tabs instead of spaces
- Unquoted strings with special characters

Validate with:
```bash
cat SKILL.md | head -n 15
```

## File structure reference

```
skill-name/
├── SKILL.md (required)
├── reference.md (optional - detailed API docs)
├── examples.md (optional - extended examples)
├── scripts/
│   └── helper.py (optional - utilities)
└── templates/
    └── template.txt (optional - boilerplate)
```

## Frontmatter fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Lowercase, numbers, hyphens. Max 64 chars. Must match directory name. |
| `description` | Yes | Max 1024 chars. Include what AND when to use. |
| `allowed-tools` | No | Comma-separated list of allowed tools (Read, Grep, Glob, etc.) |

## Example descriptions

**PDF processing**:
```yaml
description: Extract text and tables from PDF files, fill forms, merge documents. Use when working with PDF files or when the user mentions PDFs, forms, or document extraction.
```

**Excel analysis**:
```yaml
description: Analyze Excel spreadsheets, create pivot tables, and generate charts. Use when working with Excel files, spreadsheets, or analyzing tabular data in .xlsx format.
```

**Git commits**:
```yaml
description: Generates clear commit messages from git diffs. Use when writing commit messages or reviewing staged changes.
```

**Code review**:
```yaml
description: Review code for best practices and potential issues. Use when reviewing code, checking PRs, or analyzing code quality.
```
