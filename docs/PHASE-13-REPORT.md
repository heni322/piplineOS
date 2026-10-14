# Phase 13 — Contact tasks queue: Report (2026-10-14)

## 1. Why
In free-only mode, a decision-maker found on the register without a published address becomes a human task
("reach them through the company's published channels"). So do prospects whose country rules allow only other
channels. Before this phase the operator console showed only a **count** of these tasks: nobody could see, work or
close them.

## 2. What changed
- **API** (`app/api/v1/tasks.py`, operator only):
  - `GET /api/v1/tasks?status=open|done|skipped|all` lists each task with:
    - the company, register number and website;
    - the masked contact name and job title;
    - the trigger and its source link;
    - the channel and the instructions.
  - `POST /api/v1/tasks/{id}/complete` closes a task once (`done` or `skipped`, with a note).
- **Page** "Contact tasks" in the operator console:
  - a "Show name" button reuses the existing logged unmask, which asks for a reason and logs every view;
  - links to the register entry, the website and the trigger's source;
  - close with an outcome and a note.
- **The note stays with the task**, under row-level security (migration 0017). The append-only audit log records
  only the outcome. Free text there could otherwise fix personal data in place for good.
- **No `javascript:` links from outside data.** The Trigger Hunter now drops any signal whose link is not
  `http`/`https`. Job boards return their own URLs, so this is a real input. The new page also renders web links
  only.

## 3. Results
- Backend: 4 new tests. They cover:
  - masked listing;
  - close-once;
  - the note kept out of the audit log;
  - client users and anonymous callers refused;
  - hostile links dropped.
- Frontend: lint, types, unit tests and build pass. A new full-stack browser test signs in, opens Contact tasks,
  reveals a name (logged), closes a task, and runs the WCAG 2.1 AA checks before and after.

## 4. Suggested next
- The existing `CitedLetter` and Approvals pages render citation links without a scheme check. The new hunter
  guard protects new signals; a front-end check there would add defence in depth.
