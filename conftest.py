import os

# pytest loads the rootdir conftest before collecting anything, so this runs
# before any app module builds its settings. Settings treat an unset ENVIRONMENT
# as unsafe and reject the placeholder signing key, and a CI checkout has no .env
# at all, so the test run declares itself here rather than relying on a file
# being present.
os.environ.setdefault("ENVIRONMENT", "development")
