SPHINXBUILD ?= sphinx-build
SPHINXOPTS  ?= -W --keep-going
SOURCEDIR   = .
BUILDDIR    = _build

.PHONY: html latexpdf check clean livehtml

html:
	uv run --no-dev --group docs $(SPHINXBUILD) $(SPHINXOPTS) -b html $(SOURCEDIR) $(BUILDDIR)/html

latexpdf:
	uv run --no-dev --group docs $(SPHINXBUILD) $(SPHINXOPTS) -b latex $(SOURCEDIR) $(BUILDDIR)/latex
	cd $(BUILDDIR)/latex && tectonic -X compile --keep-logs nomad-scifm.tex

clean:
	rm -rf $(BUILDDIR)

livehtml:
	uv run --no-dev --group docs sphinx-autobuild --port 8080 $(SOURCEDIR) $(BUILDDIR)/html
