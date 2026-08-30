# Nano AR Skills

Three standalone [Agent Skills](https://agentskills.io/) for selectively adding the Nano AutoResearch workflow to Codex and other compatible agents. This repository is a skills collection, not a Codex plugin or an npm package.

## Skills

- `ar-kickstart` turns a rough scientific question into grounded scientific and evaluation contracts, then prepares a research formulation and visual-report packet.
- `ar-loop-n-sleep` advances long-running research in tmux and wakes the same pane at the next useful checkpoint instead of polling with model turns.
- `ar-meeting` opens PPTX, block-aware PDF, Markdown, or HTML presentations as a local click-to-ask review room with persistent feedback.

## Install

List the available skills:

```bash
npx skills add Lumin-Science/Nano-AutoResearch-Skills --list
```

Install one skill globally for Codex:

```bash
npx skills add Lumin-Science/Nano-AutoResearch-Skills --skill ar-kickstart -g -a codex
```

Replace `ar-kickstart` with `ar-loop-n-sleep` or `ar-meeting` to install either of those. Omit `--skill` to choose interactively or install the collection.

Project-local installation is the default; omit `-g` to keep a skill with the current project.
