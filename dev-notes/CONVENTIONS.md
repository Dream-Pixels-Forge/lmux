# Conventions 

> **Status:** STRICT MANDATORY.


## 1. TDD per increment (RED → GREEN)

1. Write the contract test **first**.
2. **Run it and observe the failure** — that is the RED evidence.
3. Implement the minimum to make it pass.
4. Re-run every gate.

Never write code first and backfill a test. Never comment out, `.skip()` or
`xfail()` a test to get green.

Deliver: commit → push → PR → review → merge

## 2. Anti-drift scope rule
Record every deferral in `dev-notes/PROGRESS.md` under `DEFERRED` /
`out of scope`. Never silently skip.

## 3. Git safety

Before switching branches:

1. `git rev-list --count main..HEAD` and `HEAD..main` — is it diverged?
2. `git diff --name-only HEAD main -- <paths you touched>` — any overlap?
3. If there is overlap, **stop** and reconcile first.

Never clobber unrelated work. Never merge without review. Never merge a
branch with failing or skipped tests.

**Non-negotiable merge condition:** every CI check green, zero lint warnings,
no unresolved review threads.

### 4. Branch hygiene (adopted 2026-10-05)

Squash-merge leaves the original branch commits as non-ancestors of `main`, so
stale branches pile up invisibly — the repo carried **38** of them before this
rule, and `git branch -d` could only prove 26 were merged.

1. **Always merge with** `gh pr merge <n> --squash --delete-branch`. Never
   plain `git merge` + manual cleanup.
2. **Prune after every merge:** `git fetch --prune origin`.
3. **Prune local branches** with `git branch -d` (never `-D` blindly) —
   `-d` refuses anything not provably merged, which is the safety net.
4. When auditing whether a branch still holds work, do **not** trust
   `git log main..<branch>` or `git cherry` on a squash-merged repo: both
   report false positives. Prove it with **hard evidence** — a merged PR for
   that head branch, or a distinctive symbol present in the current tree.




