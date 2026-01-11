#!/bin/bash
# Release script for Linguistic Stratigraphy
# Usage: ./scripts/release.sh [version]
# Example: ./scripts/release.sh 0.1.0

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get version
if [ -z "$1" ]; then
    VERSION=$(cat VERSION 2>/dev/null || echo "0.1.0")
else
    VERSION=$1
fi

echo -e "${GREEN}=== Linguistic Stratigraphy Release v${VERSION} ===${NC}"

# Verify we're on main/master branch
BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$BRANCH" != "main" && "$BRANCH" != "master" ]]; then
    echo -e "${YELLOW}Warning: Not on main/master branch (current: $BRANCH)${NC}"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Verify working directory is clean
if [[ -n $(git status --porcelain) ]]; then
    echo -e "${RED}Error: Working directory is not clean${NC}"
    echo "Please commit or stash your changes before releasing."
    exit 1
fi

# Run tests
echo -e "${GREEN}Running tests...${NC}"
python -m pytest tests/unit -q --tb=no || {
    echo -e "${RED}Tests failed. Aborting release.${NC}"
    exit 1
}

# Run linting
echo -e "${GREEN}Running linting...${NC}"
python -m ruff check src/ --quiet || {
    echo -e "${YELLOW}Linting warnings found. Continuing...${NC}"
}

# Build packages
echo -e "${GREEN}Building packages...${NC}"
rm -rf dist/ build/ *.egg-info

# Build wheel and sdist
python -m build

# Create release archive
echo -e "${GREEN}Creating release archive...${NC}"
ARCHIVE_DIR="releases"
mkdir -p "$ARCHIVE_DIR"

ARCHIVE_NAME="linguistic-stratigraphy-${VERSION}"
ARCHIVE_PATH="${ARCHIVE_DIR}/${ARCHIVE_NAME}.tar.gz"

# Create archive with source and built packages
tar -czf "$ARCHIVE_PATH" \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='.mypy_cache' \
    --exclude='.ruff_cache' \
    --exclude='venv' \
    --exclude='.venv' \
    --exclude='node_modules' \
    --exclude='.env' \
    --exclude='*.log' \
    --transform "s,^,${ARCHIVE_NAME}/," \
    src/ \
    tests/ \
    docs/ \
    scripts/ \
    dist/ \
    pyproject.toml \
    requirements.txt \
    requirements-dev.txt \
    README.md \
    LICENSE \
    CHANGELOG.md \
    VERSION \
    Makefile \
    docker-compose.yml \
    Dockerfile

echo -e "${GREEN}Archive created: ${ARCHIVE_PATH}${NC}"

# Generate checksums
echo -e "${GREEN}Generating checksums...${NC}"
cd "$ARCHIVE_DIR"
sha256sum "${ARCHIVE_NAME}.tar.gz" > "${ARCHIVE_NAME}.tar.gz.sha256"
cd ..

# Also checksum the wheel and sdist
cd dist
for f in *.whl *.tar.gz; do
    sha256sum "$f" > "$f.sha256"
done
cd ..

# Create git tag
echo -e "${GREEN}Creating git tag v${VERSION}...${NC}"
if git rev-parse "v${VERSION}" >/dev/null 2>&1; then
    echo -e "${YELLOW}Tag v${VERSION} already exists${NC}"
else
    git tag -a "v${VERSION}" -m "Release version ${VERSION}

Changes in this release:
- See CHANGELOG.md for full details

Assets:
- Source archive: ${ARCHIVE_NAME}.tar.gz
- Python wheel: dist/linguistic_stratigraphy-${VERSION}-py3-none-any.whl
- Source distribution: dist/linguistic_stratigraphy-${VERSION}.tar.gz
"
    echo -e "${GREEN}Tag v${VERSION} created${NC}"
fi

# Summary
echo ""
echo -e "${GREEN}=== Release Summary ===${NC}"
echo "Version: ${VERSION}"
echo "Tag: v${VERSION}"
echo ""
echo "Artifacts:"
ls -lh dist/
echo ""
ls -lh releases/
echo ""
echo -e "${GREEN}Next steps:${NC}"
echo "1. Push tag: git push origin v${VERSION}"
echo "2. Upload to PyPI: twine upload dist/*"
echo "3. Create GitHub release with artifacts from releases/"
echo ""
echo -e "${GREEN}Release v${VERSION} prepared successfully!${NC}"
