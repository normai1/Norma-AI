#!/usr/bin/env bash
#
# Regenerate apps/<service>/requirements.lock from apps/<service>/requirements.txt.
#
# requirements.txt is the intent: the packages this service actually asks for,
# pinned, with a comment saying why each non-obvious one is there. The lock is
# the resolution: every package that ends up installed, transitive dependencies
# included, at the exact version *and artefact hash* a build produced. The
# Dockerfiles install the lock with --require-hashes, so two builds from one
# commit install byte-identical wheels.
#
# Resolved in a throwaway python:3.12-slim container rather than by freezing a
# long-running service container. A container that has been up for days is not
# a clean resolve - anything pip-installed into it by hand is in the freeze too,
# and that is precisely the drift this file exists to stop.
#
# The hashes come from pip's own install report, so each one is the artefact
# this resolve actually downloaded, rather than a hash looked up separately for
# the same version number.
#
# Run it after changing a requirements.txt, then rebuild and run that app's
# suite. The lock is a build artefact but it is committed: reproducibility is
# the whole point, and an uncommitted lock reproduces nothing.
#
# Linux/amd64, CPython 3.12 - which is what the images are. One hash per
# package, for the artefact that platform resolves to, so a build elsewhere
# fails loudly rather than quietly installing something else. The host
# virtualenv used to run the API suite on Windows is not covered by these and
# is not meant to be.

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
    #
    # The emitter skips anything with no downloaded artefact to hash, which in
    # practice is only that editable package - see the note at the end.
    body=$(docker run --rm \
        -v "${HOST_ROOT}/${requirements}:/tmp/requirements.txt:ro" \
        -v "${HOST_ROOT}/packages/shared:/tmp/shared-src:ro" \
        "$PYTHON_IMAGE" \
        sh -c 'mkdir -p /packages && cp -r /tmp/shared-src /packages/shared \
               && pip install --quiet --no-cache-dir --upgrade pip \
               && pip install --quiet --no-cache-dir --report /tmp/report.json \
                      -r /tmp/requirements.txt \
               && python -c "
import json, sys

report = json.load(open(\"/tmp/report.json\"))
rows = []

for item in report.get(\"install\", []):
    metadata = item.get(\"metadata\", {})
    archive = item.get(\"download_info\", {}).get(\"archive_info\", {})
    sha256 = archive.get(\"hashes\", {}).get(\"sha256\")

    if not sha256:
        print(\"note: no artefact hash for \" + str(metadata.get(\"name\")), file=sys.stderr)
        continue

    rows.append((metadata[\"name\"].lower(), metadata[\"name\"], metadata[\"version\"], sha256))

for _, name, version, sha256 in sorted(rows):
    print(name + \"==\" + version + \" \\\\\")
    print(\"    --hash=sha256:\" + sha256)
"')

    {
        echo "# GENERATED FILE - do not edit by hand."
        echo "#"
        echo "# Every package installed for apps/${service}, transitive dependencies"
        echo "# included, at the version and artefact hash a clean resolve of"
        echo "# requirements.txt produced. The Dockerfile installs this with"
        echo "# --require-hashes, so pip refuses anything whose bytes differ from"
        echo "# what was resolved here."
        echo "#"
        echo "# Regenerate with scripts/lock-python-deps.sh, then rebuild and run the"
        echo "# suite. To change a version, change requirements.txt and regenerate -"
        echo "# editing this file directly makes the two disagree, and the Dockerfile"
        echo "# installs this one."
        echo "#"
        echo "# Resolved on ${PYTHON_IMAGE}, linux/amd64. One hash per package, for"
        echo "# the artefact that platform resolves to: a build elsewhere fails"
        echo "# loudly rather than quietly installing something else."
        echo ""
        echo "$body"
    } > "$lock"

    echo "  wrote ${lock} ($(grep -c -- '--hash=' "$lock") hashed packages)"
done

echo
echo "The editable package at /packages/shared is deliberately absent from the"
echo "locks: --require-hashes rejects unhashed requirements, and a local source"
echo "tree copied in from the build context has no meaningful artefact hash."
echo "The Dockerfiles install it separately with --no-deps, its own dependencies"
echo "being already covered above."
