# Check or update Typewriter

This is an assistant-led procedure for an existing laptop installation. It uses the public starter at https://github.com/duhring/typewriter and preserves the user's local work. It is not an automatic updater; the assistant needs access to the installation and permission to run commands.

## Start-of-run invitation

At an open-ended run start, show:

> What would you like to do?
> - Start with a new idea
> - Continue saved work, if there is any
> - Check for Typewriter updates
> - End the session

For a fresh installation, omit “Continue saved work” unless actual user material exists. Offer the update option once at the start, without fetching or installing anything automatically. If the user already gave a concrete topic or task, begin that work and briefly mention that “check for Typewriter updates” is available; do not make the menu a prerequisite or repeat it during the run. A request to end the session closes immediately.

The assistant must not claim that an update exists before checking. Checking is optional and does not require the user to postpone creative work.

## Natural requests

Read-only check:

> Check whether my Typewriter installation has updates available. Inspect my local changes and tell me what would change. Do not install anything yet.

Authorized update:

> Update my Typewriter system from https://github.com/duhring/typewriter. Inspect my installation and local changes first, back up anything affected, and preserve my database, personal content, settings, and customizations. Apply the system updates, run the relevant checks, and explain what changed.

## Inspect before changing anything

1. Locate the actual installation. Read its `AGENTS.md`, identify the current version and Python environment, and inspect its Git status, branch and remotes. Do not assume that `origin` points to Typewriter; an established personal system may use a different repository. Do not print tokens or environment-file contents.
2. Obtain the public starter's current `main` in an isolated temporary checkout. Fetching source is not installing it. Compare the installed baseline with upstream and inspect the file diff and local customizations. Record the exact target commit; do not work from an unpinned moving target.
3. Separate system tools, workflow docs and tests from private material. Preserve `data/pka.db` and its companions, inboxes, media, wiki content, local memory, machine identity and environment files. Do not copy starter configuration over local settings or use a blanket directory replacement. Do not upload personal data or local changes to GitHub as part of an update.
4. A read-only request ends with what is current, what changes are available, and any integration issues. It does not apply the update or create new user preferences. If the user asks to update, continue with the steps below; an earlier explicit update request already authorizes the routine backup and integration work.

## Back up and prepare the integration

Create a timestamped local backup directory outside the installation and outside any tracked repository. Save the installed commit, status and local patches; copy affected files, including relevant untracked custom tools. Back up private configuration without exposing it in logs. Verify these copies before replacing any file.

If a database exists, use the sanctioned online SQLite backup tool with a new destination, rather than copying an open database:

```bash
PKA_BACKUP_DIR="/absolute/path/to/new-update-backup/database" bin/pka backup_pka_db
```

Use a new backup destination for this update so the tool's dated naming and retention do not overwrite or prune earlier backups. Verify the tool reports an intact backup. If no database exists yet, record that fact rather than treating the fresh installation as missing accumulated work.

For a clean starter clone on its expected branch with no divergent commits, a reviewed fast-forward can be appropriate. Before `git merge --ff-only` or an equivalent pull, ensure that incoming tracked files cannot collide with untracked personal files and that local settings are protected. A dirty worktree, personal fork, divergent history, different remote, or ZIP installation needs staged integration, not a blind pull.

Prepare the integration in a separate checkout. For each conflicting system file, carry local customizations forward deliberately and inspect the merged result. Use the installed baseline when available for a three-way comparison. Do not infer an unknown baseline. Preserve owner-authored instructions; a conflict that changes their meaning needs the owner's decision on the concrete alternatives. Never use hard reset, force push, destructive cleanup or an automatic stash to get past local differences.

## Verify, apply, and leave a receipt

Run checks appropriate to the changed files in the staged integration. Use `bin/pka check` and `bin/pka test` when system code or its runtime changes. Run `tests/acceptance/all.sh` only in a disposable checkout: it creates fixture projects and approval records and must not be run against the user's live database. Update dependencies through the documented installer only when the change requires it; do not rebuild a working environment merely to refresh documentation.

Apply only the reviewed system files or reviewed fast-forward. Do not transplant the disposable test database or its generated artifacts. If staging or verification fails, preserve the current installation and explain the failure; do not report a partial update as complete.

After applying, verify the installed version and relevant checks. Save and index a concise update receipt under `owners-inbox/setup-notes/` via `bin/pka pka_index index-markdown --file <receipt> --category reference`. Include the previous and target commits (or a file manifest when the installation lacks Git history), changed system paths, backup location, checks and results, retained customizations, and any unresolved issues. Explain what new capabilities are available without claiming that the user's preferences or history have been changed.

Offer to return to the intended run: start a new idea, continue actual saved work, or end the session. Do not start a creative task automatically.

## Limits

Older installations do not learn this menu remotely. Their users must request an update once or read these instructions; subsequent runs can offer the option after the new contract is installed. Network or permission failures mean “could not check,” not “up to date.” This procedure does not publish, synchronize personal repositories, or schedule background updates.
