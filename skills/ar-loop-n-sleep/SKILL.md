---
name: ar-loop-n-sleep
description: Continue iterative research in tmux while avoiding model-token use during long external runs. Read the original task and recent event log, advance until the run is healthy, then schedule a tmux-only wakeup at the next useful checkpoint.
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
4. Start one background sleeper that wakes this exact tmux pane using a
   dependency-free tmux bridge: capture the pane before acting, type the wake
   message literally, capture again to verify it landed, then send `Enter`
   after a short delay.

```bash
pane="${TMUX_PANE:?invoke ar-loop-n-sleep from inside tmux}"

tmux display-message -p -t "$pane" '#{pane_id}' >/dev/null

wake_script="$(mktemp "${TMPDIR:-/tmp}/ar-loop-n-sleep-wake.XXXXXX")"
cat >"$wake_script" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

delay_seconds="$1"
pane="$2"
message='Use $ar-loop-n-sleep and continue.'

sleep "$delay_seconds"

tmux display-message -p -t "$pane" '#{pane_id}' >/dev/null
tmux capture-pane -t "$pane" -p -J -S -20 >/dev/null

tmux send-keys -t "$pane" -l -- "$message"
sleep 0.2

if ! tmux capture-pane -t "$pane" -p -J -S -20 | grep -F -- "$message" >/dev/null; then
  echo "ar-loop-n-sleep: wake text was not visible in pane $pane; not pressing Enter" >&2
  exit 1
fi

tmux send-keys -t "$pane" Enter
SH

chmod +x "$wake_script"
tmux run-shell -b "'$wake_script' '${delay_seconds}' '$pane'; rm -f '$wake_script'"
```

This intentionally follows the tmux-bridge pattern from smux without depending
on `tmux-bridge`: read the target pane, type literal text, read back to verify,
then send keys. Do not use `codex queue` in this skill. Do not spend model turns
polling a healthy run. Do not claim success, fabricate healthy training, or
schedule an unsafe continuation when work is genuinely blocked.
