PYTHON ?= python3
UV_BIN ?= $(shell command -v uv)
LAUNCHD_LABEL := local.mwm
LAUNCHD_PLIST := $(HOME)/Library/LaunchAgents/$(LAUNCHD_LABEL).plist
LAUNCHD_DOMAIN := gui/$(shell id -u)
LOCAL_BIN := $(HOME)/.local/bin
MWM_BIN := $(LOCAL_BIN)/mwm
MWM_WORKDIR := $(HOME)

.PHONY: all lint fix test install uninstall

all: fix lint test

lint:
	ruff --config pyproject.toml check .
	basedpyright --project pyproject.toml --level error .

fix:
	ruff --config pyproject.toml check --extend-select I --fix-only --fix .
	ruff format .

test:
	$(PYTHON) -m doctest README.md $(wildcard *.py)

install:
	test -x "$(UV_BIN)"
	mkdir -p $(LOCAL_BIN)
	mkdir -p $(HOME)/Library/LaunchAgents
	cp mwm.py $(MWM_BIN)
	chmod +x $(MWM_BIN)
	sed -e 's|@UV_BIN@|$(UV_BIN)|g' -e 's|@MWM_BIN@|$(MWM_BIN)|g' -e 's|@MWM_WORKDIR@|$(MWM_WORKDIR)|g' $(LAUNCHD_LABEL).plist.in > $(LAUNCHD_PLIST)
	-launchctl bootout $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	launchctl bootstrap $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	launchctl kickstart -k $(LAUNCHD_DOMAIN)/$(LAUNCHD_LABEL)

uninstall:
	-launchctl bootout $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	rm -f $(LAUNCHD_PLIST)
	rm -f $(MWM_BIN)
