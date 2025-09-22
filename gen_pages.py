"""
This script generates index pages for commands, TLOs, datatypes, plugins, and scripts
by scanning markdown files with specific tags and creating organized index pages.
"""

import mkdocs_gen_files
import re
import os
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional


# =============================================================================
# CONFIGURATION
# =============================================================================

# Special section handling via path prefixes (applies to all item types)
SPECIAL_SECTION_PREFIXES = [
    ("projects/macroquest/reference", "MacroQuest"),
    ("projects/everquest", "EverQuest"),
]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_docs_dir_path() -> Path:
    """Get the absolute path to the documentation directory."""
    try:
        docs_dir_abs_path = Path(mkdocs_gen_files.editor.FilesEditor.current.directory).resolve()
    except AttributeError:
        config_docs_dir = mkdocs_gen_files.config.get('docs_dir', 'docs')
        config_file_path = Path(mkdocs_gen_files.config.get('config_file_path', 'mkdocs.yml')).parent
        docs_dir_abs_path = (config_file_path / config_docs_dir).resolve()
        print(f"Warning: Using fallback for docs_dir path: {docs_dir_abs_path}")
    return docs_dir_abs_path


def has_tag(content: str, tag_name: str) -> bool:
    """Check if file content has a specific tag in its frontmatter."""
    frontmatter_match = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
    if frontmatter_match:
        frontmatter = frontmatter_match.group(1)
        return bool(re.search(rf'^\s*-\s*{re.escape(tag_name)}\b', frontmatter, re.MULTILINE | re.IGNORECASE))
    return False


def get_h1_title(file_path: Path) -> Optional[str]:
    """Extract the first H1 header from a markdown file."""
    if not file_path.is_file():
        return None
    
    try:
        content = file_path.read_text(encoding="utf-8")
        match = re.search(r"^\s*#\s+(.+)", content, re.MULTILINE)
        if match:
            return match.group(1).strip()
    except Exception as e:
        print(f"Warning: Could not read file {file_path}: {e}")
    return None


def get_page_link(file_path_relative_to_docs: str) -> str:
    """Generate site-absolute link for a page."""
    p = PurePosixPath(file_path_relative_to_docs)
    if p.name.lower() in ("index.md", "readme.md"):
        parent_str = str(p.parent)
        if parent_str == ".":
            return "/"
        return f"/{parent_str}/"
    return f"/{p.with_suffix('')}/"


def get_relative_link(target_file_abs: Path, output_file_dir_abs: Path, fallback_link: str) -> str:
    """Calculate relative path for links, with fallback for cross-drive scenarios."""
    try:
        return Path(os.path.relpath(target_file_abs, output_file_dir_abs)).as_posix()
    except ValueError:
        return fallback_link


def get_section_title(item_path: Path, docs_dir_path: Path) -> str:
    """Determine section title for an item based on its path."""
    parent_dir_relative = item_path.parent.as_posix()

    for prefix, section_name in SPECIAL_SECTION_PREFIXES:
        if parent_dir_relative == prefix or parent_dir_relative.startswith(prefix + '/'):
            return section_name

    parent_dir_abs_path = docs_dir_path / item_path.parent
    for filename in ["README.md", "index.md"]:
        h1_title = get_h1_title(parent_dir_abs_path / filename)
        if h1_title:
            return h1_title

    raise ValueError(f"Could not determine section title for {item_path}")


def section_sort_key(section_title: str) -> str:
    """Normalize section titles for sorting by trimming MQ/MQ2 prefixes."""
    title_lower = section_title.lower()
    if title_lower.startswith('mq2'):
        return section_title[3:]
    if title_lower.startswith('mq'):
        return section_title[2:]
    return section_title


def build_relative_link_md(entry: Dict, output_file_path: str, docs_dir_path: Path) -> str:
    """Build a markdown-relative link to an entry from the output file location."""
    output_file_dir_abs = (docs_dir_path / Path(output_file_path).parent).resolve()
    target_file_abs = entry['abs_path']
    return get_relative_link(target_file_abs, output_file_dir_abs, entry['link'])


# =============================================================================
# FILE SCANNING
# =============================================================================

def find_tagged_files(tag_name: str, docs_dir_path: Path) -> List[Dict]:
    """Find all files with a specific tag and return their metadata."""
    entries = []
    exclude_specification = mkdocs_gen_files.config.get('exclude_docs')
    
    print(f"Searching for '{tag_name}' files in: {docs_dir_path}")
    
    for file_path_abs in docs_dir_path.glob("**/*.md", recurse_symlinks=True):
        path_relative_to_docs_str = str(file_path_abs.relative_to(docs_dir_path).as_posix())
        
        # Check exclusions
        if exclude_specification and hasattr(exclude_specification, 'match_file'):
            if exclude_specification.match_file(path_relative_to_docs_str):
                continue
        
        try:
            content = file_path_abs.read_text(encoding="utf-8")
        except Exception as e:
            print(f"Warning: Could not read file {file_path_abs}: {e}")
            content = ""
        if content and has_tag(content, tag_name):
            # Get H1 title
            h1_title = get_h1_title(file_path_abs)
            if not h1_title:
                h1_title = file_path_abs.stem.replace('tlo-', '').replace('datatype-', '').replace('-', ' ').replace('_', ' ').title()
            
            entry = {
                'path': path_relative_to_docs_str,
                'link': get_page_link(path_relative_to_docs_str),
                'title': h1_title,
                'abs_path': file_path_abs,
                'content': content
            }
            entries.append(entry)
    
    return sorted(entries, key=lambda x: section_sort_key(x['title']).lower())


# =============================================================================
# MARKDOWN GENERATION
# =============================================================================

def generate_command_index(entries: List[Dict], docs_dir_path: Path) -> str:
    """Generate command index with sections."""
    if not entries:
        return "No command files found to index."
    
    # Group by sections
    sections = {}
    for entry in entries:
        cmd_path_obj = Path(entry['path'])
        section_title = get_section_title(cmd_path_obj, docs_dir_path)
        if section_title not in sections:
            sections[section_title] = []
        sections[section_title].append(entry)
    
    # Sort sections alphabetically, ignoring MQ/MQ2 prefixes
    sorted_sections = sorted(sections.items(), key=lambda x: section_sort_key(x[0]).lower())
    
    # Build markdown
    parts = []
    for section_title, section_entries in sorted_sections:
        if parts:
            parts.append("")
        parts.append(f"## {section_title}")
        
        for entry in section_entries:
            macro_path = entry['path']
            href_link = build_relative_link_md(entry, "commands/index.md", docs_dir_path)
            
            command_block = f"""
<a href="{href_link}">
{{%
  include-markdown "{macro_path}"
  start="<!--cmd-syntax-start-->"
  end="<!--cmd-syntax-end-->"
%}}
</a>
:    {{% 
        include-markdown "{macro_path}"
        start="<!--cmd-desc-start-->"
        end="<!--cmd-desc-end-->"
        trailing-newlines=false
     %}} {{{{ readMore('{macro_path}') }}}}
"""
            parts.append(command_block)
    
    return "\n".join(parts)


def generate_plugins_scripts_index(entries: List[Dict], item_type: str, output_file_path: str, docs_dir_path: Path) -> str:
    """Generate simple flat index for plugins/scripts."""
    if not entries:
        return f"No {item_type}s found to index."
    
    parts = []
    for entry in entries:
        macro_path = entry['path']
        h1_title = entry['title']
        
        # Calculate relative link
        relative_link_md = build_relative_link_md(entry, output_file_path, docs_dir_path)
        
        header = f"### [{h1_title}]({relative_link_md})"
        
        # Try to include description
        desc_start, desc_end = "<!--desc-start-->", "<!--desc-end-->"
        if desc_start in entry['content'] and desc_end in entry['content']:
            description = (
                f"{{% \n    include-markdown \"{macro_path}\" \n    start=\"{desc_start}\" \n    end=\"{desc_end}\" \n    trailing-newlines=false\n%}} "
                f"{{{{ readMore('{macro_path}') }}}}"
            )
        else:
            description = "No description available."
        
        parts.append(f"{header}\n:    {description}")
    
    return "\n\n".join(parts)


def generate_tlo_datatypes_index(entries: List[Dict], item_type: str, output_file_path: str, docs_dir_path: Path) -> str:
    """Generate complex sectioned index for TLOs/DataTypes."""
    if not entries:
        return f"No {item_type} files found to index."
    
    # Group by sections
    sections = {}
    for entry in entries:
        section_title = get_section_title(Path(entry['path']), docs_dir_path)
        if section_title not in sections:
            sections[section_title] = []
        sections[section_title].append(entry)
    
    # Sort sections alphabetically (ignoring MQ/MQ2 prefixes) and entries within each section
    for section_entries in sections.values():
        section_entries.sort(key=lambda x: section_sort_key(x['title']).lower())
    
    # Sort sections alphabetically, ignoring MQ/MQ2 prefixes
    sorted_sections = sorted(sections.items(), key=lambda x: section_sort_key(x[0]).lower())
    
    # Build markdown
    parts = []
    for section_title, section_entries in sorted_sections:
        if parts:
            parts.append("")
        parts.append(f"## {section_title}")
        
        for entry in section_entries:
            item_md = generate_type_item(entry, item_type, output_file_path, docs_dir_path)
            parts.append(item_md)
    
    return "\n\n".join(parts)


def generate_type_item(entry: Dict, item_type: str, output_file_path: str, docs_dir_path: Path) -> str:
    """Generate markdown for a single complex item (TLO or DataType)."""
    macro_path = entry['path']
    h1_title = entry['title']
    content = entry['content']
    
    # Calculate relative link
    relative_link_md = build_relative_link_md(entry, output_file_path, docs_dir_path)
    
    parts = [f"### [{h1_title}]({relative_link_md})"]
    
    # Add description
    if item_type == "tlo":
        desc_start, desc_end = "<!--tlo-desc-start-->", "<!--tlo-desc-end-->"
    else:  # datatype
        desc_start, desc_end = "<!--dt-desc-start-->", "<!--dt-desc-end-->"
    
    if desc_start in content and desc_end in content:
        desc_line = (
            f"{{% include-markdown \"{macro_path}\" start=\"{desc_start}\" end=\"{desc_end}\" trailing-newlines=false %}} "
            f"{{{{ readMore('{macro_path}') }}}}"
        )
        parts.append(desc_line)
    
    # Add content includes
    content_includes = []
    if item_type == "tlo":
        # Forms block
        forms_start, forms_end = "<!--tlo-forms-start-->", "<!--tlo-forms-end-->"
        if forms_start in content and forms_end in content:
            content_includes.append(
                f"{{% include-markdown \"{macro_path}\" start=\"{forms_start}\" end=\"{forms_end}\" heading-offset=1 %}}"
            )
        
        # Link references
        linkrefs_start, linkrefs_end = "<!--tlo-linkrefs-start-->", "<!--tlo-linkrefs-end-->"
        if linkrefs_start in content and linkrefs_end in content:
            content_includes.append(
                f"{{% include-markdown \"{macro_path}\" start=\"{linkrefs_start}\" end=\"{linkrefs_end}\" %}}"
            )
    else:  # datatype
        # Members block
        members_start, members_end = "<!--dt-members-start-->", "<!--dt-members-end-->"
        if members_start in content and members_end in content:
            content_includes.append(
                f"{{% include-markdown \"{macro_path}\" start=\"{members_start}\" end=\"{members_end}\" heading-offset=1 %}}"
            )
        
        # Link references
        linkrefs_start, linkrefs_end = "<!--dt-linkrefs-start-->", "<!--dt-linkrefs-end-->"
        if linkrefs_start in content and linkrefs_end in content:
            content_includes.append(
                f"{{% include-markdown \"{macro_path}\" start=\"{linkrefs_start}\" end=\"{linkrefs_end}\" %}}"
            )
    
    if content_includes:
        inner_block = "\n\n".join(content_includes)
        parts.append(f'<div class="indent" markdown="1">\n{inner_block}\n</div>')
    
    return "\n\n".join(parts)


# =============================================================================
# MAIN PROCESSING
# =============================================================================

def write_generated_content(content: str, output_file: str, start_marker: str, end_marker: str, 
                           script_name: str, docs_dir_path: Path):
    """Write generated content between markers in an index file."""
    output_file_abs_path = docs_dir_path / output_file
    
    generated_comment = f"<!-- Content between these markers is automatically generated by {script_name}. Do not edit this section manually. -->"
    content_to_insert = content.strip() if content else ""
    
    if content_to_insert:
        replacement_block_inner = f"{generated_comment}\n{content_to_insert}"
    else:
        replacement_block_inner = generated_comment
    
    replacement_block = f"{start_marker}\n{replacement_block_inner}\n{end_marker}"
    marker_regex = re.compile(f"{re.escape(start_marker)}.*?{re.escape(end_marker)}", re.DOTALL)
    
    # Get updated content
    try:
        if output_file_abs_path.exists():
            existing_content = output_file_abs_path.read_text(encoding="utf-8")
            match = marker_regex.search(existing_content)
            
            if match:
                # Replace existing block
                pre_block = existing_content[:match.start()]
                post_block = existing_content[match.end():]
                final_content = pre_block + replacement_block + post_block
            else:
                # No markers found - append to existing content
                final_content = existing_content.rstrip() + "\n\n" + replacement_block + "\n"
        else:
            # Create new file
            final_content = replacement_block + "\n"
                
    except IOError as e:
        print(f"Error reading {output_file_abs_path}: {e}. Creating/overwriting file.")
        final_content = replacement_block + "\n"
    
    # Write the file
    with mkdocs_gen_files.open(output_file, "w", encoding="utf-8") as f:
        f.write(final_content)


def generate_commands_index(docs_dir_path: Path, script_name: str):
    """Generate the commands index."""
    print("\nGenerating commands index...")
    
    entries = find_tagged_files('command', docs_dir_path)
    
    if entries:
        print(f"Found {len(entries)} command files.")
        content = generate_command_index(entries, docs_dir_path)
    else:
        print("No command files found.")
        content = "No command files found to index."
    
    write_generated_content(
        content, 
        "commands/index.md",
        "<!-- BEGIN GENERATED COMMANDS -->",
        "<!-- END GENERATED COMMANDS -->",
        script_name, 
        docs_dir_path
    )
    
    print("Commands index generation complete.")


def generate_tlos_index(docs_dir_path: Path, script_name: str):
    """Generate the TLOs index."""
    print("\nGenerating TLOs index...")
    
    entries = find_tagged_files('tlo', docs_dir_path)
    
    if entries:
        print(f"Found {len(entries)} TLO files.")
        content = generate_tlo_datatypes_index(entries, 'tlo', "tlos/index.md", docs_dir_path)
    else:
        print("No TLO files found.")
        content = "No TLO files found to index."
    
    write_generated_content(
        content,
        "tlos/index.md", 
        "<!-- BEGIN GENERATED TLOs -->",
        "<!-- END GENERATED TLOs -->",
        script_name,
        docs_dir_path
    )
    
    print("TLOs index generation complete.")


def generate_datatypes_index(docs_dir_path: Path, script_name: str):
    """Generate the datatypes index."""
    print("\nGenerating datatypes index...")
    
    entries = find_tagged_files('datatype', docs_dir_path)
    
    if entries:
        print(f"Found {len(entries)} datatype files.")
        content = generate_tlo_datatypes_index(entries, 'datatype', "datatypes/index.md", docs_dir_path)
    else:
        print("No datatype files found.")
        content = "No datatype files found to index."
    
    write_generated_content(
        content,
        "datatypes/index.md",
        "<!-- BEGIN GENERATED datatypes -->", 
        "<!-- END GENERATED datatypes -->",
        script_name,
        docs_dir_path
    )
    
    print("Datatypes index generation complete.")


def generate_plugins_index(docs_dir_path: Path, script_name: str):
    """Generate the plugins index."""
    print("\nGenerating plugins index...")
    
    entries = find_tagged_files('plugin', docs_dir_path)
    
    if entries:
        print(f"Found {len(entries)} plugin files.")
        content = generate_plugins_scripts_index(entries, 'plugin', "plugins/index.md", docs_dir_path)
    else:
        print("No plugin files found.")
        content = "No plugins found to index."
    
    write_generated_content(
        content,
        "plugins/index.md",
        "<!-- BEGIN GENERATED plugins -->",
        "<!-- END GENERATED plugins -->", 
        script_name,
        docs_dir_path
    )
    
    print("Plugins index generation complete.")


def generate_scripts_index(docs_dir_path: Path, script_name: str):
    """Generate the scripts index."""
    print("\nGenerating scripts index...")
    
    entries = find_tagged_files('script', docs_dir_path)
    
    if entries:
        print(f"Found {len(entries)} script files.")
        content = generate_plugins_scripts_index(entries, 'script', "scripts/index.md", docs_dir_path)
    else:
        print("No script files found.")
        content = "No scripts found to index."
    
    write_generated_content(
        content,
        "scripts/index.md",
        "<!-- BEGIN GENERATED scripts -->",
        "<!-- END GENERATED scripts -->",
        script_name,
        docs_dir_path
    )
    
    print("Scripts index generation complete.")


def main():
    """Generate all content indexes."""
    print("Starting documentation index generation...")
    
    docs_dir_path = get_docs_dir_path()
    script_name = Path(__file__).name
    
    # Generate each index type
    generate_commands_index(docs_dir_path, script_name)
    generate_tlos_index(docs_dir_path, script_name) 
    generate_datatypes_index(docs_dir_path, script_name)
    generate_plugins_index(docs_dir_path, script_name)
    generate_scripts_index(docs_dir_path, script_name)
    
    print("\nDocumentation index generation complete!")


if __name__ == "__main__":
    main()
