# Example InkSlab Plugin

This is a starter folder for a future community plugin.

Today on the experimental branch, InkSlab can:

- discover the `manifest.json`
- show the plugin in `Setup > Apps`
- render its settings UI from the declared schema
- execute `render(settings, context)` from `__init__.py`

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

## Render Contract

The current Python entry point is:

```python
def render(settings, context):
    """Return a Pillow image, (image, payload), or {"image": image, ...}."""
```

Useful payload keys:

- `name`
- `set_info`
- `card_num`
- `rarity`
- `wait_seconds`

The example plugin in this folder is runnable and can be used as a starting
point for community development.
