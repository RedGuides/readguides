import re
import mkdocs.plugins

# Which paths should get the macro injection?
TARGET_PATH_PREFIXES = [
    "projects/aqo/",
    # "projects/other-repo/",  # add more as needed
]

@mkdocs.plugins.event_priority(210)  # runs after cleaner (115) but before your existing hooks
def on_page_markdown(markdown, page, **kwargs):
    """Inject frontmatter_infobox() macro call after the first H1."""
    
    # Check if this file is in one of our target paths
    # Normalize path separators to handle Windows vs Unix
    normalized_path = page.file.src_path.replace('\\', '/')
    if not any(normalized_path.startswith(prefix) for prefix in TARGET_PATH_PREFIXES):
        return markdown

    # Insert macro call after the first H1
    def _inject_macro(match):
        title_line = match.group(0)
        return f"{title_line}\n{{{{ frontmatter_infobox() }}}}"

    markdown = re.sub(
        r'^# .+$', 
        _inject_macro, 
        markdown, 
        count=1, 
        flags=re.MULTILINE
    )

    return markdown