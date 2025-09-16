import re
import mkdocs.plugins
import pathlib  # Import the pathlib module

# for macroquest docs to link correctly
PREFIX = "projects/macroquest/"

# This adds a directory prefix to "include-markdown" and "readMore" paths.
@mkdocs.plugins.event_priority(110) # has to be higher than 100 due to plugins with priority 100
def on_page_markdown(markdown: str, page, config, files):
    # Use pathlib so this works on Windows
    src_path = pathlib.Path(page.file.src_path).as_posix()

    # Only process files in the MacroQuest docs directory
    if not src_path.startswith(PREFIX):
        return markdown

    # Treat some paths as "rooted" (don’t prefix)
    def _is_rooted(p: str) -> bool:
        return (
            p.startswith(PREFIX)               # already macroquest-rooted
            or p.startswith("projects/")      # root of umbrella docs
            or p.startswith("docs/")          # explicit docs root
            or p.startswith("/")              # absolute-style
            or re.match(r"^[a-zA-Z]+://", p)  # URLs
        )

    # 1) Prefix include-markdown paths
    def _inc_repl(m):
        orig, path = m.group(0), m.group(1)
        if _is_rooted(path):
            return orig
        # replace only the quoted path
        return orig.replace(f'"{path}"', f'"{PREFIX}{path}"')

    markdown = re.sub(
        r'{%\s*include-markdown\s*"([^"]+)"',
        _inc_repl,
        markdown
    )

    # 2) Prefix readMore() calls
    def _rm_repl(m):
        orig, path = m.group(0), m.group(1)
        if _is_rooted(path):
            return orig
        return orig.replace(f"'{path}'", f"'{PREFIX}{path}'")

    markdown = re.sub(
        r'\{\{\s*readMore\(\s*[\'"]([^\'"]+)[\'"]\s*\)\s*\}\}',
        _rm_repl,
        markdown
    )

    return markdown

# Hook to override edit URL using page-specific repo_url and path adjustment
@mkdocs.plugins.event_priority(0) #default priority
def on_page_context(context, page, config, nav):
    """
    Constructs edit URLs using page-specific repo_url and path adjustments
    without affecting the header repository link.
    
    Metadata fields:
    - docs_repository: Documentation repo (where edit button should go)
    - docs_edit_uri: Edit URI for docs repo (defaults to config.edit_uri)
    - docs_file_path: Complete override of file path for edit URL
    - docs_path_transform: Dict with 'from' and 'to' keys for path transformation
    - edit_uri_strip_dirs: Number of directory levels to strip from file path (legacy)
    - repository: Backwards compatibility fallback
    - edit_uri: Backwards compatibility fallback
    
    Example docs_path_transform:
    docs_path_transform:
      from: "docs/projects/aqo/"
      to: "docs/"
    """
    # Determine the documentation repository for edit URLs
    # Priority: docs_repository > repository (backwards compat) > global config
    docs_repo_url = (
        page.meta.get("docs_repository") or 
        page.meta.get("repository") or 
        config.repo_url
    )
    
    # Determine the edit URI
    # Priority: docs_edit_uri > edit_uri (backwards compat) > global config
    edit_uri = (
        page.meta.get("docs_edit_uri") or 
        page.meta.get("edit_uri") or 
        config.edit_uri
    )
    
    # Determine the file path for the edit URL
    if "docs_file_path" in page.meta:
        # Complete override of file path
        file_src_uri = page.meta["docs_file_path"]
    else:
        # Use original path with optional transformations
        file_src_uri = page.file.src_uri
        
        # Apply path transformation if specified
        if "docs_path_transform" in page.meta:
            transform = page.meta["docs_path_transform"]
            if isinstance(transform, dict) and "from" in transform and "to" in transform:
                from_path = transform["from"]
                to_path = transform["to"]
                if file_src_uri.startswith(from_path):
                    file_src_uri = to_path + file_src_uri[len(from_path):]
        
        # Legacy directory stripping (applied after path transformation)
        if "edit_uri_strip_dirs" in page.meta:
            file_src_uri = "/".join(file_src_uri.split("/")[page.meta["edit_uri_strip_dirs"]:])

    # Only build edit_url if we have the required components
    if docs_repo_url and edit_uri:
        page.edit_url = f"{docs_repo_url.rstrip('/')}/{edit_uri}{file_src_uri}"
    
    # Construct full URL to the original docs page if docs_site is specified
    if "docs_site" in page.meta:
        base_url = page.meta["docs_site"].rstrip('/')
        
        # Use the same transformed path we calculated for the edit_url
        relative_path = pathlib.PurePosixPath(file_src_uri)
        
        # Convert file path to URL path segment
        if relative_path.name.lower() in ("index.md", "readme.md"):
            url_segment = str(relative_path.parent)
            if url_segment == ".":
                url_segment = ""
        else:
            url_segment = str(relative_path.with_suffix(''))
            
        # Ensure trailing slash for non-empty paths
        if url_segment:
            url_segment += '/'
            
        page.meta["original_docs_url"] = f"{base_url}/{url_segment}"
        
    return context