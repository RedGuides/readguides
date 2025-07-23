import re
import mkdocs.plugins
import pathlib  # Import the pathlib module

# for macroquest docs to link correctly
PREFIX = "projects/macroquest/"

# This adds a directory prefix to "include-markdown" and "readMore" paths.
@mkdocs.plugins.event_priority(110) # larger number runs first in mkdocs.
def on_page_markdown(markdown: str, page, config, files):
    # Use pathlib so this works on Windows
    src_path = pathlib.Path(page.file.src_path).as_posix()

    # Only process files in the MacroQuest docs directory
    if not src_path.startswith(PREFIX):
        return markdown

    # 1) Prefix include-markdown paths
    def _inc_repl(m):
        orig, path = m.group(0), m.group(1)
        if path.startswith(PREFIX):
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
        if path.startswith(PREFIX):
            return orig
        return orig.replace(f"'{path}'", f"'{PREFIX}{path}'")

    markdown = re.sub(
        r'\{\{\s*readMore\(\s*[\'"]([^\'"]+)[\'"]\s*\)\s*\}\}',
        _rm_repl,
        markdown
    )

    return markdown

# Hook to override edit URL using page-specific repo_url and path adjustment
@mkdocs.plugins.event_priority(500)
def on_page_context(context, page, config, nav):
    """
    Constructs edit URLs using page-specific repo_url and path adjustments
    without affecting the header repository link
    """
    # Use page-specific repo_url if available, otherwise keep global config
    repo_url = page.meta.get("repository", config.repo_url)
    edit_uri = page.meta.get("edit_uri", config.edit_uri)
    
    # Adjust file path if needed
    file_src_uri = page.file.src_uri
    if "edit_uri_strip_dirs" in page.meta:
        file_src_uri = "/".join(file_src_uri.split("/")[page.meta["edit_uri_strip_dirs"]:])

    # Only build edit_url if we have the required components
    if repo_url and edit_uri:
        page.edit_url = f"{repo_url.rstrip('/')}/{edit_uri}{file_src_uri}"
    
    return context