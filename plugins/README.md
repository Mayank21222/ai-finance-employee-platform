# plugins/

Drop a `*.py` file here to add tools (and declare node types or dashboard
pages) without editing core code. The dashboard scans this directory at
startup.

A plugin file needs:

```python
from platform.dashboard.plugins import PluginManifest

MANIFEST = PluginManifest(
    name="my-plugin",
    version="1.0.0",
    description="what it adds",
    provides=("tool:my_tool",),
)

def register():
    # register tools with the normal tool registry API
    ...
    return []  # optional list of warning strings
```

Rules:

- A plugin that raises on import or in `register()` is logged and skipped;
  it never stops the dashboard from starting.
- Files starting with `_` are ignored.
- Everything is read-only from the platform's point of view: see the
  Plugins page in configuration mode for what loaded and what was skipped.
