LAUNCHD_LABEL := mwm
LAUNCHD_PLIST := $(HOME)/Library/LaunchAgents/$(LAUNCHD_LABEL).plist
LAUNCHD_DOMAIN := gui/$(shell id -u)
LOCAL_BIN := $(HOME)/.local/bin
MWM_BIN := $(LOCAL_BIN)/mwm.py

.PHONY: all lint fix test install uninstall install_bin install_plist
.SILENT:

all: fix lint test

lint:
	uv run ruff --quiet --config pyproject.toml check .
	uv run basedpyright --project pyproject.toml --level error .

fix:
	uv run ruff --quiet --config pyproject.toml check --extend-select I --fix-only --fix .
	uv run ruff format --quiet .

test:
	uv run python -m doctest README.md $(wildcard *.py)


install: install_bin install_plist

install_plist:
	mkdir -p $(HOME)/Library/LaunchAgents
	$(MWM_BIN) launchd-plist > $(LAUNCHD_PLIST)
	-launchctl bootout $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	launchctl bootstrap $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	launchctl kickstart -k $(LAUNCHD_DOMAIN)/$(LAUNCHD_LABEL)

install_bin:
	mkdir -p $(LOCAL_BIN)
	cp mwm.py $(LOCAL_BIN)/mwm.py

uninstall:
	-launchctl bootout $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	rm -f $(LAUNCHD_PLIST) $(LOCAL_BIN)/mwm.py
