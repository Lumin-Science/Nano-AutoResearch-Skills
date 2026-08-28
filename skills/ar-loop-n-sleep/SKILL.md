---
name: ar-loop-n-sleep
description: Continue iterative research in tmux while avoiding model-token use during long external runs. Read the original task and recent event log, advance the work until training is healthy, then schedule a delayed wake in the same pane.
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
4. Start one background sleeper that wakes this exact tmux pane:

```bash
pane="${TMUX_PANE:?invoke ar-loop-n-sleep from inside tmux}"
tmux display-message -p -t "$pane" '#{pane_id}' >/dev/null
tmux run-shell -b \
  "sleep ${delay_seconds}; \
   tmux send-keys -t '${pane}' -l -- 'Use \$ar-loop-n-sleep and continue.'; \
   tmux send-keys -t '${pane}' Enter"
```

Send the wake message and `Enter` separately. Do not spend model turns polling
a healthy run. Do not claim success, fabricate healthy training, or schedule an
unsafe continuation when work is genuinely blocked.
