import json
import re
import posixpath
from pathlib import Path
import mkdocs.plugins

# warning: this is vibe-coded and should be refactored.

# Global configurations for both features:
# - overlap: commands that exist across multiple projects (from overlap-commands.json)
# - inheritance: datatype inheritance display (from datatype-inheritance.json)
# - discussion_links: forum discussions that link to documentation pages (from discussion_mapper/data/thread_links.json)
_DUPE_CONFIG = {}
_INHERITANCE_CONFIG = {}
_THREAD_LINKS = {}

@mkdocs.plugins.event_priority(0) #default priority
def on_config(config):
    """Load configs for overlap (overlap-commands.json), datatype inheritance (datatype-inheritance.json), and discussion links."""
    global _DUPE_CONFIG, _INHERITANCE_CONFIG, _THREAD_LINKS
    
    config_dir = Path(config["config_file_path"]).parent.resolve()
    hooks_dir = config_dir / "hooks"
    
    # Load overlap configuration (commands overlapping across projects)
    dupe_path = hooks_dir / "overlap-commands.json"
    if dupe_path.exists():
        _DUPE_CONFIG = json.loads(dupe_path.read_text())
    
    # Load datatype inheritance configuration
    inheritance_path = hooks_dir / "datatype-inheritance.json"
    if inheritance_path.exists():
        _INHERITANCE_CONFIG = json.loads(inheritance_path.read_text())
    
    # Load discussion links (forum threads that link to documentation pages)
    thread_links_path = config_dir / "discussion_mapper" / "data" / "thread_links.json"
    if thread_links_path.exists():
        try:
            _THREAD_LINKS = json.loads(thread_links_path.read_text(encoding='utf-8'))
            print(f"✅ Loaded {len(_THREAD_LINKS)} discussion link mappings")
        except Exception as e:
            print(f"⚠️  Warning: Could not load discussion links: {e}")
            _THREAD_LINKS = {}
    else:
        print(f"ℹ️  No discussion links file found at {thread_links_path}")
        _THREAD_LINKS = {}
        
    return config

# === OVERLAP COMMANDS FUNCTIONALITY ===

def _extract_command_from_markdown(markdown):
    h1_match = re.search(r'^#\s+(/[^\s]+)', markdown, flags=re.MULTILINE)
    if h1_match:
        return h1_match.group(1).lower()
    return None

def _detect_project_from_path(src_path):
    projects = _DUPE_CONFIG.get("projects", {})
    normalized_path = src_path.replace("\\", "/")
    
    for project_name, config in projects.items():
        path_template = config.get("path_template", "")
        if path_template:
            base_pattern = path_template.split("{command}")[0]
            if normalized_path.startswith(base_pattern):
                return project_name
    
    return None

def _build_cross_reference(page, command, config):
    """Build overlap cross-reference blocks to other projects' versions of the command."""
    if not _DUPE_CONFIG:
        return None
        
    current_project = _detect_project_from_path(page.file.src_path)
    if not current_project:
        return None
        
    command_projects = _DUPE_CONFIG.get(command, [])
    if len(command_projects) <= 1:
        return None
        
    projects_config = _DUPE_CONFIG.get("projects", {})
    cross_refs = []
    
    docs_dir = Path(config["docs_dir"]).resolve()
    current_dir = posixpath.dirname(page.file.src_uri)
    
    for project in command_projects:
        if project == current_project:
            continue
            
        project_config = projects_config.get(project, {})
        path_template = project_config.get("path_template")
        
        if not path_template:
            continue
            
        target_path = path_template.format(command=command.lstrip("/").lower())
        target_file = docs_dir / target_path
        
        if not target_file.exists():
            continue
            
        relative_path = posixpath.relpath(target_path, current_dir or ".")
        
        # Create the cross-reference with project name and include syntax/description
        cross_ref = f'??? abstract "{project}\'s version of {command}"\n    \n    {{% \n      include-markdown "{target_path}" \n      start="<!--cmd-syntax-start-->\\n" \n      end="<!--cmd-syntax-end-->\\n"\n    %}}\n    \n    {{% \n      include-markdown "{target_path}" \n      start="<!--cmd-desc-start-->\\n" \n      end="<!--cmd-desc-end-->\\n"\n    %}}\n    \n    [Read more]({relative_path}){{ .md-button }}'
        
        cross_refs.append(cross_ref)
    
    if cross_refs:
        return "\n\n".join(cross_refs)
    
    return None

# === DATATYPEINHERITANCE (admonition) ===

def _extract_datatype_from_path(src_path):
    """Extract datatype name from file path."""
    # Look for datatype-{name}.md pattern
    match = re.search(r'datatype-([^/\\]+)\.md$', src_path)
    if match:
        return match.group(1).lower()
    return None

def _build_inheritance_admonition(datatype, page, config):
    """Build datatype inheritance information as an admonition."""
    if not _INHERITANCE_CONFIG:
        return None
        
    datatypes = _INHERITANCE_CONFIG.get("datatypes", {})
    datatype_info = datatypes.get(datatype)
    
    if not datatype_info or not datatype_info.get("inherits_from"):
        return None
    
    docs_dir = Path(config["docs_dir"]).resolve()
    current_dir = posixpath.dirname(page.file.src_uri)
    inheritance_sections = []
    
    for parent in datatype_info["inherits_from"]:
        parent_type = parent.get("type")
        parent_path = parent.get("path")
        
        if not parent_type or not parent_path:
            continue
            
        # Verify the parent file exists
        parent_file = docs_dir / parent_path
        if not parent_file.exists():
            continue
            
        # Calculate relative path from current page to parent
        relative_path = posixpath.relpath(parent_path, current_dir or ".")
        
        # Create the inheritance section with link, description, read more, and include members and linkrefs
        inheritance_section = (
            f'??? info "Inherited members from *{parent_type}*"\n'
            f'    \n'
            f'    ## Inherited from *[{parent_type}]({relative_path})*\n'
            f'    \n'
            f'    {{% \n'
            f'      include-markdown "{parent_path}" \n'
            f'      start="<!--dt-desc-start-->\\n" \n'
            f'      end="<!--dt-desc-end-->\\n"\n'
            f'    %}}\n'
            f'    \n'
            f'    [Read more]({relative_path}){{ .md-button }}\n'
            f'    \n'
            f'    {{% \n'
            f'      include-markdown "{parent_path}" \n'
            f'      start="<!--dt-members-start-->\\n" \n'
            f'      end="<!--dt-members-end-->\\n"\n'
            f'    %}}\n'
            f'    {{% \n'
            f'      include-markdown "{parent_path}" \n'
            f'      start="<!--dt-linkrefs-start-->\\n" \n'
            f'      end="<!--dt-linkrefs-end-->\\n"\n'
            f'    %}}'
        )
        
        inheritance_sections.append(inheritance_section)
    
    if not inheritance_sections:
        return None
    
    # Build the complete admonition with all inheritance sections
    return "\n\n".join(inheritance_sections)

# === PROJECT ATTRIBUTION FUNCTIONALITY ===

def _extract_title_from_markdown(markdown_content):
    """Extract the first H1 title from markdown content."""
    # Look for the first H1 header
    h1_match = re.search(r'^#\s+(.+)$', markdown_content, flags=re.MULTILINE)
    if h1_match:
        return h1_match.group(1).strip()
    return None

def _detect_project_name_from_path(src_path):
    """Detect project name from file path by reading the title from the project page."""
    # Normalize path separators
    normalized_path = src_path.replace("\\", "/")
    
    # Check if it's in the reference directory (exception case)
    if "/macroquest/reference/" in normalized_path:
        return "MacroQuest", "projects/macroquest/index.md"
    
    # Extract project from path like "projects/projectname/..."
    if not normalized_path.startswith("projects/"):
        return None, None
        
    path_parts = normalized_path.split("/")
    if len(path_parts) < 3:  # projects/projectname/file.md at minimum
        return None, None
        
    project_dir = path_parts[1]  # e.g., "mq2aaspend", "macroquest"
    
    # Special handling for macroquest subdirectories
    if project_dir == "macroquest":
        # Check for core plugins like map
        if "plugins/core-plugins/" in normalized_path and len(path_parts) >= 5:
            plugin_name = path_parts[4]  # e.g., "map"
            
            # Try both index.md and README.md (prefer index.md)
            for filename in ["index.md", "README.md"]:
                project_link = f"projects/macroquest/plugins/core-plugins/{plugin_name}/{filename}"
                try:
                    plugin_path = Path("docs") / project_link
                    if plugin_path.exists():
                        title = _extract_title_from_markdown(plugin_path.read_text(encoding='utf-8'))
                        if title:
                            return title, project_link
                except Exception:
                    pass
            
            # Fallback if no file found or no title extracted
            return plugin_name.title(), f"projects/macroquest/plugins/core-plugins/{plugin_name}/index.md"
        else:
            return "MacroQuest", "projects/macroquest/index.md"
    
    # For other projects, read the title from index.md
    project_link = f"projects/{project_dir}/index.md"
    try:
        index_path = Path("docs") / project_link
        if index_path.exists():
            title = _extract_title_from_markdown(index_path.read_text(encoding='utf-8'))
            if title:
                return title, project_link
    except Exception:
        pass
        
    return None, None

def _should_show_project_attribution(page):
    """Check if a page should show project attribution."""
    # Only process pages in the projects directory
    src_path = page.file.src_path.replace("\\", "/")
    if not src_path.startswith("projects/"):
        return False
        
    # Check if page has relevant tags
    if not page.meta or not page.meta.get("tags"):
        return False
        
    relevant_tags = {"command", "datatype", "tlo"}
    page_tags = set(page.meta.get("tags", []))
    
    return bool(relevant_tags & page_tags)

def _build_project_attribution_data(page, config):
    """Build project attribution data for template rendering."""
    project_name, project_link = _detect_project_name_from_path(page.file.src_path)
    
    if not project_name or not project_link:
        return None
        
    # Verify the project link exists
    docs_dir = Path(config["docs_dir"]).resolve()
    project_file = docs_dir / project_link
    if not project_file.exists():
        return None
        
    # Calculate relative path from current page to project
    # Must account for MkDocs output directory structure:
    # - index.md/README.md outputs to same directory
    # - regular files output to subdirectory (foo.md -> foo/)
    src_uri_parts = posixpath.splitext(page.file.src_uri)
    src_basename = posixpath.basename(src_uri_parts[0])
    
    if src_basename.lower() in ("readme", "index"):
        # index.md or README.md -> output directory is parent
        output_dir = posixpath.dirname(page.file.src_uri)
    else:
        # regular file -> output directory is parent/stem/
        output_dir = src_uri_parts[0]  # includes parent and stem without extension
    
    relative_path = posixpath.relpath(project_link, output_dir or ".")
    
    # Convert .md path to proper MkDocs URL (strip .md, handle index/readme)
    # e.g., "../../index.md" -> "../../", "../../foo.md" -> "../../foo/"
    path_parts = posixpath.splitext(relative_path)
    if path_parts[0].endswith(('/index', '/README', '\\index', '\\README')) or \
       posixpath.basename(path_parts[0]).lower() in ('index', 'readme'):
        # index.md or README.md -> use directory path
        project_url = posixpath.dirname(relative_path) + '/'
        if project_url == '/':
            project_url = './'
    else:
        # regular file -> strip .md and add trailing slash
        project_url = path_parts[0] + '/'
    
    # Determine the type from tags
    page_tags = page.meta.get("tags", [])
    if "command" in page_tags:
        item_type = "command"
    elif "datatype" in page_tags:
        item_type = "datatype"
    elif "tlo" in page_tags:
        item_type = "TLO"
    else:
        item_type = "item"  # fallback
        
    # Return data dict for template
    return {
        "project_name": project_name,
        "project_url": project_url,
        "item_type": item_type
    }

# === FRONTMATTER INFOBOX FUNCTIONALITY ===

def _should_inject_frontmatter_infobox(page):
    """Check if a page should get the frontmatter infobox injected."""
    # Only process index.md or README.md files in the projects directory
    src_path = Path(page.file.src_path).as_posix()
    if not src_path.startswith("projects/"):
        return False
    
    # Check if the file ends with index.md or README.md (case-insensitive)
    src_path_lower = src_path.lower()
    if not (src_path_lower.endswith("/index.md") or src_path_lower.endswith("/readme.md")):
        return False
    
    # Only inject if the page has frontmatter metadata that would be useful for the infobox
    if not page.meta:
        return False
        
    # Check if any of the infobox-relevant fields are present
    infobox_fields = {
        'tagline', 'authors', 'config', 'resource_link', 
        'support_link', 'repository', 'quick_start'
    }
    return any(field in page.meta for field in infobox_fields)

def _inject_frontmatter_infobox(markdown):
    """Inject {{ frontmatter_infobox() }} after the main header."""
    # Find the main header (# Title) and inject the frontmatter_infobox() after it
    # Look for the pattern: # Title followed by a newline
    header_pattern = r'^(#\s+[^\n]+\n)'
    
    def inject_infobox(match):
        header = match.group(1)
        return f"{header}{{{{ frontmatter_infobox() }}}}\n"
    
    # Apply the replacement
    return re.sub(header_pattern, inject_infobox, markdown, count=1, flags=re.MULTILINE)

# === COMBINED PAGE MARKDOWN HANDLER ===

@mkdocs.plugins.event_priority(105) # must run before include-markdown/macros, after mkdocs_hooks path prefixing
def on_page_markdown(markdown, page, config, files):
    """Insert sections for overlap (commands), inheritance (datatypes), frontmatter infobox, and project attribution."""
    
    # Process frontmatter infobox injection (highest priority - runs first)
    if _should_inject_frontmatter_infobox(page):
        markdown = _inject_frontmatter_infobox(markdown)
    
    # Process overlap commands (higher priority - runs second)
    if _DUPE_CONFIG:
        command = _extract_command_from_markdown(markdown)
        if command:
            cross_ref = _build_cross_reference(page, command, config)
            if cross_ref:
                # Insert immediately after the end of the Description block marker
                desc_end_match = re.search(r'<!--\s*cmd-desc-end\s*-->', markdown)
                if desc_end_match:
                    insert_pos = desc_end_match.end()
                    markdown = markdown[:insert_pos] + "\n\n" + cross_ref + "\n" + markdown[insert_pos:]
    
    # Process inheritance (lower priority - runs third)
    if _INHERITANCE_CONFIG:
        datatype = _extract_datatype_from_path(page.file.src_path)
        if datatype:
            inheritance_admonition = _build_inheritance_admonition(datatype, page, config)
            if inheritance_admonition:
                # Insert immediately after the end of the Members block marker
                members_end_match = re.search(r'<!--\s*dt-members-end\s*-->', markdown)
                if members_end_match:
                    insert_pos = members_end_match.end()
                    markdown = markdown[:insert_pos] + "\n\n" + inheritance_admonition + "\n" + markdown[insert_pos:]
    
    # Inject discussion links and project attribution into page metadata (for template access)
    _inject_discussion_links(page)
    
    # Process project attribution - store in page.meta for template rendering
    if _should_show_project_attribution(page):
        attribution_data = _build_project_attribution_data(page, config)
        if attribution_data:
            page.meta['project_attribution'] = attribution_data
    
    return markdown

# === DISCUSSION LINKS FUNCTIONALITY ===

def _inject_discussion_links(page):
    """Inject forum discussion links into page metadata for template access."""
    if not _THREAD_LINKS:
        return
    
    # Normalize the page URL (remove trailing slash, convert to lowercase)
    lookup_key = page.url.strip('/').lower()
    
    # Find matching discussion links
    found_links = _THREAD_LINKS.get(lookup_key, [])
    
    # Inject into page metadata for template access
    if found_links:
        page.meta['discussion_links'] = found_links
