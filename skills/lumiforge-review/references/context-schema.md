# Context Schema

The invoking Agent writes one UTF-8 JSON object. Use only facts visible in the current conversation or workspace evidence.

```json
{
  "title": "Skill-first build records",
  "goal": "Let a nontechnical user create project history through natural language",
  "summary": "The project added checkpoint and version report generation while keeping the CLI as hidden infrastructure.",
  "conversation_id": "optional-visible-conversation-id",
  "decisions": [
    {
      "decision": "Use a Skill as the primary product entry",
      "reason": "The user should not need terminal knowledge",
      "status": "accepted"
    }
  ],
  "problems": [
    {
      "problem": "File modification counts were mistaken for engineering progress",
      "resolution": "Connect intent, approach, diff, and verification evidence",
      "status": "resolved"
    }
  ],
  "verification": [
    {
      "command": "python3 -m unittest discover -s tests -v",
      "result": "All tests passed",
      "status": "passed"
    }
  ],
  "next_steps": [
    {
      "text": "Use the Skill across three separate conversations"
    }
  ],
  "confidence_note": "Conversation sync was available; browser behavior was not checked."
}
```

## Field Rules

- `title`, `goal`, and `summary` are strings.
- `conversation_id` is optional. Include it only when the host exposes a stable visible ID.
- `decisions`, `problems`, `verification`, and `next_steps` are lists of strings or small objects.
- `confidence_note` names missing evidence, inference, or verification gaps.
- Do not paste the full conversation into any field.
- Do not write hidden chain-of-thought or speculate about the Agent’s internal reasoning.
