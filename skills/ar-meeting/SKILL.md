---
name: ar-meeting
description: Open a generated PPTX, PDF, Markdown, or HTML presentation as a local interactive slide meeting with click-to-ask pins, independent forked Codex threads, per-message model and reasoning controls, persistent local logs, and an exportable slide-edit brief. Use after AutoResearch or any report-building task when the user wants to review, question, discuss, present, understand, or collect revision feedback on a deck without returning to the original Codex UI. Do not use to create or silently edit a deck.
---

# AR Meeting

Open the presentation locally and let the user question it in place. Treat the user as the supervisor and answer as the researcher who produced the work: concise, evidence-backed, and honest about gaps.

## Prepare

1. Locate the user-specified presentation, or the most recent `.pptx`, `.pdf`, `.md`, or `.html` report in the project.
2. Convert it:

   ```bash
   python3 <skill>/scripts/convert.py <presentation>
   ```

   With no `--out`, the converter writes a stable per-source room under
   `${LABMEET_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/ar-meeting}`.
   Use the absolute room path printed by the converter for every later command.
   This keeps generated HTML, slide images, logs, and feedback exports out of the
   project tree so Git cannot mistake meeting artifacts for source files. Pass an
   explicit `--out <dir>` only when the user asks for another location.

   PPTX conversion prefers LibreOffice plus Poppler for a faithful slide image and falls back to the bundled OOXML renderer. Report the fallback because charts and masters may be approximate.
3. Ensure `pack.md` exists beside the generated `index.html`. Summarize the project and map each slide claim to file, result, paper, or URL evidence. Include known limitations and the locations of useful source artifacts. Keep pointers denser than prose.

## Open the room

Run from the originating Codex task so `CODEX_THREAD_ID` is captured:

```bash
python3 <skill>/scripts/labmeet.py open <room>
```

Tell the user the local room is open. They can click anywhere on a slide, optionally select text first, choose an available Codex model and supported reasoning effort, ask a question, and continue inside that pin's tab. The right panel is resizable.

Each new spatial pin lazily creates its own persisted Codex child task with App Server `thread/fork`; follow-ups in that pin reuse its child. Thus each comment tab has independent model history while inheriting the task that launched the room. The original task is never resumed or mutated. Every turn receives the slide anchor, extracted slide text, `pack.md`, and bounded pin history and runs read-only. The browser discovers models with `model/list` and applies the user's model and reasoning-effort selection as `turn/start` overrides. Fork identities are stored in `.state/agent.json`.

If the skill is launched outside Codex and no parent task id exists, the configured fresh `codex exec` fallback answers from the deck and pack. The browser status must make that fallback visible; never describe it as a fork.

## During and after the meeting

- Questions, answers, coordinates, quotes, resolutions, model/effort selections, and follow-ups are appended to `.state/threads.jsonl` immediately. A crash or browser close must not lose them. Stopping and reopening the same meeting directory restores every pin and transcript. Re-converting the same source to that same output directory keeps `.state/`; a different meeting directory has a separate history.
- Inspect the meeting with `labmeet.py threads <room> [--json]`.
- Export at any time with the browser's **Export** button or `labmeet.py export <room> [--out <base>]`. This writes `slide-feedback.md` for the next slide-editing agent and `slide-feedback.json` for lossless automation, including slide number/topic, percentage anchor, selected quote, resolution state, complete transcript, and model/effort metadata. Ending a meeting exports automatically.
- Use `labmeet.py poll` and `labmeet.py reply` only when the automatic answerer is unavailable.
- Do not edit research files merely because a clarification was asked. Treat requested changes as proposed follow-up work.
- When the user ends the meeting, treat `slide-feedback.md` as the canonical revision handoff. Add `minutes.md` only when a decision/action summary beyond the per-slide transcript is useful. Keep the interaction log and child tasks resumable for future work.

Everything stays on localhost and local disk. Never publish or upload the deck.
