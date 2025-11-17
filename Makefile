CHANNELS = $(addprefix -c ,$(shell tr '\n' ' ' <$(RECIPE_DIR)/channels)) -c local
DEMO     = src/iotaa/demo.py
METADEPS = $(RECIPE_DIR)/meta.yaml src/*/resources/info.json
METAJSON = $(RECIPE_DIR)/meta.json
TARGETS  = devshell env format lint meta package render test typecheck unittest

export RECIPE_DIR := $(shell cd ./recipe && pwd)

spec = $(call val,name)$(2)$(call val,version)$(2)$(call val,$(1))
val  = $(shell jq -r .$(1) $(METAJSON))

.PHONY: $(TARGETS)

all:
	$(error Valid targets are: $(TARGETS))

devshell:
	condev-shell || true

env: package
	conda create -y -n $(call spec,buildnum,-) $(CHANNELS) $(call spec,build,=)

format: $(DEMO)
	@./format

lint: $(DEMO)
	recipe/run_test.sh lint

meta: $(METAJSON)

package: meta $(DEMO)
	conda build $(CHANNELS) --error-overlinking --override-channels $(RECIPE_DIR)

render: $(DEMO) README.md

test: $(DEMO)
	recipe/run_test.sh

typecheck: $(DEMO)
	recipe/run_test.sh typecheck

unittest: $(DEMO)
	recipe/run_test.sh unittest

$(METAJSON): $(METADEPS)
	condev-meta

$(DEMO): m4/demo.m4 m4/include/*.py
	m4 -I m4/include $< >$@

README.md: m4/README.m4 m4/include/*.py $(DEMO)
	m4 -I m4/include $< >$@
