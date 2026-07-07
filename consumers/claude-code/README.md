# Claude Code consumer: the `/coco-loop` skill

`coco-loop/SKILL.md` is a Claude Code skill that turns a plain-language goal into a
governed, propose-only loop. It compiles the goal into a charter, has the person
confirm it, then arms and runs the loop through the `coco-loops` CLI.

## Install

The skill needs to live in your Claude Code skills directory. Symlink it (so repo
updates flow through) or copy it:

```bash
# symlink (recommended)
ln -s "$(pwd)/consumers/claude-code/coco-loop" ~/.claude/skills/coco-loop

# or copy
cp -R consumers/claude-code/coco-loop ~/.claude/skills/coco-loop
```

`coco-loops` must be on your PATH (`pip install -e .` from the repo root). Verify:

```bash
coco-loops --help
```

Then in Claude Code, invoke `/coco-loop` and state your goal, for example:
"keep my frontend build green for the next 4 hours."

## Naming

It is `coco-loop`, not `loop`, to avoid colliding with Claude Code's built-in `/loop`
(a recurring-interval runner) and the `ralph-loop` plugin. Those are different tools;
this one arms a constitution-governed loop.

## What it does not do

The skill never bypasses governance. It cannot arm a loop above L1, and the
`coco-loops charter` command rejects any charter that would contract around the
immutable constitution floor. Installation into `~/.claude/skills` is a deliberate,
attended step; nothing here modifies your environment on its own.
