# Workflow: Plan → Code → Test → Document → Merge

This repo is a fork of OpenAI Codex. Its one goal, in the owner's words (11-09):

> "Basically we have persistent agents and then agents that spin up on the fly. They can communicate within codex and
> everything works together in a hive mind type atmosphere. The human can use codex or codex desktop to get into the
> system and manage it."

Every change walks the five steps below, in order. No step is skipped. Nothing else is built.

## 1. Plan

Before any code, write ONE plan file at `plans/<NNN>-<slug>.md`, no longer than a page. It contains:
- **Goal:** one sentence, tied to the goal above.
- **Smallest version:** the least code that meets it.
- **Will not build:** what is out of scope.
- **Files:** each file to add or change.
- **Proof:** the exact command or test that shows it works.

**The owner approves the plan before code starts.** A plan that adds a new subsystem, rebuilds something Forge already
does, or edits upstream Codex crates beyond a hook needs his explicit yes.

## 2. Code

- Work on a branch `plan/<NNN>-<slug>`, off `main`.
- Change only the files the plan names. If another file must change, update the plan and get the owner's OK first.
- Prefer upstream Codex features (message board, agent roles, subagents) over new code.
- Commit as you go, with small commits.

## 3. Test

- Run the plan's proof command, and paste its output in the plan file under "Proof result".
- Run the repo's existing tests for every crate you touched (`cargo test -p <crate>`).
- A failure is fixed in this change, not filed for later.

## 4. Document

- Update `docs/` for anything a user or an agent will do differently.
- Add one line to `CHANGELOG-FORK.md`: the date, the plan number and what changed.

## 5. Merge requirements

A branch merges into `main` only when ALL of these hold:
1. The plan file exists and is marked approved by the owner.
2. Its proof result shows a pass, from this branch's head.
3. The tests of every touched crate pass.
4. The docs and the changelog line are in the branch.
5. `git diff main...HEAD --stat` touches only the files the plan names.
6. The merge is a merge commit (no squash), made by the owner or by the agent he names. Delete the branch after the merge.

## Rules for agents

- Never create tickets, backlogs or "evidence" folders. The plan file is the only record.
- Never rebuild Forge's workflow inside this repo. Forge stays the authority for UCSOS work.
- When stuck, stop and ask the owner. Do not build a mechanism around the problem.
