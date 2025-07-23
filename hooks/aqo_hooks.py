import re
import mkdocs.plugins
from pathlib import Path

# Which part of your docs tree contains the vendored AQO repo?
AQO_PATH_PREFIX = "projects/aqo/"

# Regex that matches one of the link-lines AQO puts at the top
#    [View Repo](https://github.com/…){target=_blank}
#    [Download](https://github.com/…)
_LINK_LINE_RE = re.compile(
    r'^\[([^\]]+)\]\((https?://[^\)]+)\)(?:\{[^\}]*\})?\s*$',
    re.MULTILINE
)

@mkdocs.plugins.event_priority(115)  # runs before macro injector (110) and your existing hooks
def on_page_markdown(markdown, page, **kwargs):
    """Remove AQO-style link blocks."""
    
    # Only operate on files inside the AQO sub-module
    if not page.file.src_path.startswith(AQO_PATH_PREFIX):
        return markdown

    # Strip the link-block
    links = _LINK_LINE_RE.findall(markdown)

    if links:
        print(f"Found {len(links)} links in {page.file.src_path}")  # Debug output
        # Remove the complete block of consecutive link-lines at the top
        markdown = re.sub(
            rf'^{_LINK_LINE_RE.pattern}(\n{_LINK_LINE_RE.pattern})*',
            '', 
            markdown, 
            count=1, 
            flags=re.MULTILINE
        ).lstrip()  # Remove leading whitespace

    return markdown