"""Bundled Jinja prompt templates for migration-guide generation.

Templates are loaded via :func:`importlib.resources.files` so the
package works correctly when installed from a wheel as well as from a
source checkout. Each template is content-addressed by
:data:`guardian_guides.service.PROMPT_VERSION` — bumping that constant
opts every guide into a fresh cache key on the next request.
"""
