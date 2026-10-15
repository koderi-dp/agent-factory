These are the unchanged v1 role files packaged for the new engine (plan §5.3).
The generic files come from `claude/agents/`; `.codex.md` files preserve the
different Codex instructions. The original directories remain for the v1 runtime
until session 4.3 removes it.

TODO (plan §5.3, §10.2, session 2.1): rewrite roles for packets, execution-keyed
report paths, required sections, and size budgets. Until then, `fast-approver`
is an unchanged copy of `approver` for each provider, so the Fast template can
be validated now. Session 2.1 supplies its Fast-specific checks and CHANGES
contract; session 2.2 implements the CHANGES loop.
