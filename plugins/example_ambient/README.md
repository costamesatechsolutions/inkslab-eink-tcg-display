# Example InkSlab Plugin

This is a starter folder for a future community plugin.

Today on the experimental branch, InkSlab can safely discover the
`manifest.json` file, show the plugin in `Setup > Apps`, and render its
settings UI from the declared schema without executing plugin code.

The live code-execution contract for third-party plugins is the next step of
the modular rewrite. For now, use this folder as the shape to build against.

## Folder Shape

```text
plugins/
  your_plugin_id/
    manifest.json
    __init__.py
```

## Current Rules

- `plugin_id` must be lowercase letters, numbers, `_`, or `-`
- keep settings small and user-friendly
- only public, slow-refresh, e-ink-friendly data sources are a good fit
- do not assume scrolling, live countdowns, or second-by-second updates

## Future Render Contract

The intended Python entry point is:

```python
def render(settings):
    """Return a Pillow image for the slab."""
```

That contract is not yet executed for community plugins, but this repo now
includes the manifest template so developers can start structuring plugins in a
consistent way.
