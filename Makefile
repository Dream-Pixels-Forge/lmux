# =============================================================================
# lmux — Makefile
# Ties together build, test, development, packaging, and distribution targets.
# =============================================================================

SHELL       := /bin/bash
.DEFAULT_GOAL := help

# ── Variables ────────────────────────────────────────────────────────────────
VERSION     := $(shell node -p "require('./package.json').version" 2>/dev/null || echo "0.0.0")
PREFIX      ?= /usr/local
BINDIR      := $(PREFIX)/bin
LIBDIR      := $(PREFIX)/lib/lmux
DESTDIR     ?=
NODE        := node
PYTHON      := python3
CC          ?= gcc

# ── Phony declarations ───────────────────────────────────────────────────────
.PHONY: all build test test-unit test-integration test-fuzz clean \
        dev dev-cli lint format typecheck \
        deb appimage flatpak rpm all-packages install install-deb uninstall \
        dist release version man help

# =============================================================================
# Core build
# =============================================================================

all: build
	@echo "✓ Build complete"

build:
	$(NODE) scripts/build-core.mjs
	$(NODE) scripts/build-cli.mjs

# ── Tests ────────────────────────────────────────────────────────────────────

test: test-unit test-integration
	@echo "✓ All tests passed"

test-unit:
	$(NODE) scripts/test.mjs --unit

test-integration:
	$(PYTHON) tests/test_integration.py

test-fuzz:
	$(NODE) scripts/test.mjs --unit --fuzz

# ── Clean ────────────────────────────────────────────────────────────────────

clean:
	rm -rf build/ dist/
	@echo "✓ Cleaned"

# =============================================================================
# Development
# =============================================================================

dev: build
	$(NODE) scripts/dev.mjs

dev-cli: build
	$(NODE) scripts/run.mjs

lint:
	@echo "==> Linting JS scripts..."
	@command -v eslint  >/dev/null 2>&1 && eslint scripts/*.mjs || echo "  eslint not found, skipping"
	@echo "==> Linting Python..."
	@command -v flake8  >/dev/null 2>&1 && flake8 gui/ tests/ || echo "  flake8 not found, skipping"
	@command -v pylint  >/dev/null 2>&1 && pylint gui/ tests/ || echo "  pylint not found, skipping"

format:
	@echo "==> Formatting JS..."
	@command -v prettier >/dev/null 2>&1 && prettier --write scripts/*.mjs || echo "  prettier not found, skipping"
	@echo "==> Formatting Python..."
	@command -v black    >/dev/null 2>&1 && black gui/ tests/ || echo "  black not found, skipping"

typecheck:
	@echo "==> Type-checking Python..."
	@command -v mypy >/dev/null 2>&1 && mypy gui/ || echo "  mypy not found, skipping"

# =============================================================================
# Packaging
# =============================================================================

deb: build
	VERSION=$(VERSION) ./scripts/package-deb.sh

appimage: build
	VERSION=$(VERSION) ./scripts/package-appimage.sh

flatpak: build
	@command -v flatpak-builder >/dev/null 2>&1 || { echo "Error: flatpak-builder not found"; exit 1; }
	@echo "==> Building Flatpak for lmux v$(VERSION)"
	@# Flatpak manifest would live in packaging/io.lmux.lmux.yml
	@# For now, create the AppDir and hand off to flatpak-builder.
	flatpak-builder --force-clean dist/flatpak/build packaging/io.lmux.lmux.yml

rpm: build
	@command -v rpmbuild >/dev/null 2>&1 || { echo "Error: rpmbuild not found (install rpm-build)"; exit 1; }
	@echo "==> Building RPM for lmux v$(VERSION)"
	@mkdir -p dist/rpm/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
	rpmbuild -bb packaging/lmux.spec \
		--define "_topdir $(CURDIR)/dist/rpm" \
		--define "version $(VERSION)" \
		--define "prefix $(PREFIX)"

all-packages: deb appimage flatpak
	@echo "✓ All packages built"

# install-deb: clean one-command install. Copies the .deb into /tmp (a path the
# apt _apt sandbox user can traverse) before `apt install`, which avoids the
# "couldn't be accessed by user '_apt' / Permission denied" warning that occurs
# when installing a .deb straight out of a non-world-traversable home directory.
install-deb: deb
	@echo "==> Staging .deb in /tmp (sandbox-readable) and installing"
	@install -m 644 dist/deb/lmux_$(VERSION)_amd64.deb /tmp/lmux_$(VERSION)_amd64.deb
	@sudo apt install -y /tmp/lmux_$(VERSION)_amd64.deb; rc=$$?; rm -f /tmp/lmux_$(VERSION)_amd64.deb; exit $$rc

# ── Install / Uninstall ──────────────────────────────────────────────────────

install: build
	@echo "==> Installing lmux v$(VERSION) to $(DESTDIR)$(PREFIX)"
	install -d $(DESTDIR)$(BINDIR)
	install -m 755 build/lmux $(DESTDIR)$(BINDIR)/lmux
	install -d $(DESTDIR)$(LIBDIR)
	install -m 644 build/liblmux_core.a $(DESTDIR)$(LIBDIR)/liblmux_core.a
	@echo "==> Installing GUI..."
	install -d $(DESTDIR)$(LIBDIR)/gui
	cp -r gui/* $(DESTDIR)$(LIBDIR)/gui/
	find $(DESTDIR)$(LIBDIR)/gui -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ Installed to $(DESTDIR)$(PREFIX)"

uninstall:
	@echo "==> Removing lmux from $(DESTDIR)$(PREFIX)"
	rm -f  $(DESTDIR)$(BINDIR)/lmux
	rm -rf $(DESTDIR)$(LIBDIR)
	@echo "✓ Uninstalled"

# =============================================================================
# Distribution
# =============================================================================

dist: all-packages
	@mkdir -p dist/
	@echo "==> Collecting artifacts into dist/"
	@cp build/lmux dist/ 2>/dev/null || true
	@echo "✓ dist/ ready"

release: clean all test all-packages dist
	@echo "✓ Release v$(VERSION) complete"

version:
	@echo $(VERSION)

# =============================================================================
# Documentation
# =============================================================================

man:
	@echo "==> Generating man page from README..."
	@mkdir -p dist/man
	@# Use lowdown (or pandoc fallback) to convert README to roff
	@command -v lowdown >/dev/null 2>&1 && \
		lowdown -Tman README.md > dist/man/lmux.1 || \
		{ command -v pandoc >/dev/null 2>&1 && \
		pandoc -s -t man README.md -o dist/man/lmux.1 || \
		echo "  Neither lowdown nor pandoc found, skipping man page generation"; }
	@echo "✓ Man page: dist/man/lmux.1"

# =============================================================================
# Help
# =============================================================================

help:
	@echo "lmux v$(VERSION)"
	@echo ""
	@echo "Targets:"
	@echo "  all               Build C core + CLI"
	@echo "  build             Compile core library and CLI binary"
	@echo "  test              Run all tests (unit + integration)"
	@echo "  test-unit         Run C unit tests only"
	@echo "  test-integration  Run Python integration tests only"
	@echo "  test-fuzz         Run fuzz test only"
	@echo "  clean             Remove build/ and dist/"
	@echo ""
	@echo "Development:"
	@echo "  dev               Build + run GUI with file watcher"
	@echo "  dev-cli           Build + run CLI"
	@echo "  lint              Lint JS (eslint) and Python (flake8/pylint)"
	@echo "  format            Format JS (prettier) and Python (black)"
	@echo "  typecheck         Type-check Python (mypy)"
	@echo ""
	@echo "Packaging:"
	@echo "  deb               Build .deb package"
	@echo "  appimage          Build AppImage"
	@echo "  flatpak           Build Flatpak"
	@echo "  rpm               Build .rpm (requires rpmbuild)"
	@echo "  all-packages      Build deb + appimage + flatpak"
	@echo "  install           Install to $(PREFIX) (may need sudo)"
	@echo "  uninstall         Remove from $(PREFIX)"
	@echo ""
	@echo "Distribution:"
	@echo "  dist              Collect all artifacts into dist/"
	@echo "  release           Clean + build + test + package + dist"
	@echo "  version           Print current version"
	@echo ""
	@echo "Documentation:"
	@echo "  man               Generate man page from README"
	@echo ""
	@echo "Variables:"
	@echo "  VERSION=$(VERSION)"
	@echo "  PREFIX=$(PREFIX)"
	@echo "  DESTDIR=$(DESTDIR) (staging root for install)"
