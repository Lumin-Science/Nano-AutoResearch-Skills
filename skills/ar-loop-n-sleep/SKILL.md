---
name: ar-loop-n-sleep
description: Continue iterative research in tmux while avoiding model-token use during long external runs. Read the original task and recent event log, advance until the run is healthy, then schedule a Codex queue wakeup at the next useful checkpoint.
---

# AR Loop & Sleep

Use this skill only inside tmux. Its job is to follow the user's research task,
sleep while external training runs, and wake the same Codex pane when useful work
should resume.

Keep exactly two persistent control files in the project:

- `.ar/PROMPT.md`: the task supplied with the first invocation,
  recorded verbatim rather than summarized.
- `.ar/events.tsv`: one concise event per row with columns
  `time_utc`, `state`, `handoff`, and `next_check_utc`.

## On every invocation

Read `PROMPT.md` and the last five rows of `events.tsv`, then inspect the real
project and run state.

- **First invocation:** create the two files, record the original task, and
  follow it until training is underway and healthy.
- **Previous run succeeded:** follow the original task through whatever should
  happen after that run, then continue until the next training run is underway
  and healthy.
- **Previous run failed:** inspect the failure and, following the original task,
  fix and retry or move on. Continue until training is underway and healthy.
- **Run still running:** if the estimated remaining time is under five minutes,
  wait and continue. Otherwise, use the exit procedure.

“Next” is defined only by the original task. Do not impose a particular
evaluation, analysis, or experiment-selection workflow.

## Exit procedure

Before a normal exit:

1. Confirm that training is running healthily.
2. Append one event whose handoff is one or two sentences describing the
   current step.
3. Estimate the next useful check time and convert it to a delay in seconds.
4. Resolve the current Codex session UUID, preferring an exported
   `CODEX_SESSION_ID` or `CODEX_THREAD_ID` if one exists. If Codex does not
   expose one, use the newest `session_id` in
   `${CODEX_HOME:-$HOME/.codex}/history.jsonl`; fall back to the newest `id` in
   `${CODEX_HOME:-$HOME/.codex}/session_index.jsonl`. If the session UUID cannot
   be resolved, record the blockage and ask the user to provide the session ID;
   do not schedule a blind terminal wake.
5. Start one background sleeper that queues the continuation into that Codex
   session:

```bash
pane="${TMUX_PANE:?invoke ar-loop-n-sleep from inside tmux}"
tmux display-message -p -t "$pane" '#{pane_id}' >/dev/null

thread_id="${CODEX_SESSION_ID:-${CODEX_THREAD_ID:-}}"
if [ -z "$thread_id" ]; then
  thread_id="$(python3 - <<'PY'
import json
import os
from pathlib import Path

base = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
for filename, key in (("history.jsonl", "session_id"), ("session_index.jsonl", "id")):
    path = base / filename
    if not path.exists():
        continue
    for line in reversed(path.read_text().splitlines()):
        try:
            value = json.loads(line).get(key)
        except json.JSONDecodeError:
            continue
        if value:
            print(value)
            raise SystemExit
PY
  )"
fi

test -n "$thread_id" || { echo "could not resolve Codex session UUID" >&2; exit 1; }
codex_bin="$(command -v codex)" || { echo "codex CLI is required for queue wakeups" >&2; exit 1; }

tmux run-shell -b \
  "sleep ${delay_seconds}; \
   '${codex_bin}' queue --thread '${thread_id}' --message 'Use \$ar-loop-n-sleep and continue.'"
```

Use `codex queue` for Codex wakeups. The older `tmux send-keys ... Enter`
sequence can type into the TUI without submitting a new round, so use it only
when queueing is unavailable and the user explicitly accepts a manual-submit
wakeup. Do not spend model turns polling a healthy run. Do not claim success,
fabricate healthy training, or schedule an unsafe continuation when work is
genuinely blocked.
