import re
from pathlib import Path, PurePosixPath
import html
from markupsafe import Markup

def define_env(env):
    docs_dir_name = env.conf.get("docs_dir", "docs") # Get docs_dir name once

    @env.macro
    def renderMember(name, type=None, params=None, toc_label=None):
        if type == 'varies':
            type_str = '_varies_'
        elif type:
            type_str = f"[{type}][{type}]"
        else:
            type_str = ''

        params_str = f"[{params}]" if params else ""
        
        if toc_label is None:
            toc_label = f'{name}{params_str}'

        return f"{type_str} `{name}{params_str}` {{ #{toc_label} data-toc-label='{toc_label}' }}"

    # output a read more link if the file has sections beyond Members/Forms/Description
    @env.macro
    def readMore(doc_file):
        page = env.variables.page
        # Pass docs_dir_name to read_file
        file_result = read_file(doc_file, page, docs_dir_name) 
        
        if not file_result["success"]:
            return ""
            
        has_extra = has_extra_sections(file_result["content"])
        doc_url = file_result["doc_url"]
        
        if has_extra:
            return f'[:material-book-arrow-right-outline:]({doc_url} "Full documentation")'
        return ''

    # embed a wiki-like infobox using material's built-in admonitions. place after the title.
    @env.macro
    def frontmatter_infobox(expanded=True):
        page = env.variables.get("page")
        if not page or not getattr(page, "meta", None):
            return ""

        m = page.meta
        # Determine if this page is tagged as a plugin
        tags_value = m.get("tags")
        if isinstance(tags_value, str):
            tags_list = [tags_value.strip().lower()]
        elif isinstance(tags_value, (list, tuple)):
            tags_list = [str(t).strip().lower() for t in tags_value]
        else:
            tags_list = []
        is_plugin_page = "plugin" in tags_list
        
        # Use Material's admonition syntax for better styling
        admonition_type = "abstract" if expanded else "abstract collapsible"
        
        parts = [
            f'???+ {admonition_type} "{html.escape(page.title or "Info")}"',
            ""  # blank line after title
        ]

        # Tagline with emphasis
        if tagline := m.get("tagline"):
            parts.append(f"    *{html.escape(tagline)}*")
            parts.append("")

        # Combine authors and config on one line when both exist
        info_line_parts = []
        if authors := m.get("authors"):
            if isinstance(authors, str):
                authors_str = authors
            else:
                authors_str = ", ".join(authors)
            info_line_parts.append(f"**Authors:** {html.escape(authors_str)}")

        if cfg := m.get("config"):
            info_line_parts.append(f"**Config:** =={html.escape(cfg)}==")  # Badge syntax

        if info_line_parts:
            parts.append(f"    {' • '.join(info_line_parts)}")
            parts.append("")

        # Links as buttons in a compact format
        link_items = []
        if (url := m.get("resource_link")): 
            link_items.append(f'[:material-book: Resource]({html.escape(url)}){{ .md-button .md-button--primary }}')
            # Add download link if resource_link contains "redguides" domain
            if "redguides" in url:
                if is_plugin_page:
                    # Replace the download button with a tooltip button for plugins, inferring the plugin name
                    plugin_name = str(m.get("plugin_name") or m.get("name") or page.title or "plugin").strip().lower()
                    tooltip_text = f"Included in Very Vanilla by default. In-game, type '/plugin {plugin_name} load' to activate"
                    link_items.append(f'[:material-download: Download](#){{ .md-button onclick="alert(this.dataset.alert); return false;" data-alert="{html.escape(tooltip_text, quote=True)}" }}')
                else:
                    download_url = url.rstrip('/') + '/download'
                    link_items.append(f'[:material-download: Download]({html.escape(download_url)}){{ .md-button }}')
        
        if (url := m.get("support_link")):  
            link_items.append(f'[:material-help-circle: Support]({html.escape(url)}){{ .md-button }}')
        if (url := m.get("repository")):    
            link_items.append(f'[:material-source-repository: Repo]({html.escape(url)}){{ .md-button }}')
        if (url := m.get("quick_start")):   
            link_items.append(f'[:material-rocket-launch: Quick Start]({html.escape(url)}){{ .md-button }}')

        if link_items:
            parts.append(f"    {' '.join(link_items)}")

        return Markup("\n".join(parts))

# == Helper Functions ==

def read_file(file_path, page, docs_dir_name="docs"): # Add docs_dir_name parameter
    try:
        # Construct the full path from the project root to the document file
        # file_path is expected to be relative to docs_dir_name
        full_doc_path = Path(docs_dir_name) / file_path
        
        content = full_doc_path.read_text(encoding="utf-8") # Ensure UTF-8 encoding

        # doc_url should be calculated relative from the embedding page to the target file.
        # Both file_path (target) and page.file.src_uri (embedding) are relative to docs_dir.
        doc_url = relative_link(file_path, page.file.src_uri)
        
        return {
            "content": content,
            "base_dir": full_doc_path.parent, # Absolute path to the parent directory of the read file
            "doc_url": doc_url,
            "success": True,
            "error": None
        }
    except Exception:
        return {"content": "", "base_dir": None, "doc_url": "#", "success": False, "error": None}

# create an mkdocs-style url that's relative to the embedding page
def relative_link(target_file_path, embedding_page_src_uri, base_dir=None):
    # Absolute path of the target markdown file
    target_path = (PurePosixPath(base_dir) / target_file_path if base_dir else PurePosixPath(target_file_path))
    embedding_file = PurePosixPath(embedding_page_src_uri)
    
    if embedding_file.stem.lower() in ("readme", "index"):
        # main/guide/README.md   ->  main/guide/
        output_dir = embedding_file.parent
    else:
        # main/foo.md       ->  main/foo/
        output_dir = embedding_file.parent / embedding_file.stem

    # Relative path from the embedding page to the target file
    relative_path = target_path.relative_to(output_dir, walk_up=True)

    # strip .md, add trailing slash
    if relative_path.name.lower() in ("index.md", "readme.md"):
        parent_dir = relative_path.parent
        return "./" if str(parent_dir) == "." else f"{parent_dir}/"
    return f"{relative_path.with_suffix('')}/"

# extra sections beyond Members/Forms/Description
def has_extra_sections(content):
    SECTION_PATTERN = r'^##\s+(.+?)\s*$'
    target_sections = {"Syntax", "Members", "Forms", "Description", "Associated DataTypes", "DataTypes", "See also"}
    
    lines = content.split('\n')
    in_datatypes_section = False
    
    # Find all section headers and check if any are not in target_sections
    for i, line in enumerate(lines):
        if '<!--tlo-datatypes-start-->' in line:
            in_datatypes_section = True
        elif '<!--tlo-datatypes-end-->' in line:
            in_datatypes_section = False
            
        if not in_datatypes_section:
            match = re.match(SECTION_PATTERN, line)
            if match:
                section = match.group(1).strip()
                if section not in target_sections:
                    return True
        
    return False
