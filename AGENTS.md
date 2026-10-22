# Working on RCP Lab

Use Python 3.12. Read the touched callers and tests before editing. Keep the change minimal and preserve validation, isolation and error reporting.

Never commit real environment files, passwords, model weights, runtime missions, conversation history, server inventories or logs. Use an isolated RCP_DATA for tests. Do not run generated Python in the application process: all candidates must use evaluator.sandbox. Preserve the external acceptance gate and old regression tests.

Run the unittest command in README and JavaScript syntax checks after relevant edits. Do not report model-generated tests as independent proof of correctness. A queued instruction is not a completed feature; distinguish attempted, accepted, deferred and failed work.
