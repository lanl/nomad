SPHINXBUILD ?= sphinx-build
SPHINXOPTS  ?= -W --keep-going
SOURCEDIR   = .
BUILDDIR    = _build

.PHONY: html check clean livehtml

html:
	uv run --no-dev --group docs $(SPHINXBUILD) $(SPHINXOPTS) -b html $(SOURCEDIR) $(BUILDDIR)/html

clean:
	rm -rf $(BUILDDIR)

livehtml:
	uv run --no-dev --group docs sphinx-autobuild --port 8080 $(SOURCEDIR) $(BUILDDIR)/html
