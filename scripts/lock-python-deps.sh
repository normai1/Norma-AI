#!/usr/bin/env bash
#
# Regenerate apps/<service>/requirements.lock from apps/<service>/requirements.txt.
#
# requirements.txt is the intent: the packages this service actually asks for,
# pinned, with a comment saying why each non-obvious one is there. The lock is
# the resolution: every package that ends up installed, transitive dependencies
# included, at the exact version a build produced. The Dockerfiles install from
# the lock, so two builds from one commit are the same image.
#
# Resolved in a throwaway python:3.12-slim container rather than by freezing a
# long-running service container. A container that has been up for days is not
# a clean resolve - anything pip-installed into it by hand is in the freeze too,
# and that is precisely the drift this file exists to stop.
#
# Run it after changing a requirements.txt, then rebuild and run that app's
# suite. The lock is a build artefact but it is committed: reproducibility is
# the whole point, and an uncommitted lock reproduces nothing.
#
# Linux/amd64 only, which is what the images are. The host virtualenv used to
# run the API suite on Windows is not covered and is not meant to be.

set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_IMAGE="python:3.12-slim"

# Git Bash on Windows rewrites anything that looks like a Unix path in a
# command line, so a container-side "/tmp/requirements.txt" arrives as a
# mangled Windows path and the mount silently lands somewhere else. Turning
# the rewriting off, and handing docker a Windows-shaped host path via
# cygpath, makes the same script work from Git Bash and from Linux.
export MSYS_NO_PATHCONV=1

if command -v cygpath >/dev/null 2>&1; then
    HOST_ROOT="$(cygpath -m "$(pwd)")"
else
    HOST_ROOT="$(pwd)"
fi

for service in api voice worker; do
    requirements="apps/${service}/requirements.txt"
    lock="apps/${service}/requirements.lock"

    if [ ! -f "$requirements" ]; then
        echo "skipping ${service}: no ${requirements}"
        continue
    fi

    echo "resolving ${service}..."

    # --no-cache-dir so a stale wheel cache cannot stand in for a real resolve.
    #
    # The shared package is mounted read-only and then copied to the path the
    # Dockerfiles use, rather than mounted there directly: an editable install
    # writes a norma_shared.egg-info directory beside the source, which fails
    # on a read-only mount and would otherwise litter the repository on a
    # writable one. Copying mirrors the Dockerfile's own COPY --from=shared.
    frozen=$(docker run --rm         -v "${HOST_ROOT}/${requirements}:/tmp/requirements.txt:ro"         -v "${HOST_ROOT}/packages/shared:/tmp/shared-src:ro"         "$PYTHON_IMAGE"         sh -c "mkdir -p /packages && cp -r /tmp/shared-src /packages/shared                && pip install --quiet --no-cache-dir --upgrade pip                && pip install --quiet --no-cache-dir -r /tmp/requirements.txt                && pip freeze --exclude-editable")

    {
        echo "# GENERATED FILE - do not edit by hand."
        echo "#"
        echo "# Every package installed for apps/${service}, transitive dependencies"
        echo "# included, at the versions a clean resolve of requirements.txt produced."
        echo "# Regenerate with scripts/lock-python-deps.sh, then rebuild and run the"
        echo "# suite. To change a version, change requirements.txt and regenerate -"
        echo "# editing this file directly makes the two disagree, and the Dockerfile"
        echo "# installs this one."
        echo "#"
        echo "# Resolved on ${PYTHON_IMAGE}, linux/amd64."
        echo ""
        echo "$frozen" | LC_ALL=C sort -f
        # apps/worker does not depend on the shared package, so this is
        # conditional rather than assumed - a lock naming a package the
        # Dockerfile never mounts would fail the build it is meant to make
        # reproducible.
        if grep -q '^-e /packages/shared' "$requirements"; then
            echo ""
            echo "# The package shared with the other plane, installed editable from"
            echo "# the build context docker-compose.yml mounts at /packages/shared."
            echo "# Not a versioned release, so it is carried here rather than frozen."
            echo "-e /packages/shared"
        fi
    } > "$lock"

    echo "  wrote ${lock} ($(grep -c '==' "$lock") packages)"
done
