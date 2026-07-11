#!/usr/bin/env python3
"""
zotero_cleanup.py — clean up a messy Zotero library with an AI assistant's help

Run this LOCALLY (not inside a cloud sandbox) — it needs normal internet
access to reach api.zotero.org.

THE WORKFLOW THIS IS BUILT FOR

  1. Run `audit` (read-only). It dumps your entire library structure —
     every collection and every top-level item, with titles, authors,
     dates, tags, and current collection membership — into two files:
     zotero_library_audit.json (for a script or an AI to read) and
     zotero_library_audit.md (for you to skim).

  2. Hand those two files to an AI assistant (Claude, or anything else
     that can read a JSON file and reason about it) and ask it to
     propose a reorganization: new collections to create, which items
     should move where, which look like duplicates or junk, which
     collections are empty and safe to delete.

  3. The AI writes that proposal as a "plan" JSON file (schema below).
     You read it before anything happens — nothing is destructive by
     default.

  4. Run `apply-crosslist --plan your_plan.json` (no --confirm) to see
     a dry run: exactly what would happen, printed to your terminal,
     nothing written.

  5. If it looks right, run it again with --confirm to actually apply it.

This tool is deliberately ADDITIVE by default: applying a plan adds items
to new collections without removing them from collections they're already
in. Zotero supports an item living in multiple collections at once, so
"cleaning up" here means "give things a clear home," not "delete their
other homes." Deletion (of specific items or specific empty collections)
only happens if you explicitly list them in the plan, and only after you
see the dry run.

SETUP

  1. pip install -r requirements.txt
  2. Get a Zotero API key: https://www.zotero.org/settings/keys
     - You need "Allow library access" checked, and "Allow write access"
       checked if you intend to ever pass --confirm.
  3. Set your credentials as environment variables. Don't hardcode them,
     and don't commit them anywhere:

       export ZOTERO_API_KEY="your-key-here"
       export ZOTERO_USER_ID="your-numeric-user-id"

     (Your numeric user ID is on the same settings/keys page, and
     `verify` below will double check it against the key itself.)

  4. Run:
       python3 zotero_cleanup.py verify
       python3 zotero_cleanup.py audit
       python3 zotero_cleanup.py apply-crosslist --plan my_plan.json
       python3 zotero_cleanup.py apply-crosslist --plan my_plan.json --confirm

PLAN FILE SCHEMA

See examples/example_reorg_plan.json for a worked (fake-data) example.
A plan is a single JSON object with any of these top-level keys, all optional:

  "new_collections": [{"name": "...", "parent_key": null}, ...]
      Collections to create before anything else runs. Reference them
      later in "add_to" lists as "NEW:<name>".

  "crosslist": [{"item_key": "...", "title": "... (for your own reading)",
                  "add_to": ["Existing Collection Name", "NEW:New Collection"]}, ...]
      Adds each item to every collection listed, without removing it from
      anywhere. Existing collections are matched by exact name.

  "delete_items": ["ITEMKEY1", "ITEMKEY2"]
      Permanently deletes these specific items. Only put items here you
      have explicitly reviewed and approved — this is the one genuinely
      destructive list in the schema, so keep it short and deliberate.

  "delete_empty_collections": ["COLLECTIONKEY1", ...]
      Deletes these collections by their Zotero key. Deleting a collection
      never deletes the items inside it — they just lose that one
      organizational label.

  "delete_collections_by_name": ["Some Junk Collection Name", ...]
      Same as above but looked up by exact name instead of key — handy
      when an AI proposing the plan doesn't have (and shouldn't guess at)
      raw collection keys. If a name isn't found, it's skipped with a
      message rather than treated as an error.

  "orphan_fix": {"attachment_keys": ["KEY1", "KEY2"],
                  "target_collections": ["Some Collection"],
                  "create_parent_item": { ...full Zotero item JSON... }}
      For the specific case of a book or article that ended up in your
      library as several disconnected PDF attachments instead of one
      item with attachments under it. Creates the parent item, files it,
      then re-parents each attachment key underneath it.

A NOTE ON NAMES VS. KEYS

Every "add_to" / "delete_collections_by_name" entry that isn't prefixed
"NEW:" is resolved by exact collection NAME, not by Zotero's internal key.
This is intentional — it's much safer for an AI (or a human) writing a
plan by hand to reference "Learning Theories" than to reference an opaque
8-character key. Do NOT put a raw Zotero key into a name-matched field:
if there's no collection with that literal name, this tool will silently
CREATE one with that name rather than erroring, which is confusing to
untangle later. If you ever do have a raw key you want to target directly,
use --plan with "delete_empty_collections" (key-based) rather than
"delete_collections_by_name" (name-based).
"""

import json
import os
import sys
import time
import argparse
from pathlib import Path

import requests

ZOTERO_API_BASE = "https://api.zotero.org"


def get_credentials():
    key = os.environ.get("ZOTERO_API_KEY")
    user_id = os.environ.get("ZOTERO_USER_ID")
    if not key:
        sys.exit("Missing ZOTERO_API_KEY environment variable. See the SETUP section at the top of this file.")
    if not user_id:
        sys.exit("Missing ZOTERO_USER_ID environment variable. See the SETUP section at the top of this file.")
    return key, user_id


def zotero_headers(key):
    return {
        "Zotero-API-Version": "3",
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def cmd_verify(args):
    key, user_id = get_credentials()
    resp = requests.get(f"{ZOTERO_API_BASE}/keys/{key}", headers={"Zotero-API-Version": "3"}, timeout=15)
    if resp.status_code != 200:
        print(f"Key verification failed: HTTP {resp.status_code}")
        print(resp.text[:500])
        sys.exit(1)
    data = resp.json()
    reported_user_id = str(data.get("userID"))
    access = data.get("access", {})
    print(f"Key is valid. Zotero reports userID = {reported_user_id}")
    if reported_user_id != str(user_id):
        print(f"WARNING: ZOTERO_USER_ID env var is set to {user_id}, but the key itself belongs to userID {reported_user_id}.")
        print("Use the userID the key reports, not the other one.")
    print("Access granted by this key:")
    print(json.dumps(access, indent=2))
    lib_access = access.get("user", {})
    if not lib_access.get("library"):
        print("This key does NOT have personal library read access — go back to zotero.org/settings/keys and enable it.")
    if not lib_access.get("write"):
        print("This key does NOT have write access — 'apply-crosslist --confirm' will fail until you enable 'Allow write access' for the key.")


def get_all_collections(key, user_id):
    resp = requests.get(f"{ZOTERO_API_BASE}/users/{user_id}/collections", headers=zotero_headers(key),
                         params={"limit": 100}, timeout=30)
    resp.raise_for_status()
    return [{"key": c["key"], "name": c["data"]["name"], "parent": c["data"].get("parentCollection") or None}
            for c in resp.json()]


def get_full_top_level_items(key, user_id):
    """Everything needed to reason about reorganizing the library, without guessing
    at categorization — that part is for a human (or an AI, reading the output) to do."""
    items = []
    start = 0
    limit = 100
    while True:
        resp = requests.get(
            f"{ZOTERO_API_BASE}/users/{user_id}/items/top",
            headers=zotero_headers(key),
            params={"start": start, "limit": limit, "format": "json"},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for entry in batch:
            data = entry.get("data", {})
            creators = data.get("creators", [])
            first_author = ""
            if creators:
                c = creators[0]
                first_author = c.get("lastName") or c.get("name", "")
            items.append({
                "key": entry.get("key"),
                "title": data.get("title", "") or data.get("name", ""),
                "itemType": data.get("itemType", ""),
                "date": data.get("date", ""),
                "firstAuthor": first_author,
                "numCreators": len(creators),
                "tags": [t["tag"] for t in data.get("tags", [])],
                "collections": data.get("collections", []),
                "dateAdded": data.get("dateAdded", ""),
            })
        start += limit
        if len(batch) < limit:
            break
        time.sleep(0.2)
    return items


def cmd_audit(args):
    key, user_id = get_credentials()
    print("Pulling full library structure (collections + all top-level items)...")
    collections = get_all_collections(key, user_id)
    items = get_full_top_level_items(key, user_id)

    coll_by_key = {c["key"]: c for c in collections}
    for c in collections:
        c["path"] = c["name"]
        parent = coll_by_key.get(c["parent"]) if c["parent"] else None
        if parent:
            c["path"] = f"{parent['name']} / {c['name']}"

    audit = {"collections": collections, "items": items,
             "generated": time.strftime("%Y-%m-%d %H:%M:%S")}
    json_path = Path(__file__).parent / "zotero_library_audit.json"
    json_path.write_text(json.dumps(audit, indent=2))

    lines = [f"# Zotero library audit — {audit['generated']}", "",
             f"{len(collections)} collections, {len(items)} top-level items.\n"]
    unfiled = [i for i in items if not i["collections"]]
    lines.append(f"## Unfiled items ({len(unfiled)})\n")
    for i in unfiled:
        lines.append(f"- **{i['title']}** — {i['firstAuthor']} ({i['date']}) [{i['itemType']}] tags: {', '.join(i['tags']) or 'none'}")
    for c in collections:
        in_coll = [i for i in items if c["key"] in i["collections"]]
        lines.append(f"\n## {c['path']} ({len(in_coll)})\n")
        for i in in_coll:
            lines.append(f"- **{i['title']}** — {i['firstAuthor']} ({i['date']}) [{i['itemType']}] tags: {', '.join(i['tags']) or 'none'}")

    md_path = Path(__file__).parent / "zotero_library_audit.md"
    md_path.write_text("\n".join(lines))
    print(f"Wrote {json_path} and {md_path}")
    print(f"{len(collections)} collections, {len(items)} items, {len(unfiled)} unfiled.")
    print("Nothing was changed. Hand these two files to an AI assistant (or read them yourself) to plan a reorganization.")


def cmd_backfill_collection(args):
    """File every item with a given tag into a named collection, without touching
    anything else about the item. Safe to re-run — items already in the collection
    are skipped."""
    key, user_id = get_credentials()
    collection_key = ensure_collection(key, user_id, args.collection)
    print(f"Collection '{args.collection}' = {collection_key}")

    start = 0
    updated, already_there, skipped_attachments = 0, 0, 0
    while True:
        resp = requests.get(f"{ZOTERO_API_BASE}/users/{user_id}/items", headers=zotero_headers(key),
                             params={"tag": args.tag, "start": start, "limit": 100}, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for it in batch:
            data = it["data"]
            if data.get("itemType") == "attachment":
                skipped_attachments += 1
                continue
            current = data.get("collections", [])
            if collection_key in current:
                already_there += 1
                continue
            patch_resp = requests.patch(
                f"{ZOTERO_API_BASE}/users/{user_id}/items/{it['key']}",
                headers={**zotero_headers(key), "If-Unmodified-Since-Version": str(it["version"])},
                data=json.dumps({"collections": current + [collection_key]}),
                timeout=15,
            )
            if patch_resp.status_code == 204:
                print(f"  filed: {data.get('title', it['key'])}")
                updated += 1
            else:
                print(f"  FAILED [{data.get('title', it['key'])}]: HTTP {patch_resp.status_code} — {patch_resp.text[:200]}")
            time.sleep(0.2)
        start += 100
        if len(batch) < 100:
            break

    print(f"\n{updated} filed, {already_there} already there, {skipped_attachments} attachments skipped.")


def find_collection_by_name(key, user_id, name):
    """Strict lookup, never creates. Returns the key or None."""
    resp = requests.get(f"{ZOTERO_API_BASE}/users/{user_id}/collections", headers=zotero_headers(key),
                         params={"limit": 100}, timeout=15)
    resp.raise_for_status()
    for c in resp.json():
        if c.get("data", {}).get("name") == name:
            return c["key"]
    return None


def ensure_collection(key, user_id, name, parent=None):
    """Find a collection by name (optionally under a specific parent), or create it.
    Returns the collection key."""
    resp = requests.get(f"{ZOTERO_API_BASE}/users/{user_id}/collections", headers=zotero_headers(key),
                         params={"limit": 100}, timeout=15)
    resp.raise_for_status()
    for c in resp.json():
        data = c.get("data", {})
        if data.get("name") == name and (parent is None or data.get("parentCollection") == parent):
            return c["key"]
    body = {"name": name}
    if parent:
        body["parentCollection"] = parent
    resp = requests.post(f"{ZOTERO_API_BASE}/users/{user_id}/collections", headers=zotero_headers(key),
                          data=json.dumps([body]), timeout=15)
    resp.raise_for_status()
    result = resp.json()
    success = result.get("success", {}) or result.get("successful", {})
    if not success:
        sys.exit(f"Couldn't create collection '{name}': {resp.text[:500]}")
    return list(success.values())[0]["key"] if isinstance(list(success.values())[0], dict) else list(success.values())[0]


def cmd_apply_crosslist(args):
    """Execute a plan.json-shaped reorganization: create any needed collections,
    fix an orphaned-attachment case, add items to collections WITHOUT removing them
    from anywhere they already are, and (only if listed) delete specific approved
    items or empty collections. Always fetches each item's current version fresh
    right before patching, so it's safe even if the plan was written a while ago."""
    key, user_id = get_credentials()
    plan_path = Path(args.plan)
    if not plan_path.exists():
        sys.exit(f"Can't find {plan_path}")
    plan = json.loads(plan_path.read_text())

    name_to_key = {}
    for nc in plan.get("new_collections", []):
        if args.confirm:
            ck = ensure_collection(key, user_id, nc["name"], parent=nc.get("parent_key"))
            print(f"Collection ready: {nc['name']} -> {ck}")
        else:
            ck = f"<would-create:{nc['name']}>"
            print(f"[dry run] would ensure collection: {nc['name']} (parent {nc.get('parent_key') or 'none'})")
        name_to_key[f"NEW:{nc['name']}"] = ck

    def resolve_target(t):
        if t in name_to_key:
            return name_to_key[t]
        if t.startswith("NEW:"):
            sys.exit(f"Target '{t}' wasn't declared in new_collections")
        # plain name of an existing collection — resolve (and create defensively) by name
        if args.confirm:
            return ensure_collection(key, user_id, t)
        return f"<existing-or-new:{t}>"

    # process deletions early — safe to do before or after collection creation, doesn't
    # depend on it, and gets junk out of the way before the noisier crosslist output
    for item_key in plan.get("delete_items", []):
        if not args.confirm:
            print(f"[dry run] would delete item {item_key}")
            continue
        resp = requests.get(f"{ZOTERO_API_BASE}/users/{user_id}/items/{item_key}",
                             headers=zotero_headers(key), timeout=15)
        if resp.status_code != 200:
            print(f"  couldn't fetch item {item_key} to delete: HTTP {resp.status_code}")
            continue
        version = resp.json()["version"]
        del_resp = requests.delete(
            f"{ZOTERO_API_BASE}/users/{user_id}/items/{item_key}",
            headers={**zotero_headers(key), "If-Unmodified-Since-Version": str(version)}, timeout=15,
        )
        print(f"  {'deleted' if del_resp.status_code == 204 else 'FAILED ' + del_resp.text[:200]}: {item_key}")
        time.sleep(0.2)

    orphan = plan.get("orphan_fix")
    if orphan:
        attachment_keys = orphan.get("attachment_keys") or [orphan["attachment_key"]]
        target_keys = [resolve_target(t) for t in orphan["target_collections"]]
        print(f"\nOrphan fix: re-parenting {len(attachment_keys)} attachment(s) under a new item")
        if not args.confirm:
            print(f"  [dry run] would create parent item {orphan['create_parent_item']['title']!r} "
                  f"in collections {target_keys}, then reattach {attachment_keys} to it")
        else:
            new_item = dict(orphan["create_parent_item"])
            new_item["collections"] = target_keys
            resp = requests.post(f"{ZOTERO_API_BASE}/users/{user_id}/items", headers=zotero_headers(key),
                                  data=json.dumps([new_item]), timeout=15)
            resp.raise_for_status()
            success = resp.json().get("success", {})
            if not success:
                print(f"  FAILED to create parent item: {resp.text[:400]}")
            else:
                new_key = list(success.values())[0]
                print(f"  created parent item {new_key}")
                for att_key in attachment_keys:
                    att_resp = requests.get(f"{ZOTERO_API_BASE}/users/{user_id}/items/{att_key}",
                                             headers=zotero_headers(key), timeout=15)
                    if att_resp.status_code != 200:
                        print(f"  couldn't fetch attachment {att_key}: HTTP {att_resp.status_code}")
                        continue
                    att = att_resp.json()
                    patch = requests.patch(
                        f"{ZOTERO_API_BASE}/users/{user_id}/items/{att_key}",
                        headers={**zotero_headers(key), "If-Unmodified-Since-Version": str(att["version"])},
                        data=json.dumps({"parentItem": new_key}), timeout=15,
                    )
                    print(f"  re-attached {att_key}: {'OK' if patch.status_code == 204 else patch.text[:300]}")
                    time.sleep(0.2)

    print(f"\nCross-listing {len(plan.get('crosslist', []))} items...")
    for entry in plan.get("crosslist", []):
        targets = [resolve_target(t) for t in entry["add_to"]]
        if not args.confirm:
            print(f"  [dry run] {entry['title']} -> add to {targets}")
            continue
        resp = requests.get(f"{ZOTERO_API_BASE}/users/{user_id}/items/{entry['item_key']}",
                             headers=zotero_headers(key), timeout=15)
        if resp.status_code != 200:
            print(f"  FAILED to fetch {entry['item_key']}: HTTP {resp.status_code}")
            continue
        item = resp.json()
        current = item["data"].get("collections", [])
        new_collections = list(set(current) | set(targets))
        if set(new_collections) == set(current):
            print(f"  already filed correctly: {entry['title']}")
            continue
        patch = requests.patch(
            f"{ZOTERO_API_BASE}/users/{user_id}/items/{entry['item_key']}",
            headers={**zotero_headers(key), "If-Unmodified-Since-Version": str(item["version"])},
            data=json.dumps({"collections": new_collections}), timeout=15,
        )
        print(f"  {'OK' if patch.status_code == 204 else 'FAILED ' + patch.text[:200]}: {entry['title']}")
        time.sleep(0.2)

    for coll_key in plan.get("delete_empty_collections", []):
        if not args.confirm:
            print(f"  [dry run] would delete empty collection {coll_key}")
            continue
        resp = requests.get(f"{ZOTERO_API_BASE}/users/{user_id}/collections/{coll_key}",
                             headers=zotero_headers(key), timeout=15)
        if resp.status_code != 200:
            print(f"  couldn't fetch collection {coll_key} to delete: HTTP {resp.status_code}")
            continue
        version = resp.json()["version"]
        del_resp = requests.delete(
            f"{ZOTERO_API_BASE}/users/{user_id}/collections/{coll_key}",
            headers={**zotero_headers(key), "If-Unmodified-Since-Version": str(version)}, timeout=15,
        )
        print(f"  {'deleted' if del_resp.status_code == 204 else 'FAILED ' + del_resp.text[:200]}: {coll_key}")
        time.sleep(0.2)

    for name in plan.get("delete_collections_by_name", []):
        if not args.confirm:
            print(f"  [dry run] would find and delete collection named '{name}'")
            continue
        coll_key = find_collection_by_name(key, user_id, name)
        if not coll_key:
            print(f"  '{name}' not found (already deleted, or never existed) — skipping")
            continue
        resp = requests.get(f"{ZOTERO_API_BASE}/users/{user_id}/collections/{coll_key}",
                             headers=zotero_headers(key), timeout=15)
        version = resp.json()["version"]
        del_resp = requests.delete(
            f"{ZOTERO_API_BASE}/users/{user_id}/collections/{coll_key}",
            headers={**zotero_headers(key), "If-Unmodified-Since-Version": str(version)}, timeout=15,
        )
        print(f"  {'deleted' if del_resp.status_code == 204 else 'FAILED ' + del_resp.text[:200]}: '{name}' ({coll_key})")
        time.sleep(0.2)

    if not args.confirm:
        print("\nDRY RUN — nothing written. Re-run with --confirm once this looks right.")


def main():
    parser = argparse.ArgumentParser(description="Clean up a messy Zotero library with an AI assistant's help")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("verify", help="Confirm the API key works and check its permissions")
    sub.add_parser("audit", help="Read-only: dump your full library structure (collections + items) for reorganization planning")

    backfill_parser = sub.add_parser("backfill-collection", help="File all items with a given tag into a collection (safe/idempotent)")
    backfill_parser.add_argument("--collection", required=True, help="Collection name (created if missing)")
    backfill_parser.add_argument("--tag", required=True, help="Tag to filter by")

    crosslist_parser = sub.add_parser("apply-crosslist", help="Apply a reorganization plan JSON (additive by default; see PLAN FILE SCHEMA above)")
    crosslist_parser.add_argument("--plan", required=True, help="Path to your plan JSON file")
    crosslist_parser.add_argument("--confirm", action="store_true", help="Actually write to Zotero (default is dry-run)")

    args = parser.parse_args()
    if args.command == "verify":
        cmd_verify(args)
    elif args.command == "audit":
        cmd_audit(args)
    elif args.command == "backfill-collection":
        cmd_backfill_collection(args)
    elif args.command == "apply-crosslist":
        cmd_apply_crosslist(args)


if __name__ == "__main__":
    main()
