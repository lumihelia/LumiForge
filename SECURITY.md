# Security and privacy

LumiForge is local-first, but its local output is sensitive by default. Conversation messages, file diffs, tool input, command output, source paths, and verification logs may appear in `.lumiforge/` or `build-history/`.

## Before sharing

- Do not publish `.lumiforge/`.
- Review every generated Build Record before sharing it.
- Keep `build-history/` out of source control unless you have deliberately removed private content.
- Never record `.env`, private keys, credentials, or access tokens.

LumiForge excludes common secret files from content capture, but this is a safeguard rather than a complete secret scanner.

## Reporting a vulnerability

Open a GitHub security advisory for vulnerabilities that could expose private evidence or execute unintended commands. Do not include real credentials, private transcripts, or other users' data in a public issue.
