# Zotero Cleanup with AI

Got a Zotero library that's turned into a junk drawer? This is a small,
transparent tool for cleaning it up with an AI assistant doing the
categorization thinking, while you stay in control of every write.

It grew out of using an AI assistant to reorganize a 180+ item, 25-collection
library that had been filed ad hoc for a couple of years. The pattern that
worked: dump the library structure to a file, hand it to an AI, get back a
proposed reorganization as a plan, review it, dry-run it, then apply it.
This repo packages that pattern up so anyone can reuse it.

## Why this exists

Zotero's own interface is great for adding and reading items, but doesn't
give you much help *reorganizing* a library that's already gotten messy —
that's a categorization problem, which is exactly the kind of thing an AI
assistant is good at, if it can actually see your library. This tool is
the bridge: it reads your library into a plain file an AI can reason about,
and writes an AI's proposed changes back out through a reviewable,
dry-run-first script instead of letting the AI touch your account directly.

## How it works

1. **Audit.** Run the script's `audit` command. It's read-only — it pulls
   every collection and every item in your library and writes two files:
   a JSON file (for a script or an AI to parse) and a Markdown file (for
   you to skim).
2. **Plan.** Show those two files to an AI assistant. Ask it things like
   "these three collections are basically the same topic, split into a
   better structure" or "find items that look like duplicates" or "these
   40 items have no collection at all, suggest where they belong." Ask it
   to write its answer as a plan JSON file, matching the schema documented
   in `zotero_cleanup.py`'s docstring (and shown in
   `examples/example_reorg_plan.json`).
3. **Review.** Read the plan yourself. It's just JSON — you don't need to
   trust the AI's judgment blindly, you can literally see every move it's
   proposing before anything happens.
4. **Dry run.** Run `apply-crosslist --plan your_plan.json` (no `--confirm`).
   It prints exactly what it would do without writing anything.
5. **Apply.** If the dry run looks right, run it again with `--confirm`.

Applying a plan is additive by default: adding an item to a new collection
never removes it from a collection it's already in (Zotero items can live
in more than one collection at once — that's a feature, not something to
work around). Anything genuinely destructive — deleting a specific item,
deleting a specific empty collection — only happens if it's explicitly
listed in the plan, and you'll have already seen it named in the dry run.

## Quick start

```bash
pip install -r requirements.txt

export ZOTERO_API_KEY="your-key-here"
export ZOTERO_USER_ID="your-numeric-user-id"

python3 zotero_cleanup.py verify     # confirm the key works
python3 zotero_cleanup.py audit      # read-only, writes zotero_library_audit.{json,md}

# ... hand the audit files to an AI assistant, get back a plan.json ...

python3 zotero_cleanup.py apply-crosslist --plan plan.json             # dry run
python3 zotero_cleanup.py apply-crosslist --plan plan.json --confirm   # actually apply it
```

Get your API key at [zotero.org/settings/keys](https://www.zotero.org/settings/keys) —
check "Allow library access," and "Allow write access" too if you intend
to ever pass `--confirm`. Your numeric user ID is on the same page, and
`verify` will flag it for you if it doesn't match what the key itself reports.

## What it does NOT do

- It doesn't merge true duplicate records (two genuinely identical items).
  Zotero's own **Duplicate Items** view (left sidebar, next to Trash) does
  this safely — it combines notes, tags, and collection membership from
  both copies. A script doing the same over the raw API risks silently
  dropping something, so this tool deliberately leaves that step to Zotero.
- It doesn't permanently empty your Trash. Deletions here (approved items,
  approved empty collections) send things to Trash the same way the Zotero
  app does; emptying Trash for good is left to you, in Zotero, on purpose.
- It doesn't fabricate anything about your items — it only reads what's
  already there and moves/labels things you (via your AI-drafted, human-
  reviewed plan) explicitly asked to move or label.

## Plan file schema

Full details are in the docstring at the top of `zotero_cleanup.py`.
Short version — a plan is one JSON object with any of these optional keys:

- `new_collections` — collections to create first
- `crosslist` — items to add to one or more collections (by name, or `NEW:<name>` for one just created), without removing from anywhere
- `delete_items` — specific item keys to delete (the one destructive list — keep it short and deliberate)
- `delete_empty_collections` — collections to delete by key
- `delete_collections_by_name` — collections to delete by exact name (safer for an AI to write than a raw key)
- `orphan_fix` — re-parent one or more standalone attachments under a newly created item (for the "I downloaded this book as five separate PDFs" case)

See `examples/example_reorg_plan.json` for a filled-in (fake-data) example.

## Safety notes

- Every write operation defaults to dry-run. You always have to add `--confirm` on purpose.
- The script never guesses a raw Zotero key for you — collection targets are matched by exact name, and if no match exists, a new collection gets created with that name rather than silently failing. Don't put a raw key where a name is expected.
- Nothing here touches your API key except reading it from an environment variable. Never commit it, never put it in a plan file.

## License

MIT — see `LICENSE`. Use it, fork it, adapt it for other reference managers if you want.
