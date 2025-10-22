#!/usr/bin/env python3
"""Monitor EQ RSS feed for slash command mentions, ask LLM to suggest doc updates."""
import json
import os
import re
import argparse
import requests
import feedparser
from pathlib import Path
from bs4 import BeautifulSoup
from openai import OpenAI

# --- Config ---
RSS_URL = "https://forums.everquest.com/index.php?forums/game-update-notes-live.9/index.rss"
COMMANDS_DIR = Path("docs/projects/everquest/commands")
STATE_FILE = Path(".cache/eq_feed_state.json")
INITIAL_MAX_ID = 305891  # Don't process entries older than this on first run

TEMPLATE_GUIDE = """EverQuest command docs follow this template:
```markdown
---
tags:
  - command
---

# /commandname

## Syntax

<!--cmd-syntax-start-->
```eqcommand
/commandname [options] <variable> [repeatable]...
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

**CRITICAL INSTRUCTIONS:**
1.  **UPDATE AND INTEGRATE**: Integrate the information from the patch notes into the doc. DO NOT just add a "patch notes" section - look at the syntax, options, etc. and update them.
2.  **DON'T REMOVE EXISTING EXAMPLES**: Also, don't invent new examples unless one is explicitly provided in the patch notes.
3.  **INFER FOR NEW COMMANDS**: For a **NEW** command only, if the patch notes describe it as being "similar" to an existing command, you should infer its syntax and options from the existing command's documentation.
4.  **TIMELESS DOC**: Do not add "NEW" or "Updated" or "Now" or "added" or "changed" to the doc. This should be timeless.
5. ** NO EMPTY SECTIONS **: If a section is empty, don't include it.
"""

# --- Core Functions ---

# Initialize OpenAI client once at module level
_deepseek_client = None

def get_deepseek_client():
    """Get or create DeepSeek API client."""
    global _deepseek_client
    if _deepseek_client is None:
        _deepseek_client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
    return _deepseek_client

def deepseek_chat(messages, temperature=1.0, max_tokens=8192, use_reasoner=False):
    """Call DeepSeek API using OpenAI SDK."""
    client = get_deepseek_client()
    model = "deepseek-reasoner" if use_reasoner else "deepseek-chat"
    resp = client.chat.completions.create(model=model, messages=messages, 
                                          temperature=temperature, max_tokens=max_tokens)
    return resp.choices[0].message.content.strip()

def fetch_thread_content(url):
    """Fetch full thread content from forum link."""
    soup = BeautifulSoup(requests.get(url, timeout=30).text, 'html.parser')
    article = soup.find('article', class_=lambda x: x and 'message-body' in x)
    if article:
        content = article.find('div', class_='bbWrapper') or article
        return content.get_text(separator=' ', strip=True)
    return None

def extract_numeric_id(entry_id):
    """Extract numeric ID from entry identifier."""
    match = re.search(r'(\d+)', str(entry_id))
    return int(match.group(1)) if match else None

def find_commands():
    """Return dict: command_name -> path"""
    if not COMMANDS_DIR.exists():
        return {}
    return {
        path.stem[4:].lower() if path.stem.startswith("cmd-") else path.stem.lower(): path
        for path in COMMANDS_DIR.rglob("*.md")
    }

def extract_commands_from_text(text):
    """Uses LLM to get a list of base slash commands from text."""
    print("-> Asking LLM to extract commands...")
    resp = deepseek_chat([
        {"role": "system", "content": "You are an EverQuest command extractor. Return ONLY a JSON array of command names."},
        {"role": "user", "content": f"Extract slash commands from these patch notes. For commands with args like '/outputfile inventory', return only base '/outputfile'.\n\nReturn ONLY JSON array: []\n\nPatch notes:\n{text}"}
    ], temperature=0.0, max_tokens=512)
    commands = json.loads(resp)
    print(f"[OK] Extracted {len(commands)} commands: {commands}")
    return commands

def generate_doc(cmd, text, existing_doc, related_docs=None):
    """Generates the markdown for a single command using the LLM."""
    if related_docs is None:
        related_docs = {}
    prompt = TEMPLATE_GUIDE + "\n\n"
    if existing_doc:
        prompt += f"Command: {cmd}\nPatch notes:\n{text}\n\nExisting doc:\n```markdown\n{existing_doc}\n```\n\nGenerate the COMPLETE updated markdown. Preserve structure, update relevant sections. Return ONLY markdown."
    else:
        prompt += f"New command: {cmd}\nPatch notes:\n{text}\n\n"
        if related_docs:
            prompt += "This command is similar to existing commands. Use as reference:\n\n"
            prompt += "\n".join(f"Reference `{name}`:\n```markdown\n{doc}\n```\n" for name, doc in related_docs.items())
        prompt += "\nDraft complete markdown following template. Return ONLY markdown."
    
    resp = deepseek_chat([
        {"role": "system", "content": "You are a technical writer for EverQuest commands. Generate markdown following all instructions."},
        {"role": "user", "content": prompt}
    ], temperature=1.0, max_tokens=8192, use_reasoner=True)
    
    return re.sub(r'(^```(?:markdown|md)?\s*\n?|\n?```\s*$)', '', resp.strip())

def find_related_docs_for_new_command(cmd_name, patch_text, cmd_map):
    """For new commands, find mentioned existing commands to use as reference."""
    mentioned = {c.lstrip('/').lower() for c in re.findall(r"`(/[\w]+)`", patch_text)}
    related_docs = {}
    for name in mentioned:
        if name in cmd_map and name != cmd_name:
            print(f"    -> Found reference to `/{name}`, providing as context")
            related_docs[f"/{name}"] = cmd_map[name].read_text(encoding="utf-8")
    return related_docs

def process_text_for_commands(text, cmd_map):
    """Given text, extracts commands and generates docs."""
    commands = extract_commands_from_text(text)
    if not commands:
        return []
    
    print(f"Checking against {len(cmd_map)} existing command docs...\n")
    results = []
    for cmd in commands:
        cmd_name = cmd.lstrip("/").lower()
        existing_path = cmd_map.get(cmd_name)
        
        if existing_path:
            print(f"  {cmd}: EXISTING doc at {existing_path}")
            existing_doc = existing_path.read_text(encoding="utf-8")
            related_docs = {}
        else:
            print(f"  {cmd}: NEW command")
            existing_doc = None
            related_docs = find_related_docs_for_new_command(cmd_name, text, cmd_map)
        
        print(f"  -> Generating {'updated' if existing_doc else 'new'} doc...")
        markdown = generate_doc(cmd, text, existing_doc, related_docs)
        print(f"  [OK] {len(markdown)} chars\n")
        
        results.append({
            "command": cmd,
            "markdown": markdown,
            "existing_path": existing_path
        })
    return results

def save_results(results):
    """Write command documentation to files."""
    if not results:
        print("[i] No results to save.")
        return
    
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    for r in results:
        cmd_name = r["command"].lstrip("/")
        filepath = r["existing_path"] if r["existing_path"] else COMMANDS_DIR / f"cmd-{cmd_name}.md"
        filepath.write_text(r["markdown"], encoding="utf-8")
        print(f"  -> {'Updated' if r['existing_path'] else 'Created'} {filepath} ({len(r['markdown'])} chars)")
    
    print(f"\n[OK] Saved {len(results)} doc(s)")

# --- Main Execution ---

def process_url_mode(url, cmd_map):
    """Process a single forum URL."""
    print(f"[URL MODE] {url}")
    if text := fetch_thread_content(url):
        print(f"[OK] Fetched {len(text)} chars")
        save_results(process_text_for_commands(text, cmd_map))

def process_rss_mode(cmd_map, limit=None):
    """Monitor RSS feed for new entries."""
    limit_msg = f" (limit={limit} entries)" if limit else ""
    print(f"Starting RSS monitor{limit_msg}...")
    
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"seen": [], "max_id": INITIAL_MAX_ID}
    
    # Ensure max_id exists in state (for legacy state files)
    if "max_id" not in state:
        state["max_id"] = max((extract_numeric_id(id) for id in state["seen"]), default=INITIAL_MAX_ID)
    
    print(f"Loaded state: {len(state['seen'])} previously seen, max_id={state['max_id']}")
    
    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        print("[X] No feed entries found")
        return
    print(f"[OK] {len(feed.entries)} entries in feed")
    
    all_results = []
    new_ids = []
    processed_entries = []  # Track entries we processed
    
    for i, entry in enumerate(feed.entries, 1):
        if limit and len(new_ids) >= limit:
            print(f"\n[LIMIT] Reached {limit}, stopping")
            break
        
        entry_id = entry.get("id") or entry.get("link", "")
        title = entry.get("title", "Untitled")
        numeric_id = extract_numeric_id(entry_id)
        
        # Skip if already seen
        if entry_id in state["seen"]:
            print(f"  [{i}/{len(feed.entries)}] Skip (seen): {title[:60]}")
            continue
        
        # Skip if older than max_id
        if numeric_id and numeric_id <= state["max_id"]:
            print(f"  [{i}/{len(feed.entries)}] Skip (ID {numeric_id} <= {state['max_id']}): {title[:60]}")
            continue
        
        print(f"\n[{i}/{len(feed.entries)}] NEW (ID {numeric_id}): {title}")
        
        if not (link := entry.get("link")):
            print("  [!] No link, skipping")
            continue
        
        if not (text := fetch_thread_content(link)):
            print("  [!] Failed to fetch, skipping")
            continue
        
        print(f"  [OK] {len(text)} chars")
        new_ids.append(entry_id)
        if numeric_id:
            state["max_id"] = max(state["max_id"], numeric_id)
        
        results_for_entry = process_text_for_commands(text, cmd_map)
        all_results.extend(results_for_entry)
        
        # Track this entry if it had command results
        if results_for_entry:
            processed_entries.append({"title": title, "link": link})
    
    print(f"\n{'='*60}")
    
    if all_results:
        save_results(all_results)
    
    if new_ids:
        state["seen"] = (state["seen"] + new_ids)[-200:]
        STATE_FILE.write_text(json.dumps(state, indent=2))
        print(f"[OK] Updated state with {len(new_ids)} new entries, max_id={state['max_id']}")
    else:
        print("[i] No new entries")
    
    # Output for GitHub Actions
    if processed_entries and "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            # Output patch notes as JSON for parsing in workflow
            f.write(f"patch_notes={json.dumps(processed_entries)}\n")
            print(f"[OK] Exported {len(processed_entries)} patch note link(s) to GITHUB_OUTPUT")

def main():
    parser = argparse.ArgumentParser(description="Monitor EQ RSS feed for command changes.")
    parser.add_argument("--url", help="Process a single forum URL instead of RSS feed")
    parser.add_argument("--limit", type=int, help="Limit new RSS entries to process")
    args = parser.parse_args()
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    
    cmd_map = find_commands()
    if args.url:
        process_url_mode(args.url, cmd_map)
    else:
        process_rss_mode(cmd_map, args.limit)

if __name__ == "__main__":
    main()
