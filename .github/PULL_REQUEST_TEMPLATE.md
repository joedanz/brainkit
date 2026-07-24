## What and why

<!-- The diff shows what changed. Use this space for why it needed to. -->

## How it was verified

<!--
"Tests pass" is enough when tests cover it. If you checked something by hand,
say what you ran and what you saw — that's the part a reviewer can't reconstruct.
-->

## Checklist

- [ ] Tests cover the change (a fix has a test that failed before it)
- [ ] `uv run pytest` passes
- [ ] `uvx ruff check .` is clean
- [ ] Docs updated if a documented behavior changed — a flag, an exit code, a
      file layout, a permission rule

## Does this touch the privacy boundary?

<!--
Delete this section if not. Otherwise say which invariant you reasoned about.

resolver.py, compiler.py, writeback.py, and ingest.py decide what lands in a
person's vault and what may leave it. Changes there should extend
tests/test_leak_property.py or tests/test_search_leak_property.py rather than
only adding a case-by-case test — those assert the invariant itself, which is
what keeps holding when the implementation moves.
-->
