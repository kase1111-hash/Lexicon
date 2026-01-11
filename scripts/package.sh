#!/bin/bash
# Build distributable packages for Linguistic Stratigraphy
# Usage: ./scripts/package.sh [wheel|sdist|docker|all]

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

VERSION=$(python -c "from src import __version__; print(__version__)" 2>/dev/null || echo "0.1.0")
PROJECT_NAME="linguistic-stratigraphy"
DIST_DIR="dist"
BUILD_DIR="build"

print_header() {
    echo ""
    echo -e "${BLUE}=====================================${NC}"
    echo -e "${BLUE}  Linguistic Stratigraphy Packager${NC}"
    echo -e "${BLUE}  Version: ${VERSION}${NC}"
    echo -e "${BLUE}=====================================${NC}"
    echo ""
}

clean() {
    echo -e "${YELLOW}Cleaning previous builds...${NC}"
    rm -rf "$BUILD_DIR" "$DIST_DIR" .eggs *.egg-info
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo -e "${GREEN}Clean complete${NC}"
}

build_wheel() {
    echo -e "${YELLOW}Building wheel package...${NC}"
    pip install build --quiet
    python -m build --wheel
    echo -e "${GREEN}Wheel built: ${DIST_DIR}/${PROJECT_NAME}-${VERSION}-py3-none-any.whl${NC}"
}

build_sdist() {
    echo -e "${YELLOW}Building source distribution...${NC}"
    pip install build --quiet
    python -m build --sdist
    echo -e "${GREEN}Source dist built: ${DIST_DIR}/${PROJECT_NAME}-${VERSION}.tar.gz${NC}"
}

build_docker() {
    echo -e "${YELLOW}Building Docker image...${NC}"

    # Build production image
    docker build -t "${PROJECT_NAME}:${VERSION}" -t "${PROJECT_NAME}:latest" .

    echo -e "${GREEN}Docker images built:${NC}"
    echo "  - ${PROJECT_NAME}:${VERSION}"
    echo "  - ${PROJECT_NAME}:latest"
}

build_zip() {
    echo -e "${YELLOW}Building zip archive...${NC}"

    ZIP_NAME="${DIST_DIR}/${PROJECT_NAME}-${VERSION}.zip"
    mkdir -p "$DIST_DIR"

    # Create zip with essential files
    zip -r "$ZIP_NAME" \
        src/ \
        requirements.txt \
        requirements-dev.txt \
        pyproject.toml \
        README.md \
        LICENSE \
        Makefile \
        Dockerfile \
        docker-compose.yml \
        config/ \
        scripts/ \
        -x "*.pyc" -x "*/__pycache__/*" -x "*.egg-info/*"

    echo -e "${GREEN}Zip archive built: ${ZIP_NAME}${NC}"
}

verify_packages() {
    echo -e "${YELLOW}Verifying packages...${NC}"

    # Check wheel
    if [ -f "${DIST_DIR}/${PROJECT_NAME//-/_}-${VERSION}-py3-none-any.whl" ]; then
        echo -e "${GREEN}✓ Wheel package exists${NC}"
        pip install twine --quiet
        twine check "${DIST_DIR}/"*.whl 2>/dev/null && echo -e "${GREEN}✓ Wheel passes twine check${NC}" || true
    fi

    # Check sdist
    if [ -f "${DIST_DIR}/${PROJECT_NAME}-${VERSION}.tar.gz" ]; then
        echo -e "${GREEN}✓ Source distribution exists${NC}"
    fi

    # Check Docker image
    if docker image inspect "${PROJECT_NAME}:${VERSION}" >/dev/null 2>&1; then
        echo -e "${GREEN}✓ Docker image exists${NC}"
        SIZE=$(docker image inspect "${PROJECT_NAME}:${VERSION}" --format='{{.Size}}' | numfmt --to=iec 2>/dev/null || echo "unknown")
        echo "  Image size: ${SIZE}"
    fi

    echo ""
    echo -e "${GREEN}Package verification complete${NC}"
}

show_help() {
    print_header
    echo "Usage: ./scripts/package.sh [command]"
    echo ""
    echo "Commands:"
    echo "  wheel     Build Python wheel package"
    echo "  sdist     Build source distribution"
    echo "  docker    Build Docker image"
    echo "  zip       Build zip archive"
    echo "  python    Build wheel and sdist"
    echo "  all       Build all packages"
    echo "  clean     Remove build artifacts"
    echo "  verify    Verify built packages"
    echo "  help      Show this help"
    echo ""
    echo "Examples:"
    echo "  ./scripts/package.sh wheel    # Build just the wheel"
    echo "  ./scripts/package.sh all      # Build everything"
    echo ""
}

# Main
print_header

case "${1:-help}" in
    wheel)
        build_wheel
        ;;
    sdist)
        build_sdist
        ;;
    docker)
        build_docker
        ;;
    zip)
        build_zip
        ;;
    python)
        clean
        build_wheel
        build_sdist
        verify_packages
        ;;
    all)
        clean
        build_wheel
        build_sdist
        build_zip
        build_docker
        verify_packages
        ;;
    clean)
        clean
        ;;
    verify)
        verify_packages
        ;;
    help|*)
        show_help
        ;;
esac

echo ""
echo -e "${GREEN}Done!${NC}"
