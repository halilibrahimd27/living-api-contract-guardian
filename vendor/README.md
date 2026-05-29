# Vendored Python wheels

This directory holds Python wheels pinned for offline CI/Docker builds.

`pip` is configured (via `pyproject.toml` build hooks and the CI workflow)
to look here first using `--find-links=vendor/wheels`. To refresh the
vendored set:

```bash
pip download -d vendor/wheels --python-version 3.11 \
    --platform manylinux2014_x86_64 \
    --only-binary=:all: \
    -r <(pip compile pyproject.toml)
```

The directory must always exist (even empty) so Docker builds and CI
runs can mount it without conditional logic.
