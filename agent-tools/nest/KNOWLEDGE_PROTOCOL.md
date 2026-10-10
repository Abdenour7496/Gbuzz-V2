## Knowledge brain protocol (GCOR)

This workspace has a shared, human-approved knowledge brain. Follow the
`gcor-knowledge` skill (`.agents/skills/gcor-knowledge/SKILL.md`):

1. **Ask first** — before investigating a question run
   `node "C:/Gbuzz/agent-tools/gcor-agent/dist/gcor-agent.mjs" ask --channel <uuid> "<question>"`
   and use cited approved knowledge when it exists.
2. **Propose what you learn** — when a task yields a durable decision, root
   cause, procedure or fact, reply to the evidence message(s) with
   `!knowledge propose <Title> | <finding>`. Humans approve; agents never do.
3. **Real line breaks** — send multi-line messages with `--content -` and
   stdin, never `\n` inside a quoted string.
