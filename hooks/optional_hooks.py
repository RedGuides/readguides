import importlib.util
from pathlib import Path

# List of optional hook files to load
_optional_hooks = ["rg_hooks.py"]

for hook_file in _optional_hooks:
    _private = Path(__file__).with_name(hook_file)
    
    if _private.is_file():
        module_name = hook_file.replace(".py", "")
        spec = importlib.util.spec_from_file_location(module_name, str(_private))
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        # Export any MkDocs event handlers found in the private module
        for name in dir(mod):
            if name.startswith("on_"):
                globals()[name] = getattr(mod, name)
# If the files don't exist, we simply export nothing and MkDocs proceeds without these hooks.