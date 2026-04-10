# Project audit (2026-04-10)

## Scope
This audit checks:
1. Current repository contents.
2. Historical deletions in git.
3. Whether currently deleted modules are still referenced in active code.

## Current top-level Python/module snapshot
Current top-level project files include:
- main.py
- extractor.py
- ai_analyzer.py
- nascent_checker.py
- prebid_generator.py
- tracker.py
- gdrive_sync.py
- submission_generator.py
- ocr_engine.py
- chatbot.py
- doc_generator.py

## What was deleted historically
From `git log --diff-filter=D`, the repository history shows these files were deleted at least once:

### Deleted once
- config_patch.py (2026-03-28, commit 892ea07)
- main_extra.py (2026-03-28, commit 3081c35)
- portal_watcher.py (2026-04-02, commit ce421cc)
- form_filler.py (2026-04-02, commit 6f9e08f)
- letterhead_manager.py (2026-04-02, commit 9e6102a)
- post_award.py (2026-04-02, commit ad9ad0d)
- post_bid_tracker.py (2026-04-02, commit 228ecb5)
- pdf_merger.py (2026-04-02, commit 1e73d7a)
- corrigendum_analyzer.py (2026-04-02, commit 7ec0519)
- indian_tender_guidelines.py (2026-04-02, commit 5d9714d)
- technical_proposal_generator.py (2026-04-02, commit 58946a6)
- submission_doc_generator.py (2026-04-02, commit 86ff447)
- gdrive_sync.py (2026-04-02, commit 17b9eea)

### Deleted multiple times (cleanup/repeated removal commits)
- sync_manager.py (latest deletion 2026-04-09, commit 5c3d89f)
- t247_downloader.py (latest deletion 2026-04-09, commit 5c3d89f)
- __pycache__/drive_manager.cpython-312.pyc (3 deletions)
- __pycache__/main.cpython-312.pyc (3 deletions)
- __pycache__/nascent_checker.cpython-312.pyc (3 deletions)
- __pycache__/sync_manager.cpython-312.pyc (3 deletions)

## Important nuance: deleted != permanently missing
`gdrive_sync.py` was deleted in commit 17b9eea, but it exists in the current working tree and is imported by `main.py`, so this module was reintroduced later.

## Missing-feature risk check (reference scan)
A scan for references to the historically deleted modules in active Python files shows only one match:
- `main.py` references `gdrive_sync`.

No active references were found for:
- sync_manager
- t247_downloader
- submission_doc_generator
- technical_proposal_generator
- indian_tender_guidelines
- corrigendum_analyzer
- pdf_merger
- post_bid_tracker
- post_award
- letterhead_manager
- form_filler
- portal_watcher
- main_extra
- config_patch

This suggests most removed files were intentionally decommissioned (or replaced) rather than currently broken imports.

## Quick health check executed
- `python -m py_compile *.py` completed successfully (no syntax/import-time compilation failures for top-level Python files).

## Recommendation
If you believe business features are missing (not just files), next step should be a commit-range feature diff (e.g., compare route/function inventory before and after 2026-04-02 and 2026-04-09) to identify capability loss even where imports do not break.
