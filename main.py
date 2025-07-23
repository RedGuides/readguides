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
        
        # Collapsible structure - just details element
        parts = [
            f'<details class="info inline end"{" open" if expanded else ""}>',
            f'  <summary>{html.escape(page.title or "Info")}</summary>',
        ]

        if tagline := m.get("tagline"):
            parts.append(f"  <p><em>{html.escape(tagline)}</em></p>")

        if authors := m.get("authors"):
            if isinstance(authors, str):
                authors_str = authors
            else:
                authors_str = ", ".join(authors)
            parts.append(f"  <p><strong>Authors:</strong> {html.escape(authors_str)}</p>")

        if cfg := m.get("config"):
            parts.append(f"  <p><strong>Config:</strong> <code>{html.escape(cfg)}</code></p>")

        link_items = []
        if (url := m.get("resource_link")): 
            link_items.append(f'📕 <a href="{html.escape(url)}">Resource</a>')
        if (url := m.get("support_link")):  
            link_items.append(f'🧑‍🤝‍🧑 <a href="{html.escape(url)}">Support</a>')
        if (url := m.get("repository")):    
            link_items.append(f'⚙️ <a href="{html.escape(url)}">Git&nbsp;Repo</a>')
        if (url := m.get("quick_start")):   
            link_items.append(f'💡 <a href="{html.escape(url)}">Quick&nbsp;Start</a>')

        if link_items:
            parts.append(f"  <p><strong>Links:</strong> {' · '.join(link_items)}</p>")

        parts.append('</details>')
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
    except FileNotFoundError:
        # More specific error for file not found
        project_root_cwd = Path.cwd() # CWD is usually project root for MkDocs
        expected_path_abs = project_root_cwd / docs_dir_name / file_path
        error_msg = f"File not found by read_file (called by readMore): '{file_path}'. Expected absolute path: '{expected_path_abs}'"
        # print(f"DEBUG: {error_msg}") # Uncomment for debugging
        return {
            "content": "", "base_dir": None, "doc_url": "#", "success": False, "error": error_msg
        }
    except Exception as e:
        project_root_cwd = Path.cwd()
        attempted_path_abs = project_root_cwd / docs_dir_name / file_path
        error_msg = f"Error reading file '{file_path}' (attempted absolute path: '{attempted_path_abs}') in read_file (called by readMore): {e}"
        # print(f"DEBUG: {error_msg}") # Uncomment for debugging
        return {
            "content": "", "base_dir": None, "doc_url": "#", "success": False, "error": error_msg
        }

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
    target_sections = {"Members", "Forms", "Description", "Associated DataTypes", "DataTypes"}
    
    lines = content.split('\n')
    sections = []
    in_datatypes_section = False  # Initialize the flag here
    
    # Find all section headers and their positions
    for i, line in enumerate(lines):
        if '<!--tlo-datatypes-start-->' in line:
            in_datatypes_section = True
        elif '<!--tlo-datatypes-end-->' in line:
            in_datatypes_section = False
            
        if not in_datatypes_section:
            match = re.match(SECTION_PATTERN, line)
            if match:
                sections.append((i, match.group(1).strip()))
    
    # Find last occurrence of target sections
    last_target_index = -1
    for idx, (line_num, section) in enumerate(sections):
        if section in target_sections:
            last_target_index = idx
    
    # Check if there are sections after the last target section
    if last_target_index != -1 and len(sections) > last_target_index + 1:
        return True
        
    return False
