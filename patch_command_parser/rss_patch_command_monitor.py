#!/usr/bin/env python3
"""Quick dev script: monitor EQ RSS feed for slash command mentions, ask LLM to suggest doc updates."""
import os
import sys
import json
import html
import re
import requests
import feedparser

RSS_URL = "https://forums.everquest.com/index.php?forums/game-update-notes-live.9/index.rss"
STATE_FILE = os.path.join("patch_command_parser", "eq_feed_state.json")
OUTPUT_FILE = os.path.join("patch_command_parser", "llm_command_suggestions.json")
COMMANDS_DIR = "docs/projects/everquest/commands"


def deepseek_chat(messages, temperature=0.2, max_tokens=1024):
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    url = "https://api.deepseek.com/v1/chat/completions"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": "deepseek-chat", "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def strip_html(html_str):
    text = html.unescape(html_str)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_thread_content(url):
    """Fetch full thread content from forum link."""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        # Find main post content - XenForo uses .bbWrapper for post content
        match = re.search(r'<article[^>]*class="[^"]*message-body[^"]*"[^>]*>(.*?)</article>', resp.text, re.DOTALL)
        if match:
            content = match.group(1)
            # Extract just the bbWrapper content if present
            bb_match = re.search(r'<div class="bbWrapper">(.*?)</div>\s*</article>', content, re.DOTALL)
            if bb_match:
                return strip_html(bb_match.group(1))
            return strip_html(content)
        return None
    except Exception as e:
        print(f"    [!] Failed to fetch thread: {e}")
        return None


def get_entry_text(entry):
    """Get full text from entry, fetching thread if needed."""
    # Try to get link to full thread
    link = getattr(entry, "link", None)
    if link:
        print(f"  Fetching full thread from {link}")
        full_content = fetch_thread_content(link)
        if full_content:
            # Prepend title
            title = getattr(entry, "title", "")
            return f"{title}\n\n{full_content}"
    
    # Fallback to RSS summary if fetch failed
    parts = []
    for key in ("title", "summary", "description"):
        if hasattr(entry, key):
            parts.append(str(getattr(entry, key)))
    return strip_html("\n\n".join(parts))


def find_commands():
    """Return dict: command_name -> path
    
    Handles files named like 'cmd-outputfile.md' for '/outputfile' command
    """
    cmds = {}
    if not os.path.isdir(COMMANDS_DIR):
        return cmds
    for root, _, files in os.walk(COMMANDS_DIR):
        for f in files:
            if f.endswith(".md"):
                name = f[:-3].lower()  # remove .md
                # Handle cmd-commandname.md pattern
                if name.startswith("cmd-"):
                    name = name[4:]  # remove cmd- prefix
                cmds[name] = os.path.join(root, f)
    return cmds


def process_single_url(url):
    """Process a single forum post URL without state tracking."""
    print(f"Fetching thread content...")
    text = fetch_thread_content(url)
    if not text:
        print("[X] Failed to fetch thread content")
        return
    
    print(f"[OK] Fetched {len(text)} chars")
    
    # Extract commands
    print("-> Asking LLM to extract commands...")
    llm_resp = deepseek_chat(
        [
            {"role": "system", "content": "Extract EverQuest slash commands. Return ONLY base command names without arguments. JSON array format: [\"/command1\", \"/command2\"]"},
            {"role": "user", "content": f"Extract base slash commands (without arguments) from these patch notes. For example: if notes mention '/outputfile inventory', extract only '/outputfile'.\n\nPatch notes:\n{text}"},
        ],
        temperature=0.0,
        max_tokens=256,
    )
    try:
        commands = json.loads(llm_resp)
        print(f"[OK] Extracted {len(commands)} commands: {commands}")
    except Exception as e:
        print(f"[!] Failed to parse LLM response: {e}")
        return
    
    if not commands:
        print("[i] No commands found")
        return
    
    cmd_map = find_commands()
    print(f"Checking against {len(cmd_map)} existing command docs...\n")
    
    results = []
    template_guide = """EverQuest command docs follow this template:
```markdown
---
tags:
  - command
---

# /commandname

## Syntax

<!--cmd-syntax-start-->
```eqcommand
/commandname [options]
```
<!--cmd-syntax-end-->

## Description

<!--cmd-desc-start-->
Brief description.
<!--cmd-desc-end-->

## Options

**`option1`**
:   Description

## Examples

!!! example
    `/commandname option` - Description
```

Maintain structure. Use HTML comments. Use definition lists for options."""
    
    for cmd in commands:
        cmd_name = cmd.lstrip("/").lower()
        existing_path = cmd_map.get(cmd_name)
        
        if existing_path:
            print(f"  {cmd}: EXISTING doc found at {existing_path}")
            existing_doc = open(existing_path, encoding="utf-8").read()
        else:
            print(f"  {cmd}: NEW command (no existing doc)")
            existing_doc = None

        print(f"  -> Asking LLM to generate {'updated' if existing_doc else 'new'} doc...")
        
        if existing_doc:
            prompt = (
                template_guide + "\n\n"
                f"Command: {cmd}\n"
                f"Patch notes:\n{text}\n\n"
                f"Existing doc:\n```markdown\n{existing_doc}\n```\n\n"
                "Generate the COMPLETE updated markdown incorporating changes. "
                "Preserve structure, only update relevant sections. Return ONLY the markdown, no wrapper."
            )
        else:
            prompt = (
                template_guide + "\n\n"
                f"New command: {cmd}\n"
                f"Patch notes:\n{text}\n\n"
                "Draft complete markdown following template. Return ONLY the markdown, no wrapper."
            )

        llm_resp = deepseek_chat(
            [{"role": "system", "content": "Technical writer for EverQuest commands. Generate properly formatted markdown."}, 
             {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2000,
        )
        
        cleaned = re.sub(r'^```(?:markdown|md)?\s*\n?', '', llm_resp.strip())
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)
        print(f"  [OK] Generated {len(cleaned)} chars of markdown\n")

        result = {"command": cmd, "type": "update" if existing_doc else "new", "markdown": cleaned}
        if existing_path:
            result["existing_path"] = existing_path
        results.append(result)
    
    # Save results
    os.makedirs("patch_command_parser", exist_ok=True)
    url_output = os.path.join("patch_command_parser", "url_command_suggestions.json")
    json.dump({"url": url, "results": results}, open(url_output, "w"), indent=2)
    print(f"[OK] Wrote {len(results)} result(s) to {url_output}")
    
    # Save markdown files
    output_dir = os.path.join("patch_command_parser", "url_suggested_docs")
    os.makedirs(output_dir, exist_ok=True)
    
    for result in results:
        cmd_name = result["command"].lstrip("/")
        filename = f"cmd-{cmd_name}.md"
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(result["markdown"])
        
        print(f"  -> Saved {filename} ({len(result['markdown'])} chars)")
    
    print(f"\n[OK] Saved {len(results)} markdown file(s) to {output_dir}/")


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except:
        pass

    # Parse args
    limit = None
    test_url = None
    
    if "--url" in sys.argv:
        idx = sys.argv.index("--url")
        if idx + 1 < len(sys.argv):
            test_url = sys.argv[idx + 1]
    
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        if idx + 1 < len(sys.argv):
            limit = int(sys.argv[idx + 1])

    # Handle single URL test mode
    if test_url:
        print(f"[URL MODE] Processing single post: {test_url}")
        process_single_url(test_url)
        return

    print("Starting RSS patch monitor...")
    if limit:
        print(f"[LIMIT MODE] Will only process first {limit} new entries")
    
    os.makedirs("patch_command_parser", exist_ok=True)
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {"seen": []}
    print(f"Loaded state: {len(state['seen'])} previously seen entries")

    print(f"Fetching RSS feed: {RSS_URL}")
    try:
        feed = feedparser.parse(RSS_URL)
    except Exception as e:
        print(f"[X] Failed to fetch feed: {e}")
        return
    
    if not feed.entries:
        print("[X] No feed entries found")
        return
    print(f"[OK] Found {len(feed.entries)} total entries in feed")

    results = []
    new_ids = []
    processed_count = 0
    for i, entry in enumerate(feed.entries, 1):
        if limit and processed_count >= limit:
            print(f"\n[LIMIT] Reached limit of {limit} entries, stopping")
            break
        entry_id = getattr(entry, "id", getattr(entry, "link", ""))
        entry_title = getattr(entry, "title", "Untitled")
        
        if entry_id in state["seen"]:
            print(f"  [{i}/{len(feed.entries)}] Skipping (already seen): {entry_title[:60]}")
            continue
        
        print(f"\n[{i}/{len(feed.entries)}] NEW ENTRY: {entry_title}")
        new_ids.append(entry_id)
        processed_count += 1

        text = get_entry_text(entry)
        if not text:
            print("  [!] No text content, skipping")
            continue
        print(f"  Entry text: {len(text)} chars")

        # Ask LLM to extract slash commands
        print("  -> Asking LLM to extract commands...")
        llm_resp = deepseek_chat(
            [
                {"role": "system", "content": "Extract EverQuest slash commands. Return ONLY base command names without arguments. JSON array format: [\"/command1\", \"/command2\"]"},
                {"role": "user", "content": f"Extract base slash commands (without arguments) from these patch notes. For example: if notes mention '/outputfile inventory', extract only '/outputfile'.\n\nPatch notes:\n{text}"},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        try:
            commands = json.loads(llm_resp)
            print(f"  [OK] Extracted {len(commands)} commands: {commands}")
        except Exception as e:
            print(f"  [!] Failed to parse LLM response: {e}")
            commands = []

        if not commands:
            print("  No commands found in entry")
            continue

        cmd_map = find_commands()
        print(f"  Checking against {len(cmd_map)} existing command docs...")
        
        for cmd in commands:
            cmd_name = cmd.lstrip("/").lower()
            existing_path = cmd_map.get(cmd_name)
            
            if existing_path:
                print(f"    {cmd}: EXISTING doc found at {existing_path}")
                existing_doc = open(existing_path, encoding="utf-8").read()
            else:
                print(f"    {cmd}: NEW command (no existing doc)")
                existing_doc = None

            print(f"    -> Asking LLM to generate {'updated' if existing_doc else 'new'} doc...")
            
            # Template guidance for consistent formatting
            template_guide = """EverQuest command docs follow this template:
```markdown
---
tags:
  - command
---

# /commandname

## Syntax

<!--cmd-syntax-start-->
```eqcommand
/commandname [options]
```
<!--cmd-syntax-end-->

## Description

<!--cmd-desc-start-->
Brief description.
<!--cmd-desc-end-->

## Options

**`option1`**
:   Description

## Examples

!!! example
    `/commandname option` - Description
```

Maintain structure. Use HTML comments. Use definition lists for options. Only add examples if they're specifically from the patch notes, otherwise skip."""

            if existing_doc:
                prompt = (
                    template_guide + "\n\n"
                    f"Command: {cmd}\n"
                    f"Patch notes:\n{text}\n\n"
                    f"Existing doc:\n```markdown\n{existing_doc}\n```\n\n"
                    "Generate the COMPLETE updated markdown incorporating changes. "
                    "Preserve structure, only update relevant sections. Return ONLY the markdown, no wrapper."
                )
            else:
                prompt = (
                    template_guide + "\n\n"
                    f"New command: {cmd}\n"
                    f"Patch notes:\n{text}\n\n"
                    "Draft complete markdown following template. Return ONLY the markdown, no wrapper."
                )

            llm_resp = deepseek_chat(
                [{"role": "system", "content": "Technical writer for EverQuest commands. Generate properly formatted markdown."}, 
                 {"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=2000,
            )
            
            # Strip markdown code fences if present
            cleaned = re.sub(r'^```(?:markdown|md)?\s*\n?', '', llm_resp.strip())
            cleaned = re.sub(r'\n?```\s*$', '', cleaned)
            print(f"    [OK] Generated {len(cleaned)} chars of markdown")

            result = {"command": cmd, "type": "update" if existing_doc else "new", "markdown": cleaned}
            if existing_path:
                result["existing_path"] = existing_path
            results.append(result)

    print(f"\n{'='*60}")
    if new_ids:
        state["seen"] = (state["seen"] + new_ids)[-200:]
        json.dump(state, open(STATE_FILE, "w"), indent=2)
        print(f"[OK] Updated state file with {len(new_ids)} new entries")

    if results:
        # Save JSON summary
        json.dump({"results": results}, open(OUTPUT_FILE, "w"), indent=2)
        print(f"[OK] Wrote {len(results)} result(s) to {OUTPUT_FILE}")
        
        # Save individual markdown files
        output_dir = os.path.join("patch_command_parser", "suggested_docs")
        os.makedirs(output_dir, exist_ok=True)
        
        for result in results:
            cmd_name = result["command"].lstrip("/")
            filename = f"cmd-{cmd_name}.md"
            filepath = os.path.join(output_dir, filename)
            
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(result["markdown"])
            
            print(f"  -> Saved {filename} ({len(result['markdown'])} chars)")
        
        print(f"\n[OK] Saved {len(results)} markdown file(s) to {output_dir}/")
    else:
        print("[i] No new commands found")


if __name__ == "__main__":
    main()


