import mkdocs.plugins
from pathlib import Path
import shutil
import os

# Directory containing your overlay files (relative to project root)
OVERLAY_ROOT = Path("metadata")

@mkdocs.plugins.event_priority(500)  # Run very early, before meta plugin
def on_pre_build(config):
    """Copy .meta.yml overlay files into their target locations before build,
    but only when the content is different, to avoid an endless rebuild loop
    when using `mkdocs serve`."""
    
    docs_dir = Path(config["docs_dir"]).resolve()
    
    if not OVERLAY_ROOT.exists():
        print(f"Warning: Overlay directory {OVERLAY_ROOT} not found")
        return
    
    print(f"Copying overlay files from {OVERLAY_ROOT}...")
    
    overlay_files = list(OVERLAY_ROOT.rglob("*.yml")) + list(OVERLAY_ROOT.rglob("*.yaml"))
    copied = 0
    
    for overlay_file in overlay_files:
        rel_path   = overlay_file.relative_to(OVERLAY_ROOT)
        target_path = docs_dir / rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        # --- only copy when content is different ---------------------------
        if not target_path.exists() or not files_match(overlay_file, target_path):
            shutil.copy2(overlay_file, target_path)
            copied += 1
            print(f"  Copied: {rel_path}")
        # -------------------------------------------------------------------
    
    print(f"Overlay copy complete: {copied} file{'s' if copied != 1 else ''} updated")


def files_match(src: Path, dst: Path) -> bool:
    """Return True if two files have identical byte content, False otherwise."""
    if src.stat().st_size != dst.stat().st_size:
        return False
    # compare in chunks to avoid loading large files entirely into memory
    bufsize = 8192
    with src.open('rb') as f1, dst.open('rb') as f2:
        while True:
            b1 = f1.read(bufsize)
            b2 = f2.read(bufsize)
            if b1 != b2:
                return False
            if not b1:        # EOF reached
                return True